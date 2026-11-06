"""Expense categories: how each kind of expense is treated for IRPF and IVA."""
from __future__ import annotations

import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ... import calc
from ...i18n import t
from ...repositories import CategoryRepository
from ..forms import parse_cap
from ..infra import svc

bp = Blueprint("categories", __name__)


def _modes():
    irpf, vat = request.form.get("irpf_mode", "full"), request.form.get("vat_mode", "full")
    return (irpf, vat) if irpf in calc.IRPF_MODES and vat in calc.VAT_MODES else None


@bp.route("/categories", methods=["GET", "POST"])
def page():
    s = svc()
    if request.method == "POST":
        name, modes = request.form.get("name", "").strip(), _modes()
        try:
            cap = parse_cap(request.form.get("annual_cap"))
        except ValueError:
            flash(t("err.cap_amount"), "error")
            return redirect(url_for("categories.page"))
        if not name or not modes:
            flash(t("err.cat_fields"), "error")
        else:
            try:
                s.categories.add(name, *modes, request.form.get("notes", "").strip(), cap)
                flash(t("flash.cat_added"), "ok")
            except sqlite3.IntegrityError:
                flash(t("err.cat_exists"), "error")
        return redirect(url_for("categories.page"))
    return render_template("categories.html", cats=s.categories.all(), usage=s.categories.usage())


@bp.post("/categories/<int:category_id>")
def update(category_id):
    s = svc()
    row = s.categories.get(category_id)
    if row is None:
        abort(404)
    modes = _modes()
    try:
        cap = parse_cap(request.form.get("annual_cap"))
    except ValueError:
        flash(t("err.cap_amount"), "error")
        return redirect(url_for("categories.page"))
    if not modes:
        return redirect(url_for("categories.page"))
    # Untouched translated defaults keep following the interface language: only a changed value is stored.
    shown_name = CategoryRepository.label(row["code"], row["name"])
    submitted = request.form.get("name", "").strip() or shown_name
    name = row["name"] if submitted == shown_name else submitted
    note = request.form.get("notes", "").strip()
    if note == CategoryRepository.note(row["code"], ""):
        note = ""
    try:
        s.categories.update(category_id, name, *modes, note, cap)
        flash(t("flash.cat_updated"), "ok")
    except sqlite3.IntegrityError:
        flash(t("err.cat_exists"), "error")
    return redirect(url_for("categories.page"))


@bp.post("/categories/<int:category_id>/delete")
def delete(category_id):
    if not svc().categories.delete(category_id):
        flash(t("err.cat_in_use"), "error")
    return redirect(url_for("categories.page"))
