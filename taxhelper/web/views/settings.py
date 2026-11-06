"""The settings page: language, taxpayer, tax profile, defaults, home office, IRPF details, backup."""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ... import deadlines
from ...i18n import t
from ...settings import encode
from ..forms import parse_settings
from ..helpers import current_year
from ..infra import svc

bp = Blueprint("settings", __name__)


@bp.route("/settings", methods=["GET", "POST"])
def page():
    s = svc()
    year = current_year()
    if request.method == "POST":
        result = parse_settings(request.form, s.settings.load())
        if result.errors:
            for message in result.errors:
                flash(message, "error")
            return render_template("settings.html", raw=result.shown, carry=result.carry_in_text,
                                   tracked=[m for m in result.shown["tracked_models"].split(",") if m],
                                   known_models=deadlines.KNOWN_MODELS)
        s.settings.save(result.settings)
        s.settings.set_carry_in(year, result.carry_in_text)
        s.calendar.sync()  # tracked models, direct debit, holidays and start date change the calendar
        flash(t("flash.settings_saved"), "ok")
        return redirect(url_for("settings.page", year=year))
    settings = s.settings.load()
    return render_template("settings.html", raw=encode(settings), carry=s.settings.carry_in_text(year),
                           tracked=list(settings.tracked_models), known_models=deadlines.KNOWN_MODELS)
