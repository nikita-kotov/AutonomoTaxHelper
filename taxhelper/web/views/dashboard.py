"""The start page: overdue and upcoming filings and the running figures of the current quarter."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, render_template

from ... import calc, i18n
from ...i18n import t
from ..helpers import current_year
from ..infra import svc

bp = Blueprint("dashboard", __name__)


@bp.get("/")
def index():
    s = svc()
    year = current_year()
    settings = s.settings.load()
    s.calendar.ensure({year - 1, year, date.today().year})
    quarter = calc.quarter_of(date.today()) if year == date.today().year else 4

    entries = s.reports.year_entries(year)
    annual = s.reports.annual(year)
    overview = s.reports.overview(year)
    paid_130 = {int(f["period"][0]): f["result_cents"] for f in s.filings.for_year_model(year, "130")
                if f["status"] == "filed" and f["result_cents"] is not None and f["period"][:1].isdigit()}
    open_filings = [f for f in s.calendar.rows(pending_only=True) if f["display"] in ("overdue", "due_soon", "open")]
    return render_template(
        "dashboard.html",
        vat=s.reports.vat_year(year, quarter)[-1], irpf=s.reports.irpf_year(year, quarter)[-1],
        annual=annual, q=quarter, overview=overview, chart=_chart(overview, settings.vat_registered),
        taxes_total=sum(r.irpf + r.vat for r in overview),
        renta=s.reports.renta_estimate(year, paid_130, quarter), default_scale=not settings.regional_scale,
        overdue=[f for f in open_filings if f["display"] == "overdue"],
        upcoming=[f for f in open_filings if f["display"] != "overdue"][:6],
        notes=_notes(settings, entries), entries_count=len(entries), vat_registered=settings.vat_registered)


def _chart(overview, vat_registered):
    """The data of the quarterly chart: the series in their fixed order and each quarter's values with their text."""
    series = [("income", t("dash.fig_income"), "s1"), ("expenses", t("dash.fig_expenses"), "s2"),
              ("irpf", t("dash.s_irpf"), "s3")] + ([("vat", t("dash.s_vat"), "s4")] if vat_registered else [])
    return {"label": t("dash.chart_label"),
            "series": [{"key": key, "label": label, "cls": cls} for key, label, cls in series],
            "quarters": [{"label": f"{r.quarter}T",
                          "cells": {key: {"cents": getattr(r, key), "text": i18n.format_money(getattr(r, key))}
                                     for key, _, _ in series}} for r in overview]}


def _notes(settings, entries):
    """Things the user should fix, in plain words."""
    notes = []
    if settings.last_backup_error:
        notes.append(t("dash.note_backup_failed", error=settings.last_backup_error))
    elif not settings.backup_dir and entries:
        notes.append(t("dash.note_backup_off"))
    if settings.tax().affected_share == 0 and any(e.irpf_mode.startswith("home") for e in entries):
        notes.append(t("dash.note_home_unset"))
    return notes
