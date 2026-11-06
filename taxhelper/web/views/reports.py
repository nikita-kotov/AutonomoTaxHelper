"""The 303, 130 and Renta report pages."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, render_template, request

from ... import calc
from ..aeat_card import aeat_card
from ..helpers import current_year, quarter_arg
from ..infra import svc

bp = Blueprint("reports", __name__)


@bp.get("/reports/303")
def vat303():
    s = svc()
    year = current_year()
    q = quarter_arg(request.args, calc.quarter_of(date.today()))
    report = s.reports.vat_year(year, q)[-1]
    return render_template("report_303.html", rep=report, q=q, detail=s.reports.vat_detail(year, q), s=s.settings.load().tax(),
                           carry_in=s.settings.carry_in(year), aeat=aeat_card("303", year, q))


@bp.get("/reports/130")
def irpf130():
    s = svc()
    year = current_year()
    q = quarter_arg(request.args, calc.quarter_of(date.today()))
    return render_template("report_130.html", rep=s.reports.irpf_year(year, q)[-1], q=q,
                           by_cat=s.reports.irpf_by_category(year, q), s=s.settings.load().tax(),
                           aeat=aeat_card("130", year, q))


@bp.get("/reports/115")
def rent115():
    """Modelo 115: IRPF the user withheld from landlords, per quarter, with the entries of the chosen quarter."""
    s = svc()
    year = current_year()
    q = quarter_arg(request.args, calc.quarter_of(date.today()))
    quarters = s.reports.retentions_115(year)
    return render_template("report_115.html", quarters=quarters, rep=quarters[q - 1], q=q)


@bp.get("/reports/renta")
def renta():
    s = svc()
    year = current_year()
    return render_template("report_renta.html", a=s.reports.annual(year), s=s.settings.load().tax(),
                           paid=s.filings.for_year_model(year, "130"))
