"""The settings page: language, taxpayer, tax profile, defaults, home office, IRPF details, backup."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from ... import backup, deadlines
from ...i18n import t
from ...settings import encode
from ..forms import parse_settings
from ..helpers import current_year
from ..infra import svc

bp = Blueprint("settings", __name__)


def _folder_backups(folder_text: str) -> list:
    """The backups in the backup folder, newest first, for the restore list."""
    folder = folder_text.strip()
    return [(p.name, backup.backup_time(p)) for p in reversed(backup.list_backups(Path(folder)))] if folder else []


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
                                   known_models=deadlines.KNOWN_MODELS,
                                   backups=_folder_backups(result.shown["backup_dir"]))
        s.settings.save(result.settings)
        s.settings.set_carry_in(year, result.carry_in_text)
        s.calendar.sync()  # tracked models, direct debit, holidays and start date change the calendar
        flash(t("flash.settings_saved"), "ok")
        return redirect(url_for("settings.page", year=year))
    settings = s.settings.load()
    return render_template("settings.html", raw=encode(settings), carry=s.settings.carry_in_text(year),
                           tracked=list(settings.tracked_models), known_models=deadlines.KNOWN_MODELS,
                           backups=_folder_backups(settings.backup_dir))
