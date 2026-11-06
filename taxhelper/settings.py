"""User settings as one typed object.

The database keeps settings as text key/value rows (so adding a setting needs no migration).
This module is the only place that knows how those strings map to typed values:
`SettingsRepository.load()` decodes them (tolerating missing or damaged rows by falling back
to defaults) and `save()` encodes them back.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, fields, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional, Tuple

from . import calc, deadlines, renta

PAYMENT_METHODS = ("nrc", "domiciliacion")
VAT_HOME_MODES = ("share", "share_factor", "none")
VAT_NEGATIVE_MODES = ("compensate", "refund")  # what a negative IVA result of the 4th quarter asks for
FX_MODES = ("auto", "manual")
# AEAT key of the activity in the books: A (in the IAE) or B (not in the IAE) plus a two-digit type, or C / D
ACTIVITY_CODES = ("A01", "A02", "A03", "A04", "A05", "B01", "B02", "B03", "B04", "B05", "B06", "C", "D")


@dataclass(frozen=True)
class Settings:
    # interface
    language: str = ""  # '' = follow the browser
    # taxpayer
    holder_nif: str = ""
    holder_surname: str = ""
    holder_firstname: str = ""
    holder_iban: str = ""
    payment_method: str = "nrc"
    aeat_certificate: str = ""  # thumbprint of the Windows-store certificate used to sign submissions
    # tax profile
    irpf_method: str = "simplificada"
    irpf_rate_pct: Decimal = Decimal(20)
    vat_registered: bool = True
    tracked_models: Tuple[str, ...] = deadlines.DEFAULT_MODELS
    extra_holidays: Tuple[date, ...] = ()
    activity_start: Optional[date] = None
    activity_code: str = "A05"  # for the AEAT books; A05 = other professional activities
    iae_epigraph: str = ""  # IAE group or epigraph as in the census, e.g. 2*763
    # defaults for new entries
    default_vat_rate: Decimal = Decimal(21)
    default_withholding_rate: Decimal = Decimal(0)
    fx_mode: str = "auto"
    # home office: what modelo 036 declares - surface of the home (informational) and the percentage affected
    home_total_m2: Decimal = Decimal(0)
    home_affected_pct: Decimal = Decimal(0)
    supplies_factor: Decimal = Decimal("0.30")  # a fraction
    supplies_rule: str = "share_factor"  # IRPF rule for electricity, gas, water, internet
    property_rule: str = "share"  # IRPF rule for rent, IBI, community fees, home insurance
    vat_home_mode: str = "share"
    vat_negative_4t: str = "compensate"  # "refund" asks the AEAT to pay a negative 4T result into the IBAN
    # IRPF details
    difficult_enabled: bool = True
    difficult_pct: Decimal = Decimal("0.05")  # a fraction
    difficult_cap_cents: int = 200_000
    prev_year_net_yield_cents: Optional[int] = None
    start_reduction: bool = False  # 20 % reduction for starting an activity, Renta only; the user decides
    start_reduction_year: int = 0  # first year with a positive net yield; 0 = the year of the activity start
    # estimate of the annual Renta on the dashboard
    renta_personal_minimum_cents: int = renta.DEFAULT_PERSONAL_MINIMUM_CENTS
    renta_other_income_cents: int = 0  # net income of the year outside the activity
    renta_regional_scale: str = ""  # one bracket per line, `12500 12.5`; empty = the default scale
    # backup
    backup_dir: str = ""
    last_backup_at: str = ""
    last_backup_attempt: str = ""
    last_backup_error: str = ""

    def tax(self) -> calc.TaxSettings:
        """The subset the tax calculations need."""
        return calc.TaxSettings(
            home_affected_pct=self.home_affected_pct,
            supplies_factor=self.supplies_factor, supplies_rule=self.supplies_rule,
            property_rule=self.property_rule, vat_home_mode=self.vat_home_mode,
            difficult_enabled=self.difficult_enabled, difficult_pct=self.difficult_pct,
            difficult_cap_cents=self.difficult_cap_cents,
            prev_year_net_yield_cents=self.prev_year_net_yield_cents,
            irpf_rate=self.irpf_rate_pct / 100, irpf_method=self.irpf_method,
            vat_registered=self.vat_registered, start_reduction_years=self.start_reduction_years)

    @property
    def regional_scale(self) -> Optional[renta.Scale]:
        """The regional part of the general scale the user entered, None for the default one."""
        try:
            return renta.parse_scale(self.renta_regional_scale)
        except ValueError:
            return None

    @property
    def start_reduction_years(self) -> Tuple[int, ...]:
        """The years the start-of-activity reduction applies to: its first year and the next; () when it is off."""
        first = self.start_reduction_year or (self.activity_start.year if self.activity_start else 0)
        return (first, first + 1) if self.start_reduction and first else ()

    def default_regime(self, kind: str) -> str:
        """The IVA treatment a new entry of this kind starts with: the general regime for an IVA taxpayer."""
        return "domestic" if self.vat_registered else ("exempt" if kind == "income" else "no_vat")

    @property
    def uses_direct_debit(self) -> bool:
        return self.payment_method == "domiciliacion"

    @property
    def auto_fx(self) -> bool:
        return self.fx_mode == "auto"


# ----------------------------------------------------------------- text <-> typed

def is_valid_iban(value: str) -> bool:
    """True for a well-formed IBAN: country letters, two check digits, up to 30 letters or digits, and a correct
    ISO 7064 mod-97 checksum (what a bank, and the AEAT, verify). Spaces and case are ignored.

    Parameter: the text to check. Returns a bool; an empty text is not valid.
    """
    iban = "".join(value.split()).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    digits = "".join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def _decimal(raw: Dict[str, str], key: str, default: Decimal) -> Decimal:
    try:
        value = Decimal(raw.get(key, "").strip().replace(",", "."))
    except InvalidOperation:
        return default
    return value if value.is_finite() else default


def _flag(raw: Dict[str, str], key: str, default: bool) -> bool:
    """'1' / '0' as stored; anything else (missing, damaged) means the default."""
    return {"1": True, "0": False}.get(raw.get(key, ""), default)


def _money(text: str) -> Optional[int]:
    try:
        return calc.parse_money(text) if text.strip() else None
    except ValueError:
        return None


def parse_year(text: str) -> Optional[int]:
    """A four-digit year 2000..2100, or None when the text is anything else."""
    text = text.strip()
    return int(text) if re.fullmatch(r"20\d\d|2100", text) else None


def _money_or(text: str, default: int) -> int:
    """An amount in euros as cents, or the default when the text is empty or unreadable."""
    value = _money(text)
    return default if value is None else value


def _choice(value: str, allowed, default: str) -> str:
    return value if value in allowed else default


def _home_pct(raw: Dict[str, str], default: Decimal) -> Decimal:
    """The affected percentage. Older versions stored the office area in m²: convert it once, when no
    percentage has been saved yet (the next save of the settings writes the percentage)."""
    if "home_affected_pct" in raw:
        return _decimal(raw, "home_affected_pct", default)
    total, office = _decimal(raw, "home_total_m2", Decimal(0)), _decimal(raw, "home_affected_m2", Decimal(0))
    if total > 0 and office > 0:
        return min((office / total * 100).quantize(Decimal("0.01")), Decimal(100))
    return default


def _dates(text: str) -> Tuple[date, ...]:
    try:
        return tuple(deadlines.parse_holidays(text))
    except ValueError:
        return ()


def _date(text: str) -> Optional[date]:
    try:
        return date.fromisoformat(text.strip()) if text.strip() else None
    except ValueError:
        return None


def decode(raw: Dict[str, str]) -> Settings:
    """Typed settings from stored text rows; anything missing or unreadable falls back to the default."""
    d = Settings()
    cap = _money(raw.get("difficult_cap_eur", ""))
    return Settings(
        language=raw.get("language", d.language),
        holder_nif=raw.get("holder_nif", d.holder_nif),
        holder_surname=raw.get("holder_surname", d.holder_surname),
        holder_firstname=raw.get("holder_firstname", d.holder_firstname),
        holder_iban=raw.get("holder_iban", d.holder_iban),
        payment_method=_choice(raw.get("payment_method", ""), PAYMENT_METHODS, d.payment_method),
        aeat_certificate=raw.get("aeat_certificate", d.aeat_certificate),
        irpf_method=_choice(raw.get("irpf_method", ""), calc.IRPF_METHODS, d.irpf_method),
        irpf_rate_pct=_decimal(raw, "irpf_rate_pct", d.irpf_rate_pct),
        vat_registered=_flag(raw, "vat_registered", d.vat_registered),
        tracked_models=tuple(m for m in raw.get("tracked_models", ",".join(d.tracked_models)).split(",")
                             if m in deadlines.KNOWN_MODELS),
        extra_holidays=_dates(raw.get("extra_holidays", "")),
        activity_start=_date(raw.get("activity_start", "")),
        activity_code=_choice(raw.get("activity_code", ""), ACTIVITY_CODES, d.activity_code),
        iae_epigraph=raw.get("iae_epigraph", d.iae_epigraph),
        default_vat_rate=_decimal(raw, "default_vat_rate", d.default_vat_rate),
        default_withholding_rate=_decimal(raw, "default_withholding_rate", d.default_withholding_rate),
        fx_mode=_choice(raw.get("fx_mode", ""), FX_MODES, d.fx_mode),
        home_total_m2=_decimal(raw, "home_total_m2", d.home_total_m2),
        home_affected_pct=_home_pct(raw, d.home_affected_pct),
        supplies_factor=_decimal(raw, "supplies_factor", d.supplies_factor),
        supplies_rule=_choice(raw.get("supplies_rule", ""), calc.HOME_RULES, d.supplies_rule),
        property_rule=_choice(raw.get("property_rule", ""), calc.HOME_RULES, d.property_rule),
        vat_home_mode=_choice(raw.get("vat_home_mode", ""), VAT_HOME_MODES, d.vat_home_mode),
        vat_negative_4t=_choice(raw.get("vat_negative_4t", ""), VAT_NEGATIVE_MODES, d.vat_negative_4t),
        difficult_enabled=_flag(raw, "difficult_enabled", d.difficult_enabled),
        difficult_pct=_decimal(raw, "difficult_pct", d.difficult_pct),
        difficult_cap_cents=cap if cap is not None else d.difficult_cap_cents,
        prev_year_net_yield_cents=_money(raw.get("prev_year_net_yield_eur", "")),
        start_reduction=_flag(raw, "start_reduction", d.start_reduction),
        start_reduction_year=parse_year(raw.get("start_reduction_year", "")) or 0,
        renta_personal_minimum_cents=_money_or(raw.get("renta_personal_minimum_eur", ""), d.renta_personal_minimum_cents),
        renta_other_income_cents=_money_or(raw.get("renta_other_income_eur", ""), d.renta_other_income_cents),
        renta_regional_scale=raw.get("renta_regional_scale", d.renta_regional_scale),
        backup_dir=raw.get("backup_dir", d.backup_dir),
        last_backup_at=raw.get("last_backup_at", d.last_backup_at),
        last_backup_attempt=raw.get("last_backup_attempt", d.last_backup_attempt),
        last_backup_error=raw.get("last_backup_error", d.last_backup_error),
    )


def _euros(cents: int) -> str:
    return str(cents // 100) if cents % 100 == 0 else f"{cents / 100:.2f}"


def encode(s: Settings) -> Dict[str, str]:
    """Stored text rows for typed settings (also what the settings form shows)."""
    return {
        "language": s.language,
        "holder_nif": s.holder_nif, "holder_surname": s.holder_surname,
        "holder_firstname": s.holder_firstname, "holder_iban": s.holder_iban,
        "payment_method": s.payment_method, "aeat_certificate": s.aeat_certificate,
        "irpf_method": s.irpf_method, "irpf_rate_pct": calc.fmt_percent(s.irpf_rate_pct),
        "vat_registered": "1" if s.vat_registered else "0",
        "tracked_models": ",".join(s.tracked_models),
        "extra_holidays": " ".join(d.isoformat() for d in s.extra_holidays),
        "activity_start": s.activity_start.isoformat() if s.activity_start else "",
        "activity_code": s.activity_code, "iae_epigraph": s.iae_epigraph,
        "default_vat_rate": calc.fmt_percent(s.default_vat_rate),
        "default_withholding_rate": calc.fmt_percent(s.default_withholding_rate),
        "fx_mode": s.fx_mode,
        "home_total_m2": str(s.home_total_m2), "home_affected_pct": calc.fmt_percent(s.home_affected_pct),
        "supplies_factor": str(s.supplies_factor), "supplies_rule": s.supplies_rule,
        "property_rule": s.property_rule, "vat_home_mode": s.vat_home_mode, "vat_negative_4t": s.vat_negative_4t,
        "difficult_enabled": "1" if s.difficult_enabled else "0",
        "difficult_pct": str(s.difficult_pct), "difficult_cap_eur": _euros(s.difficult_cap_cents),
        "prev_year_net_yield_eur": _euros(s.prev_year_net_yield_cents) if s.prev_year_net_yield_cents is not None else "",
        "start_reduction": "1" if s.start_reduction else "0",
        "start_reduction_year": str(s.start_reduction_year) if s.start_reduction_year else "",
        "renta_personal_minimum_eur": _euros(s.renta_personal_minimum_cents),
        "renta_other_income_eur": _euros(s.renta_other_income_cents),
        "renta_regional_scale": s.renta_regional_scale,
        "backup_dir": s.backup_dir, "last_backup_at": s.last_backup_at,
        "last_backup_attempt": s.last_backup_attempt, "last_backup_error": s.last_backup_error,
    }


# ----------------------------------------------------------------- storage

class SettingsRepository:
    """Reads and writes the key/value `settings` table as typed `Settings`."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self._loaded: Optional[Settings] = None  # a request reads the settings many times; read the table once

    def raw(self) -> Dict[str, str]:
        return {r["key"]: r["value"] for r in self.con.execute("SELECT key, value FROM settings")}

    def set_raw(self, key: str, value: str) -> None:
        self.con.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
        self._loaded = None

    def load(self) -> Settings:
        if self._loaded is None:
            self._loaded = decode(self.raw())
        return self._loaded

    def save(self, settings: Settings) -> None:
        for key, value in encode(settings).items():
            self.set_raw(key, value)
        self.con.commit()

    def update(self, **changes) -> Settings:
        """Change some fields and persist (e.g. update(language='es'))."""
        unknown = set(changes) - {f.name for f in fields(Settings)}
        if unknown:
            raise TypeError(f"unknown settings: {sorted(unknown)}")
        updated = replace(self.load(), **changes)
        self.save(updated)
        return updated

    # IVA balance carried into a year is a per-year value, kept outside the typed settings
    def carry_in(self, year: int) -> int:
        return _money(self.raw().get(f"vat_carry_in_{year}", "")) or 0

    def carry_in_text(self, year: int) -> str:
        return self.raw().get(f"vat_carry_in_{year}", "")

    def set_carry_in(self, year: int, text: str) -> None:
        self.set_raw(f"vat_carry_in_{year}", text.strip())
        self.con.commit()
