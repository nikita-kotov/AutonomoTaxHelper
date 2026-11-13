"""Cross-cutting web plumbing: the per-request service container, security guards, language
selection, template filters and globals, and the automatic backup hook."""
from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import cached_property
from urllib.parse import urlparse

from flask import Flask, abort, current_app, g, request

from .. import backup as bk
from .. import calc, dates, db, i18n
from ..aeat.transport import Transport
from ..filing_calendar import FilingCalendar
from ..fx import RateProvider, RateService
from ..repositories import (CategoryRepository, CheckRepository, ContactRepository, EntryRepository,
                            FilingRepository, FxRateCache, SubmissionRepository)
from ..reports import TaxReports
from ..settings import ACTIVITY_CODES, Settings, SettingsRepository
from .helpers import current_year


AUTHOR = "Nikita Kotov"
DONATE_URL = "https://buy.stripe.com/28E9ATgho9Z98Hqft51wY00"  # where users can thank the author


class Services:
    """Composition root for one request: repositories and services sharing a single connection."""

    def __init__(self, con: sqlite3.Connection, fx_provider: RateProvider, transport: Transport):
        self.con = con
        self._fx_provider = fx_provider
        self.transport = transport  # how authenticated calls to AEAT are made

    @cached_property
    def settings(self) -> SettingsRepository:
        return SettingsRepository(self.con)

    @cached_property
    def entries(self) -> EntryRepository:
        return EntryRepository(self.con)

    @cached_property
    def categories(self) -> CategoryRepository:
        return CategoryRepository(self.con)

    @cached_property
    def contacts(self) -> ContactRepository:
        return ContactRepository(self.con)

    @cached_property
    def filings(self) -> FilingRepository:
        return FilingRepository(self.con)

    @cached_property
    def submissions(self) -> SubmissionRepository:
        return SubmissionRepository(self.con)

    @cached_property
    def checks(self) -> CheckRepository:
        return CheckRepository(self.con)

    @cached_property
    def calendar(self) -> FilingCalendar:
        return FilingCalendar(self.filings, self.settings, self.entries)

    @cached_property
    def reports(self) -> TaxReports:
        return TaxReports(self.settings, self.entries)

    @property
    def fx(self) -> RateService:
        """Rate lookups; online access only while automatic rates are switched on in the settings."""
        provider = self._fx_provider if self.settings.load().auto_fx else None
        return RateService(FxRateCache(self.con), provider)


def svc() -> Services:
    """The request's service container (created on first use)."""
    if "services" not in g:
        g.db = db.connect()
        g.services = Services(g.db, current_app.config["FX_PROVIDER"], current_app.config["AEAT_TRANSPORT"])
    return g.services


def is_new_installation(settings: Settings, entries: EntryRepository) -> bool:
    """True while nothing has been entered: no taxpayer NIF, no activity start and no entry."""
    return not settings.holder_nif and settings.activity_start is None and entries.first_date() is None


def start_path() -> str:
    """The page the browser opens at start-up: the settings on a new installation, otherwise the dashboard."""
    con = db.connect()
    try:
        return "/settings" if is_new_installation(SettingsRepository(con).load(), EntryRepository(con)) else "/"
    finally:
        con.close()


def init_app(app: Flask) -> None:
    """Register guards, hooks, filters and template globals."""

    @app.teardown_appcontext
    def close_db(_exc):
        con = g.pop("db", None)
        g.pop("services", None)
        if con is not None:
            con.close()

    @app.before_request
    def local_only_guard():
        """Reject DNS-rebinding / cross-site posts: this app holds financial data."""
        host = (request.host or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost"):
            abort(400, "Only localhost is allowed")
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403, "Cross-origin request blocked")
            if request.headers.get("Sec-Fetch-Site") == "cross-site":  # sent by every modern browser
                abort(403, "Cross-site request blocked")

    @app.before_request
    def select_language():
        """Saved language, else the browser's preferred one, else English."""
        if request.endpoint == "static":
            return
        saved = svc().settings.load().language
        available = list(i18n.available())
        if saved not in available:
            saved = request.accept_languages.best_match(available) or i18n.FALLBACK
        i18n.set_language(saved)

    @app.after_request
    def auto_backup_after_write(response):
        """After a successful data change, back up if the last backup is old enough."""
        if request.method == "POST" and response.status_code < 400 and request.endpoint not in ("system.backup_now", "system.restore"):
            try:
                bk.run_backup(svc().con)
            except Exception:  # never let a backup problem break saving data
                app.logger.exception("auto backup failed")
        return response

    @app.template_filter("eur")
    def eur_filter(cents):
        return "" if cents is None else i18n.format_money(int(cents))

    @app.template_filter("eur0")
    def eur0_filter(cents):
        """Whole euros, for the large figures of the dashboard."""
        return "" if cents is None else i18n.format_money(int(cents), whole=True)

    @app.template_filter("pct")
    def pct_filter(fraction):
        """'0.30' -> '30' (fraction string to percent string for form fields)."""
        try:
            return calc.fmt_percent(Decimal(str(fraction)) * 100)
        except InvalidOperation:
            return ""

    @app.template_filter("dmy")
    def dmy_filter(value):
        return i18n.format_date(value)

    @app.context_processor
    def template_globals():
        s = svc()
        labels = lambda prefix, codes: {c: i18n.t(f"{prefix}.{c}") for c in codes}  # noqa: E731
        return {
            "HAS_115": "115" in s.settings.load().tracked_models,  # the retention report is opt-in
            "CAN_QUIT": "SERVER" in current_app.config,  # only when run through taxhelper.launcher
            "year": current_year(),
            "years": s.entries.years_in_use(),
            "today": date.today(),
            "lang": i18n.current_language(),
            "LANGS": i18n.available(),
            "INCOME_REGIMES": labels("regime.income", calc.INCOME_REGIMES),
            "EXPENSE_REGIMES": labels("regime.expense", calc.EXPENSE_REGIMES),
            "IRPF_MODES": labels("irpf", calc.IRPF_MODES),
            "ACTIVITY_CODES": ACTIVITY_CODES,
            "AUTHOR": AUTHOR, "DONATE_URL": DONATE_URL, "DATE_MIN": dates.MIN_ISO, "DATE_MAX": dates.MAX_ISO,
            "VAT_MODES": labels("vat", calc.VAT_MODES),
            "cat_name": lambda c: CategoryRepository.label(c["code"], c["name"]),
            "cat_note": lambda c: CategoryRepository.note(c["code"], c["notes"]),
            "model_title": lambda m: i18n.t(f"model.{m}") if i18n.exists(f"model.{m}") else "",
        }
