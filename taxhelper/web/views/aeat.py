"""Generate the AEAT flat file and check it with AEAT's validation service (nothing is filed)."""
from __future__ import annotations

import io
from datetime import date

from flask import Blueprint, abort, flash, redirect, request, send_file, url_for

from ...aeat import service as aeat_service
from ...i18n import t
from ..helpers import current_year, quarter_arg
from ..infra import svc

bp = Blueprint("aeat", __name__)

REPORT_ENDPOINT = {"303": "reports.vat303", "130": "reports.irpf130"}


@bp.post("/aeat/check/<model>")
def check(model):
    """Send the generated file to the AEAT test validator and store its verdict."""
    if model not in aeat_service.MODELS:
        abort(404)
    s = svc()
    year = request.form.get("year", type=int) or date.today().year
    q = quarter_arg(request.form)
    try:
        built = aeat_service.build_file(s.settings.load(), s.reports, model, year, q)
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        row = aeat_service.run_check(s.checks, built)
        flash(t("flash.aeat_ok") if row["ok"] else t("flash.aeat_errors"), "ok" if row["ok"] else "error")
    return redirect(url_for(REPORT_ENDPOINT[model], year=year, q=q))


@bp.get("/aeat/file/<model>")
def download(model):
    """Download the flat file in AEAT's import naming (NIF-year-period.model), ISO-8859-1."""
    if model not in aeat_service.MODELS:
        abort(404)
    s = svc()
    year, q = current_year(), quarter_arg(request.args)
    try:
        built = aeat_service.build_file(s.settings.load(), s.reports, model, year, q)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for(REPORT_ENDPOINT[model], year=year, q=q))
    return send_file(io.BytesIO(built.text.encode("latin-1", "replace")), mimetype="text/plain",
                     as_attachment=True, download_name=built.filename)
