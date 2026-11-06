"""The AEAT books (libros registro) as an Excel file for Renta WEB, the modelo 130 form and Pre303."""
from __future__ import annotations

import io

from flask import Blueprint, redirect, render_template, request, send_file, url_for

from ...aeat import client as aeat_client
from ...aeat import libros
from ...i18n import UserError
from ..helpers import current_year, quarter_arg
from ..infra import svc

bp = Blueprint("libros", __name__)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _build(year: int, upto: int):
    s = svc()
    return libros.build_books(s.settings.load(), s.entries.year_rows(year), year, upto)


def _page(check=None):
    year, upto = current_year(), quarter_arg(request.values, default=4)
    built, error = None, ""
    try:
        built = _build(year, upto)
    except UserError as exc:
        error = str(exc)
    return render_template("libros.html", built=built, error=error, upto=upto, check=check)


@bp.get("/libros")
def index():
    return _page()


@bp.get("/libros/file")
def download():
    year, upto = current_year(), quarter_arg(request.args, default=4)
    try:
        built = _build(year, upto)
    except UserError:
        return redirect(url_for("libros.index", year=year, q=upto))  # the page shows what is missing
    return send_file(io.BytesIO(built.content), mimetype=XLSX_MIME, as_attachment=True, download_name=built.filename)


@bp.post("/libros/check")
def check():
    """Send the generated workbook to AEAT's books validator and show its verdict (nothing is filed)."""
    year, upto = current_year(), quarter_arg(request.values, default=4)
    try:
        built = _build(year, upto)
    except UserError:
        return _page()
    return _page(check=aeat_client.validate_libros(built.content, year))
