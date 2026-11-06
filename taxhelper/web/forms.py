"""Turning posted forms into validated, typed data.

Each parser returns the parsed values plus a list of translated error messages (and warnings
where saving is still allowed). Parsers read the request form but never touch the database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

from .. import calc, deadlines, fx, i18n, renta
from ..dates import parse_date
from ..i18n import UserError, t
from ..settings import (ACTIVITY_CODES, FX_MODES, PAYMENT_METHODS, VAT_HOME_MODES, VAT_NEGATIVE_MODES, Settings, encode,
                        is_valid_iban, parse_year)

# ----------------------------------------------------------------- number helpers


def parse_decimal(text, default="0") -> Decimal:
    """A finite Decimal from user text ('7,5' allowed); NaN/Infinity are rejected like any garbage."""
    tx = (text or default).strip().replace(",", ".") or default
    try:
        value = Decimal(tx)
    except InvalidOperation as exc:
        raise ValueError(tx) from exc
    if not value.is_finite():
        raise ValueError(tx)
    return value


def parse_percent(text, default="0") -> Decimal:
    """A percentage 0..100. Raises UserError with a translated message."""
    try:
        value = parse_decimal((text or "").replace("%", ""), default)
    except ValueError as exc:
        raise UserError("err.percent_invalid", value=text) from exc
    if value < 0 or value > 100:
        raise UserError("err.percent_range")
    return value


def parse_cap(text) -> Optional[int]:
    """Annual cap in cents, or None when blank. Raises ValueError for bad input."""
    text = (text or "").strip()
    return calc.parse_money(text) if text else None


# ----------------------------------------------------------------- entry (invoice) form

@dataclass
class EntryFormResult:
    values: Dict[str, object]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _money_or_error(text, error_key: str, errors: List[str]) -> Optional[int]:
    try:
        return calc.parse_money(text)
    except ValueError:
        errors.append(t(error_key))
        return None


def parse_entry(form, kind: str) -> EntryFormResult:
    """Validate the income/expense form, including the foreign-currency fields."""
    errors: List[str] = []
    warnings: List[str] = []
    v: Dict[str, object] = {"kind": kind}

    try:
        v["date"] = parse_date(form.get("date", "")).isoformat()  # a year such as 0026 would vanish from every view
    except ValueError:
        errors.append(t("err.date"))
        v["date"] = ""
    for name in ("number", "vat_id", "description", "notes"):
        v[name] = form.get(name, "").strip()
    v["counterparty"] = " ".join(form.get("counterparty", "").split())  # same spelling rule as the contacts
    v["country"] = (form.get("country", "ES").strip().upper() or "ES")[:2]

    regimes = calc.INCOME_REGIMES if kind == "income" else calc.EXPENSE_REGIMES
    v["regime"] = form.get("regime", "domestic")
    if v["regime"] not in regimes:
        errors.append(t("err.regime"))

    _parse_money_fields(form, v, errors, warnings)
    _parse_vat_fields(form, kind, v, errors)

    if kind == "expense":
        category = form.get("category_id", type=int)
        if not category:
            errors.append(t("err.category"))
        v["category_id"] = category
    else:
        v["category_id"] = None
    return EntryFormResult(v, errors, warnings)


def _parse_money_fields(form, v: dict, errors: List[str], warnings: List[str]) -> None:
    """Currency, original amount, exchange rate and the euro base."""
    currency = (form.get("currency") or fx.EUR).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        errors.append(t("err.currency"))
        currency = fx.EUR
    v["currency"] = currency
    v.update(orig_amount_cents=None, fx_rate="", fx_rate_date="", fx_source="")

    expected = None  # the euro amount implied by original amount and rate
    if currency != fx.EUR:
        orig_text, rate_text = (form.get("orig_amount") or "").strip(), (form.get("fx_rate") or "").strip()
        if not orig_text or not rate_text:
            errors.append(t("err.fx_required"))
        else:
            orig = _money_or_error(orig_text, "err.orig_amount", errors)
            try:
                rate = fx.parse_rate(rate_text)
            except ValueError:
                errors.append(t("err.fx_rate"))
                rate = None
            if orig is not None and rate is not None:
                expected = fx.convert_to_eur_cents(orig, rate)
                v["orig_amount_cents"] = orig
                v["fx_rate"] = format(rate.normalize(), "f")
                source = form.get("fx_source")
                rate_date = (form.get("fx_rate_date") or "").strip()
                try:
                    date.fromisoformat(rate_date)
                except ValueError:
                    rate_date = ""
                v["fx_source"] = fx.SOURCE_ECB if source == fx.SOURCE_ECB and rate_date else fx.SOURCE_MANUAL
                v["fx_rate_date"] = rate_date if v["fx_source"] == fx.SOURCE_ECB else ""

    base_text = (form.get("base") or "").strip()
    if base_text:
        base = _money_or_error(base_text, "err.base", errors)
    elif expected is not None:
        base = expected  # left blank: use the converted amount
    else:
        errors.append(t("err.base"))
        base = None
    v["base_cents"] = base if base is not None else 0
    if base is not None and expected is not None and abs(base - expected) > 1:
        warnings.append(t("warn.fx_mismatch", entered=i18n.format_money(base), expected=i18n.format_money(expected)))


def _parse_vat_fields(form, kind: str, v: dict, errors: List[str]) -> None:
    """IVA rate/amount and the IRPF retention: withheld from the user by a client (income), or by the user from a
    landlord (expense, declared in modelo 115)."""
    try:
        rate = parse_percent(form.get("vat_rate"), "0")
    except UserError as exc:
        errors.append(str(exc))
        rate = Decimal(0)
    entered_vat = None
    if (form.get("vat_amount") or "").strip():
        entered_vat = _money_or_error(form.get("vat_amount"), "err.vat_amount", errors)
    if v["regime"] in calc.RATELESS_REGIMES:
        rate = Decimal(0)
    v["vat_rate"] = calc.fmt_percent(rate)
    v["vat_cents"] = calc.compute_vat(v["regime"], v["base_cents"], rate, entered_vat)

    withholding = Decimal(0)
    try:
        withholding = parse_percent(form.get("withholding_rate"), "0")
    except UserError as exc:
        errors.append(str(exc))
    v["withholding_rate"] = calc.fmt_percent(withholding)
    v["withholding_cents"] = calc.round_cents(Decimal(v["base_cents"]) * withholding / 100)


# ----------------------------------------------------------------- contact form

@dataclass
class ContactFormResult:
    values: Dict[str, object]
    errors: List[str] = field(default_factory=list)


def parse_contact(form) -> ContactFormResult:
    """Validate the contact form: a client or a supplier and the values new entries start from."""
    errors: List[str] = []
    v: Dict[str, object] = {"kind": form.get("kind", "")}
    if v["kind"] not in ("income", "expense"):
        errors.append(t("contacts.err_kind"))
    v["name"] = " ".join(form.get("name", "").split())
    if not v["name"]:
        errors.append(t("contacts.err_name"))
    v["country"] = (form.get("country", "ES").strip().upper() or "ES")[:2]
    for name in ("vat_id", "address", "email", "description", "notes"):
        v[name] = form.get(name, "").strip()
    if v["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(v["email"])):
        errors.append(t("contacts.err_email"))

    regimes = calc.INCOME_REGIMES if v["kind"] == "income" else calc.EXPENSE_REGIMES
    v["regime"] = form.get("regime", "")
    if v["regime"] and v["regime"] not in regimes:
        errors.append(t("err.regime"))
    for name in ("vat_rate", "withholding_rate"):
        text = (form.get(name) or "").strip()
        v[name] = ""
        if text:
            try:
                v[name] = calc.fmt_percent(parse_percent(text))
            except UserError as exc:
                errors.append(str(exc))
    v["category_id"] = (form.get("category_id", type=int) or None) if v["kind"] == "expense" else None
    return ContactFormResult(v, errors)


# ----------------------------------------------------------------- settings form

@dataclass
class SettingsFormResult:
    settings: Settings
    carry_in_text: str
    errors: List[str]
    shown: Dict[str, str]  # what to put back into the form fields (for re-display after an error)


def _probe_writable(folder: str) -> Optional[str]:
    """None if the folder can be created and written to, else the OS error text."""
    try:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".taxhelper-write-test"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return str(exc)
    return None


def _parse_percents(form, errors: List[str]) -> Dict[str, Decimal]:
    """The percentage fields of the settings page by field name; a bad one adds an error and is left out."""
    percents: Dict[str, Decimal] = {}
    for name, default in (("home_affected_pct", "0"), ("supplies_factor_pct", "30"), ("difficult_pct_pct", "5"),
                          ("irpf_rate_pct", "20"), ("default_vat_rate", "21"), ("default_withholding_rate", "0")):
        try:
            percents[name] = parse_percent(form.get(name), default)
        except UserError as exc:
            errors.append(str(exc))
    return percents


def _parse_amount_settings(form, errors: List[str]) -> Dict[str, Optional[int]]:
    """The optional amounts of the settings page in cents (None = left empty); a bad one adds an error."""
    money: Dict[str, Optional[int]] = {}
    for name in ("difficult_cap_eur", "prev_year_net_yield_eur", "vat_carry_in", "renta_personal_minimum_eur",
                 "renta_other_income_eur"):
        text = form.get(name, "").strip()
        try:
            money[name] = calc.parse_money(text) if text else None
        except ValueError:
            errors.append(t("err.amount_field", field=name))
    if (money.get("renta_personal_minimum_eur") or 0) < 0:
        errors.append(t("err.amount_field", field="renta_personal_minimum_eur"))
    return money


def _parse_taxpayer(form, errors: List[str]):
    """(NIF, IBAN, payment method) of the settings page, checking the NIF length and the IBAN."""
    nif = form.get("holder_nif", "").strip().upper()
    if nif and len(nif) != 9:
        errors.append(t("err.nif_len9"))
    iban = form.get("holder_iban", "").replace(" ", "").upper()
    payment = form.get("payment_method") if form.get("payment_method") in PAYMENT_METHODS else "nrc"
    if payment == "domiciliacion" and not iban:
        errors.append(t("err.iban_domic"))
    elif iban and not is_valid_iban(iban):
        errors.append(t("err.iban_invalid"))
    return nif, iban, payment


def parse_settings(form, current: Settings) -> SettingsFormResult:
    """Validate the settings page. Fields that are not on the page (backup status) are kept."""
    errors: List[str] = []

    shown = encode(current)
    shown.update(form.to_dict())
    shown["tracked_models"] = ",".join(form.getlist("tracked_models"))
    shown["vat_registered"] = "1" if form.get("vat_registered") else "0"
    shown["difficult_enabled"] = "1" if form.get("difficult_enabled") else "0"
    shown["start_reduction"] = "1" if form.get("start_reduction") else "0"

    home_total = Decimal(0)  # surface of the home as in modelo 036 (informational)
    try:
        home_total = parse_decimal(form.get("home_total_m2"))
        if home_total < 0:
            raise ValueError("negative")
    except ValueError:
        errors.append(t("err.area_numeric"))

    percents = _parse_percents(form, errors)
    money = _parse_amount_settings(form, errors)

    nif, iban, payment = _parse_taxpayer(form, errors)
    if form.get("vat_negative_4t") == "refund" and not iban:
        errors.append(t("err.iban_refund"))
    language = form.get("language", "")
    if language and language not in i18n.available():
        errors.append(t("err.language"))
    irpf_method = form.get("irpf_method", "simplificada")
    if irpf_method not in calc.IRPF_METHODS:
        errors.append(t("err.irpf_method"))
    try:
        holidays = tuple(deadlines.parse_holidays(form.get("extra_holidays", "")))
    except ValueError:
        errors.append(t("err.holidays"))
        holidays = ()
    activity_start = None
    if form.get("activity_start", "").strip():
        try:
            activity_start = parse_date(form.get("activity_start"))
        except ValueError:
            errors.append(t("err.activity_start"))
    scale_text = form.get("renta_regional_scale", "").strip()
    try:
        renta.parse_scale(scale_text)
    except ValueError:
        errors.append(t("err.renta_scale"))
    reduction_year = parse_year(form.get("start_reduction_year", ""))
    if reduction_year is None and form.get("start_reduction_year", "").strip():
        errors.append(t("err.start_reduction_year"))
    backup_dir = form.get("backup_dir", "").strip()
    if backup_dir:
        problem = _probe_writable(backup_dir)
        if problem:
            errors.append(t("err.backup_dir", error=problem))

    carry_text = form.get("vat_carry_in", "")
    if errors:
        return SettingsFormResult(current, carry_text, errors, shown)

    cap = money["difficult_cap_eur"]
    settings = replace(
        current,
        language=language, holder_nif=nif, holder_iban=iban, payment_method=payment,
        holder_surname=form.get("holder_surname", "").strip(), holder_firstname=form.get("holder_firstname", "").strip(),
        irpf_method=irpf_method, irpf_rate_pct=percents["irpf_rate_pct"],
        vat_registered=bool(form.get("vat_registered")),
        tracked_models=tuple(m for m in form.getlist("tracked_models") if m in deadlines.KNOWN_MODELS),
        extra_holidays=holidays, activity_start=activity_start,
        activity_code=form.get("activity_code") if form.get("activity_code") in ACTIVITY_CODES else "A05",
        iae_epigraph=form.get("iae_epigraph", "").strip()[:5],
        default_vat_rate=percents["default_vat_rate"], default_withholding_rate=percents["default_withholding_rate"],
        fx_mode=form.get("fx_mode") if form.get("fx_mode") in FX_MODES else "auto",
        home_total_m2=home_total, home_affected_pct=percents["home_affected_pct"],
        supplies_factor=percents["supplies_factor_pct"] / 100,
        supplies_rule=form.get("supplies_rule") if form.get("supplies_rule") in calc.HOME_RULES else "share_factor",
        property_rule=form.get("property_rule") if form.get("property_rule") in calc.HOME_RULES else "share",
        vat_home_mode=form.get("vat_home_mode") if form.get("vat_home_mode") in VAT_HOME_MODES else "share",
        vat_negative_4t=form.get("vat_negative_4t") if form.get("vat_negative_4t") in VAT_NEGATIVE_MODES else "compensate",
        difficult_enabled=bool(form.get("difficult_enabled")), difficult_pct=percents["difficult_pct_pct"] / 100,
        difficult_cap_cents=cap if cap is not None else 200_000,
        prev_year_net_yield_cents=money["prev_year_net_yield_eur"],
        start_reduction=bool(form.get("start_reduction")), start_reduction_year=reduction_year or 0,
        renta_personal_minimum_cents=(money["renta_personal_minimum_eur"] if money["renta_personal_minimum_eur"] is not None
                                      else renta.DEFAULT_PERSONAL_MINIMUM_CENTS),
        renta_other_income_cents=money["renta_other_income_eur"] or 0, renta_regional_scale=scale_text,
        backup_dir=backup_dir)
    return SettingsFormResult(settings, carry_text, [], shown)


# ----------------------------------------------------------------- filing form

@dataclass
class FilingFormResult:
    values: Dict[str, object]
    errors: List[str]


def parse_filing(form, row) -> FilingFormResult:
    """Validate the filing details page against the stored row (for the unchanged fallbacks)."""
    errors: List[str] = []
    try:
        due = parse_date(form.get("due_date", "")).isoformat()
    except ValueError:
        errors.append(t("err.filing_due"))
        due = row["due_date"]
    status = form.get("status", "pending")
    if status not in ("pending", "filed", "not_required"):
        status = "pending"
    filed_date = form.get("filed_date", "").strip()
    if filed_date:
        try:
            filed_date = parse_date(filed_date).isoformat()
        except ValueError:
            errors.append(t("err.filed_date"))
    if status == "filed" and not filed_date and not errors:
        filed_date = date.today().isoformat()
    result = None
    if form.get("result", "").strip():
        result = _money_or_error(form.get("result"), "err.result_amount", errors)
    return FilingFormResult({
        "due_date": due, "status": status, "filed_date": filed_date, "result_cents": result,
        "reference": form.get("reference", "").strip(), "notes": form.get("notes", "").strip(),
        "due_manual": 1 if (due != row["due_date"] or row["due_manual"]) else 0}, errors)
