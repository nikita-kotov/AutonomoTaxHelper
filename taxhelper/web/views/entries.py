"""Income and expense entries: list, create, edit, copy, delete, live preview, exchange rates, CSV export."""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from datetime import date
from decimal import Decimal

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file,
                   url_for)

from ...codes import DeductionMode, Kind, Regime
from ... import calc, fx, i18n
from ...fx import FxError
from ...i18n import UserError, t
from ..forms import parse_entry, parse_percent
from ..import_csv import csv_safe
from ..helpers import current_year, quarter_arg
from ..infra import svc
from ..uploads import delete_document, save_upload

bp = Blueprint("entries", __name__)

VAT_RATE_CHOICES = ("21", "10", "4", "0")
JS_TEXT_KEYS = ("form.help_eu_reverse", "form.help_non_eu_reverse", "form.help_no_vat", "form.help_eu_b2b",
                "form.help_non_eu", "form.pv_expense", "form.pv_expense_reverse",
                "form.pv_income", "form.fx_found", "form.fx_loading")


# ----------------------------------------------------------------- list and export

def ledger_rows(year, quarter=None, kind=None, text=None):
    """The year's entries for the list/CSV, newest first, optionally filtered.

    Caps on category deductions depend on the whole year in order, so deductions are computed on
    the complete chronological list first and only then filtered.
    """
    s = svc()
    tax = s.settings.load().tax()
    sql_rows = s.entries.year_rows(year)
    entries = [s.entries.to_entry(r) for r in sql_rows]
    needle = (text or "").casefold()
    rows = []
    for r, e, irpf_ded in zip(sql_rows, entries, calc.irpf_deductions(entries, tax)):
        if kind in tuple(Kind) and r["kind"] != kind:
            continue
        if quarter and calc.quarter_of(e.date) != quarter:
            continue
        if needle and needle not in " ".join((r["counterparty"], r["description"], r["number"])).casefold():
            continue
        rows.append({"r": r, "e": e, "vat_ded": calc.vat_deductible(e, tax), "irpf_ded": irpf_ded,
                     "cat": e.category, "total": r["base_cents"] + r["vat_cents"] - r["withholding_cents"]})
    rows.reverse()  # newest first
    return rows


@bp.get("/entries")
def index():
    year = current_year()
    quarter = quarter_arg(request.args, default=None)
    kind = request.args.get("kind") or None
    text = (request.args.get("text") or "").strip() or None
    rows = ledger_rows(year, quarter, kind, text)
    totals = {
        "income": sum(x["r"]["base_cents"] for x in rows if x["r"]["kind"] == Kind.INCOME),
        "expense": sum(x["r"]["base_cents"] for x in rows if x["r"]["kind"] == Kind.EXPENSE),
        "irpf_ded": sum(x["irpf_ded"] for x in rows),
        "vat_ded": sum(x["vat_ded"] for x in rows),
    }
    return render_template("entries.html", rows=rows, totals=totals, quarter=quarter, kind=kind, text=text or "")


@bp.get("/export/entries.csv")
def export_csv():
    year = current_year()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["fecha", "tipo", "numero", "contraparte", "pais", "nif", "descripcion", "categoria",
                "regimen_iva", "base", "tipo_iva", "cuota_iva", "retencion", "iva_deducible", "gasto_irpf_deducible",
                "moneda", "importe_divisa", "tipo_cambio", "fecha_cambio", "fuente_cambio"])

    def money(cents):
        return f"{cents / 100:.2f}".replace(".", ",")

    for x in reversed(ledger_rows(year)):
        r = x["r"]
        foreign = r["orig_amount_cents"] is not None
        w.writerow([r["date"], r["kind"], csv_safe(r["number"]), csv_safe(r["counterparty"]), r["country"],
                    csv_safe(r["vat_id"]), csv_safe(r["description"]), csv_safe(x["cat"]), r["regime"], money(r["base_cents"]), r["vat_rate"],
                    money(r["vat_cents"]), money(r["withholding_cents"]), money(x["vat_ded"]), money(x["irpf_ded"]),
                    r["currency"], money(r["orig_amount_cents"]) if foreign else "", r["fx_rate"],
                    r["fx_rate_date"], r["fx_source"]])
    data = io.BytesIO(("﻿" + buf.getvalue()).encode("utf-8"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name=f"operaciones_{year}.csv")


# ----------------------------------------------------------------- create / edit

def form_context(kind):
    """Defaults and texts shared by the new/edit pages."""
    s = svc()
    settings = s.settings.load()
    default_rate = calc.fmt_percent(settings.default_vat_rate)
    rates = list(VAT_RATE_CHOICES)
    if default_rate not in rates:
        rates.insert(0, default_rate)
    regime = settings.default_regime(kind)
    # clients (income) or suppliers (expense) by lower-cased name: the form fills fields from the one typed in
    contacts = {c["name"].lower(): {"name": c["name"], "country": c["country"], "vat_id": c["vat_id"],
                                    "regime": c["regime"], "category_id": c["category_id"] or "",
                                    "vat_rate": c["vat_rate"], "withholding_rate": c["withholding_rate"],
                                    "description": c["description"]}
                for c in s.contacts.all(kind)}
    return {
        "contacts": contacts, "rateless_regimes": list(calc.RATELESS_REGIMES),
        "cats": s.categories.all(), "vat_rates": rates, "fx_auto": settings.auto_fx,
        "js_texts": {k: t(k) for k in JS_TEXT_KEYS},
        "defaults": {"date": date.today().isoformat(), "country": "ES", "currency": "EUR", "regime": regime,
                     "vat_rate": default_rate,
                     "withholding_rate": calc.fmt_percent(settings.default_withholding_rate) if kind == Kind.INCOME else "0"}}


def save_posted_entry(kind, row=None):
    """Validate and store the posted form: insert when `row` is None, otherwise update that row.

    Returns the redirect on success, or None after flashing the validation errors.
    """
    s = svc()
    result = parse_entry(request.form, kind)
    if not result.errors and kind == Kind.EXPENSE and s.categories.get(result.values["category_id"]) is None:
        result.errors.append(t("err.category"))  # e.g. the category was deleted in another tab
    new_doc = ""
    if not result.errors:
        try:
            new_doc = save_upload(request.files.get("document"))
        except UserError as exc:
            result.errors.append(str(exc))
    if result.errors:
        for message in result.errors:
            flash(message, "error")
        return None

    old_doc = row["document"] if row is not None else ""
    doc = new_doc or ("" if request.form.get("remove_document") else old_doc)
    try:
        if row is None:
            s.entries.insert(result.values, doc)
            if request.form.get("save_contact"):
                s.contacts.remember(result.values)
            message = "flash.entry_added"
        else:
            s.entries.update(row["id"], result.values, doc)
            message = "flash.saved"
    except sqlite3.IntegrityError:
        delete_document(new_doc)  # the upload belongs to a save that did not happen
        flash(t("err.category"), "error")
        return None
    if old_doc and doc != old_doc:
        delete_document(old_doc)  # only after the row no longer points at it
    for warning in result.warnings:
        flash(warning, "warn")
    flash(t(message), "ok")
    return redirect(url_for("entries.index", year=result.values["date"][:4]))


@bp.route("/entries/new/<kind>", methods=["GET", "POST"])
def new(kind):
    if kind not in tuple(Kind):
        abort(404)
    ctx = form_context(kind)
    if request.method == "POST":
        done = save_posted_entry(kind)
        if done:
            return done
        return render_template("entry_form.html", e=request.form, kind=kind, entry_id=None, doc="", **ctx)
    return render_template("entry_form.html", e=ctx["defaults"], kind=kind, entry_id=None, doc="", **ctx)


@bp.route("/entries/<int:entry_id>/edit", methods=["GET", "POST"])
def edit(entry_id):
    row = svc().entries.get(entry_id)
    if row is None:
        abort(404)
    ctx = form_context(row["kind"])
    if request.method == "POST":
        done = save_posted_entry(row["kind"], row)
        if done:
            return done
        return render_template("entry_form.html", e=request.form, kind=row["kind"], entry_id=entry_id,
                               doc=row["document"], **ctx)
    e = dict(row)
    e["base"] = f"{row['base_cents'] / 100:.2f}"
    # an explicit 0.00 of a domestic invoice is a value, not "empty": show it so saving keeps it
    e["vat_amount"] = f"{row['vat_cents'] / 100:.2f}" if (row["vat_cents"] or row["regime"] == Regime.DOMESTIC) else ""
    e["orig_amount"] = f"{row['orig_amount_cents'] / 100:.2f}" if row["orig_amount_cents"] is not None else ""
    return render_template("entry_form.html", e=e, kind=row["kind"], entry_id=entry_id, doc=row["document"], **ctx)


@bp.post("/entries/<int:entry_id>/clone")
def clone(entry_id):
    """Copy an entry dated today (handy for monthly subscriptions)."""
    s = svc()
    if s.entries.get(entry_id) is None:
        abort(404)
    new_id = s.entries.copy_dated(entry_id, date.today())
    flash(t("flash.entry_copied"), "ok")
    return redirect(url_for("entries.edit", entry_id=new_id))


@bp.post("/entries/<int:entry_id>/delete")
def delete(entry_id):
    s = svc()
    row = s.entries.get(entry_id)
    if row is None:
        abort(404)
    s.entries.delete(entry_id)
    delete_document(row["document"])  # only after the row no longer points at it
    flash(t("flash.entry_deleted"), "ok")
    return redirect(url_for("entries.index", year=row["date"][:4]))


# ----------------------------------------------------------------- JSON helpers for the form

@bp.post("/api/preview")
def preview():
    """Live preview for the entry form, using the same calculation code as the reports."""
    s = svc()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False})
    # whatever the client sent, work on strings; the two flags say whether the amounts were typed by hand
    vat_manual, base_manual = bool(payload.get("vat_manual")), bool(payload.get("base_manual"))
    data = {k: "" if v is None else str(v) for k, v in payload.items()}
    kind, regime = data.get("kind", "expense"), data.get("regime", "domestic")
    if kind not in tuple(Kind) or regime not in (calc.INCOME_REGIMES if kind == Kind.INCOME else calc.EXPENSE_REGIMES):
        return jsonify({"ok": False})
    currency = (data.get("currency") or fx.EUR).strip().upper()

    base, base_text = None, None
    if currency != fx.EUR and not base_manual:  # derive the euro base from amount and rate
        try:
            base = fx.convert_to_eur_cents(calc.parse_money(data.get("orig_amount")), fx.parse_rate(data.get("fx_rate")))
            base_text = f"{base / 100:.2f}"
        except ValueError:
            base = None
    if base is None:
        try:
            base = calc.parse_money(data.get("base"))
        except ValueError:
            return jsonify({"ok": False})
    try:
        rate = parse_percent(data.get("vat_rate"), "0")
        withholding_rate = parse_percent(data.get("withholding_rate"), "0")
    except UserError:
        return jsonify({"ok": False})
    if regime in calc.RATELESS_REGIMES:
        rate = Decimal(0)
    entered = None
    if data.get("vat_amount", "").strip() and vat_manual:
        try:
            entered = calc.parse_money(data.get("vat_amount"))
        except ValueError:
            entered = None
    vat = calc.compute_vat(regime, base, rate, entered)
    withholding = calc.round_cents(Decimal(base) * withholding_rate / 100)

    irpf_mode, vat_mode = DeductionMode.FULL, DeductionMode.FULL
    if kind == Kind.EXPENSE and data.get("category_id", "").isdigit():
        category = s.categories.get(int(data["category_id"]))
        if category:
            irpf_mode, vat_mode = category["irpf_mode"], category["vat_mode"]
    try:
        on = date.fromisoformat(data.get("date") or date.today().isoformat())
    except ValueError:
        on = date.today()
    entry = calc.Entry(kind=kind, date=on, regime=regime, base_cents=base, vat_rate=rate, vat_cents=vat,
                       withholding_cents=withholding, irpf_mode=irpf_mode, vat_mode=vat_mode)
    tax = s.settings.load().tax()
    money = i18n.format_money
    out = {"ok": True, "vat_cents": vat, "vat": money(vat), "withholding": money(withholding),
           "quarter": calc.quarter_of(on), "reverse": regime in calc.REVERSE_REGIMES, "base_text": base_text}
    if kind == Kind.EXPENSE:
        out.update(vat_ded=money(calc.vat_deductible(entry, tax)), irpf_ded=money(calc.irpf_deductible(entry, tax)),
                   total=money(base + (0 if regime in calc.REVERSE_REGIMES else vat) - withholding))
        if irpf_mode.startswith("home") and tax.affected_share == 0:
            out["warn"] = t("form.pv_home_unset")
    else:
        out["total"] = money(base + vat - withholding)
    return jsonify(out)


@bp.get("/api/fx")
def fx_rate():
    """The ECB reference rate of a currency on a date (cached after the first lookup)."""
    currency = request.args.get("currency", "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        return jsonify({"ok": False, "reason": t("err.currency")})
    try:
        on = date.fromisoformat(request.args.get("date", ""))
    except ValueError:
        return jsonify({"ok": False, "reason": t("err.date")})
    try:
        rate = svc().fx.lookup(currency, on)
    except FxError as exc:
        return jsonify({"ok": False, "reason": str(exc)})
    return jsonify({"ok": True, "rate": format(rate.per_eur.normalize(), "f"), "rate_date": rate.rate_date.isoformat(),
                    "source": rate.source})
