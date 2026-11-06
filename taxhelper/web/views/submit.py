"""Sending modelo 130 and 303 to AEAT (the test environment): certificate, review and confirmation, result."""
from __future__ import annotations

import json
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ...aeat import service as aeat_service
from ...aeat import submission
from ...aeat.transport import TransportError
from ...i18n import UserError, t
from ..helpers import current_year, quarter_arg
from ..infra import svc

bp = Blueprint("submit", __name__)

ENVIRONMENT = "test"  # the only environment enabled; see aeat.submission.ENVIRONMENTS

# casillas shown on the review screen, by model
REVIEW_CASILLAS = {"130": ("01", "02", "03", "04", "05", "06", "07", "12", "13", "14", "15", "17", "19"),
                   "303": ("27", "45", "46", "64", "71")}


def _certificates():
    """(certificates, error message): the store can be unavailable (not Windows, PowerShell blocked)."""
    try:
        return svc().transport.certificates(), ""
    except TransportError as exc:
        return [], str(exc)


def _review_args():
    model = request.values.get("model", "")
    if model not in aeat_service.MODELS:
        abort(404)
    return model, request.values.get("year", type=int) or current_year(), quarter_arg(request.values)


def _preflight(model, year, quarter):
    s = svc()
    return submission.preflight(transport=s.transport, settings=s.settings.load(), reports=s.reports, checks=s.checks,
                                model=model, year=year, quarter=quarter)


def _last_finished_quarter(today: date):
    """(year, quarter) of the latest quarter that is over: the one a declaration is due for."""
    quarter = (today.month - 1) // 3
    return (today.year, quarter) if quarter else (today.year - 1, 4)


@bp.get("/submit")
def index():
    s = svc()
    certs, cert_error = _certificates()
    now = submission.spain_now()
    default_year, default_quarter = _last_finished_quarter(now.date())
    return render_template("submit.html", certs=certs, cert_error=cert_error, selected=s.settings.load().aeat_certificate,
                           history=s.submissions.all(), outside=submission.outside_test_hours(now),
                           models=aeat_service.MODELS, default_year=default_year, default_quarter=default_quarter)


@bp.post("/submit/certificate")
def choose_certificate():
    thumbprint = request.form.get("thumbprint", "").strip().upper()
    certs, error = _certificates()
    if error or not any(c.thumbprint == thumbprint for c in certs):
        flash(error or t("submit.err_unknown_certificate"), "error")
    else:
        svc().settings.update(aeat_certificate=thumbprint)
        flash(t("submit.flash_certificate"), "ok")
    return redirect(url_for("submit.index"))


@bp.get("/submit/review")
def review():
    model, year, quarter = _review_args()
    try:
        pre = _preflight(model, year, quarter)
    except (UserError, TransportError) as exc:
        flash(str(exc), "error")
        return redirect(url_for("submit.index"))
    rep = svc().reports.irpf_year(year, quarter)[-1] if model == "130" else svc().reports.vat_year(year, quarter)[-1]
    casillas = [(n, getattr(rep, f"c{n}")) for n in REVIEW_CASILLAS[model]]
    return render_template("submit_review.html", pre=pre, model=model, year=year, quarter=quarter, casillas=casillas)


@bp.post("/submit/send")
def send():
    model, year, quarter = _review_args()
    back = redirect(url_for("submit.review", model=model, year=year, q=quarter))
    if not request.form.get("confirm"):
        flash(t("submit.flash_confirm"), "error")
        return back
    s = svc()
    try:
        row = submission.submit(transport=s.transport, settings=s.settings.load(), reports=s.reports, checks=s.checks,
                                submissions=s.submissions, model=model, year=year, quarter=quarter,
                                nrc=request.form.get("nrc", ""), env=ENVIRONMENT,
                                expected_sha=request.form.get("sha", ""))
    except (UserError, TransportError) as exc:
        flash(str(exc), "error")
        return back
    return redirect(url_for("submit.detail", submission_id=row["id"]))


@bp.get("/submit/<int:submission_id>")
def detail(submission_id):
    row = svc().submissions.get(submission_id)
    if row is None:
        abort(404)
    return render_template("submit_result.html", r=row, errors=json.loads(row["errors_json"]),
                           warnings=json.loads(row["warnings_json"]))
