"""CSV import of entries: upload, preview, confirm."""
from __future__ import annotations

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from ...i18n import t
from ..import_csv import MAX_BYTES, build_plan, decode, template_csv
from ..infra import svc

bp = Blueprint("importing", __name__)


def _posted_text() -> str:
    """The CSV text of the form: the uploaded file if any, else the pasted text."""
    upload = request.files.get("file")
    if upload and upload.filename:
        return decode(upload.stream.read(MAX_BYTES + 1))
    return request.form.get("csv", "")


@bp.get("/import")
def page():
    return render_template("import.html", plan=None, csv_text="")


@bp.get("/import/template.csv")
def template():
    body = "﻿" + template_csv()
    return Response(body, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=taxhelper-import-template.csv"})


@bp.post("/import/preview")
def preview():
    text = _posted_text()
    plan = build_plan(text, svc())
    return render_template("import.html", plan=plan, csv_text=text if plan.rows else "")


@bp.post("/import/commit")
def commit():
    """Re-validate the file and store the rows ticked in the preview (all or none)."""
    s = svc()
    plan = build_plan(request.form.get("csv", ""), s)
    chosen = set(request.form.getlist("row", type=int))
    rows = [r for r in plan.rows if r.index in chosen and r.ok]
    if not rows:
        flash(t("import.err_none"), "error")
        return redirect(url_for("importing.page"))
    s.entries.insert_many([r.values for r in rows])
    flash(t("import.done", n=len(rows), skipped=len(plan.rows) - len(rows)), "ok")
    return redirect(url_for("entries.index", year=max(r.values["date"][:4] for r in rows)))
