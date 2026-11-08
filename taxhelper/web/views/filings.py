"""The filings tracker."""
from __future__ import annotations

import sqlite3
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ...dates import parse_date
from ...i18n import UserError, t
from ...settings import parse_year
from ..forms import parse_filing
from ..helpers import safe_next
from ..infra import svc
from ..uploads import delete_document, save_upload

bp = Blueprint("filings", __name__)


@bp.get("/filings")
def index():
    s = svc()
    this_year = date.today().year
    s.calendar.ensure(set(s.entries.years_in_use()) | {this_year - 1, this_year})
    rows = s.calendar.rows()

    # what the app itself calculates for each quarter, shown next to the result the user filed
    reports = {}
    for year in {r["year"] for r in rows}:
        reports[year] = {"303": s.reports.vat_year(year), "130": s.reports.irpf_year(year)}
    for r in rows:
        r["calc_result"] = None
        if r["model"] in ("303", "130") and r["quarter"]:
            rep = reports[r["year"]][r["model"]][r["quarter"] - 1]
            r["calc_result"] = rep.c71 if r["model"] == "303" else rep.c19

    by_year = {}
    for r in rows:
        by_year.setdefault(r["year"], []).append(r)
    groups = sorted(by_year.items(), reverse=True)
    for _year, items in groups:
        items.sort(key=lambda x: (x["due_date"], x["model"]))
    return render_template("filings.html", groups=groups)


@bp.post("/filings/<int:filing_id>/mark")
def mark(filing_id):
    if svc().filings.get(filing_id) is None:
        abort(404)
    if not svc().calendar.mark(filing_id, request.form.get("action", "")):
        abort(400)
    return redirect(safe_next(request.form.get("next", ""), url_for("filings.index")))


@bp.route("/filings/<int:filing_id>/edit", methods=["GET", "POST"])
def edit(filing_id):
    s = svc()
    row = s.filings.get(filing_id)
    if row is None:
        abort(404)
    if request.method == "POST":
        result = parse_filing(request.form, row)
        errors = list(result.errors)
        document = row["document"]
        if not errors:
            try:
                document = save_upload(request.files.get("document")) or document
            except UserError as exc:
                errors.append(str(exc))
        if errors:
            for message in errors:
                flash(message, "error")
            return render_template("filing_form.html", f=dict(row) | request.form.to_dict())
        s.filings.update_details(filing_id, document=document, **result.values)
        s.filings.commit()
        if document != row["document"]:
            delete_document(row["document"])  # only after the row no longer points at it
        flash(t("flash.saved"), "ok")
        return redirect(url_for("filings.index"))
    f = dict(row)
    f["result"] = f"{row['result_cents'] / 100:.2f}" if row["result_cents"] is not None else ""
    return render_template("filing_form.html", f=f)


@bp.post("/filings/new")
def new():
    """A hand-made filing (e.g. 349, 115, 036) with its own due date."""
    try:
        year = parse_year(request.form.get("fyear", ""))
        due = parse_date(request.form.get("due_date", ""))
        model = request.form.get("model", "").strip()[:10]
        period = request.form.get("period", "").strip()[:10] or "ANUAL"
        if not model or year is None:
            raise ValueError("model/year")
    except ValueError:
        flash(t("err.filing_fields"), "error")
        return redirect(url_for("filings.index"))
    try:
        svc().calendar.add_custom(year, model, period, due)
        flash(t("flash.filing_added"), "ok")
    except sqlite3.IntegrityError:
        flash(t("err.filing_exists"), "error")
    return redirect(url_for("filings.index"))


@bp.post("/filings/bulk-filed")
def bulk_filed():
    """Mark every still-pending filing due on or before a date as filed (history catch-up)."""
    try:
        upto = parse_date(request.form.get("upto", ""))
    except ValueError:
        flash(t("err.date_required"), "error")
        return redirect(url_for("filings.index"))
    flash(t("flash.bulk_filed", n=svc().calendar.mark_filed_until(upto)), "ok")
    return redirect(url_for("filings.index"))


@bp.post("/filings/restore")
def restore():
    """Recreate the standard filings that were deleted earlier."""
    svc().calendar.restore_deleted()
    flash(t("flash.filings_restored"), "ok")
    return redirect(url_for("filings.index"))


@bp.post("/filings/<int:filing_id>/delete")
def delete(filing_id):
    s = svc()
    row = s.filings.get(filing_id)
    if row:
        s.calendar.delete(row)
        delete_document(row["document"])
    return redirect(url_for("filings.index"))
