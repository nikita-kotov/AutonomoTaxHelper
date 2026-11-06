"""Pure tax calculation logic for a Spanish autonomo (IRPF estimacion directa
simplificada, IVA regimen general). No I/O here: everything takes plain data
and returns plain data so it can be unit-tested.

All money is integer cents. Rates are percentages (21 means 21%).
Rounding is half-up to the cent, applied per entry.

Rules marked VERIFY depend on interpretation (DGT criteria) or change by year.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Dict, Iterable, List, Optional, Tuple

from . import i18n
from .i18n import Msg

# ----------------------------------------------------------------- constants

# Code lists only: display labels live in locales/*.json under regime.income.<code>,
# regime.expense.<code>, irpf.<code> and vat.<code>.
INCOME_REGIMES = ("domestic", "exempt", "eu_b2b", "non_eu")
EXPENSE_REGIMES = ("domestic", "eu_reverse", "non_eu_reverse", "no_vat")
IRPF_MODES = ("full", "home_supply", "home_property", "none")
VAT_MODES = ("full", "home_supply", "home_property", "none")
IRPF_METHODS = ("simplificada", "normal")
HOME_RULES = ("share", "share_factor")  # IRPF rule for home costs: affected %, or affected % x 30 %

REVERSE_REGIMES = ("eu_reverse", "non_eu_reverse")
RATELESS_REGIMES = ("exempt", "eu_b2b", "non_eu", "no_vat")  # no IVA rate applies: the rate is forced to 0


# ----------------------------------------------------------------- helpers

def D(value) -> Decimal:
    """Convert any numeric-like value to Decimal."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def round_cents(amount: Decimal) -> int:
    """Round a Decimal number of cents half-up to an int."""
    return int(D(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _plain_number(digits: str, local_decimal: str) -> str:
    """Turn a digit string with '.'/',' separators into a plain '1234.56' string.

    With both separators the last one is the decimal point. A separator that repeats is a thousands
    mark ('1.234.567'). One lone separator followed by exactly three digits ('1.500', '1,234') is
    ambiguous: it is a decimal point only if it is the decimal separator of the user's language,
    otherwise a thousands mark. Raises ValueError for malformed groupings.
    """
    dots, commas = digits.count("."), digits.count(",")
    if dots and commas:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return digits.replace(thousands, "").replace(decimal, ".")
    if not dots and not commas:
        return digits
    sep = "." if dots else ","
    groups = digits.split(sep)
    if len(groups) > 2:  # repeated separator: thousands grouping
        if not (1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])):
            raise ValueError(digits)
        return "".join(groups)
    head, tail = groups
    reads_as_thousands = len(tail) == 3 and 1 <= len(head) <= 3 and head != "0"
    if reads_as_thousands and sep != local_decimal:
        return head + tail
    return f"{head}.{tail}"


def parse_money(text, decimal_separator: Optional[str] = None) -> int:
    """Parse an amount such as '1.234,56', '1,234.56', '1234.56', '1 234,56' or '-12' into cents.

    `decimal_separator` ('.' or ',') is the user's decimal mark; it settles the ambiguous '1.500' /
    '1,500' and defaults to the interface language's. Raises ValueError on garbage input.
    """
    if text is None:
        raise ValueError("empty amount")
    s = re.sub(r"[\s\u20ac]", "", str(text))  # spaces (incl. non-breaking) and the euro sign
    negative = s.startswith("-")
    if s[:1] in ("+", "-"):  # one optional sign
        s = s[1:]
    if not s or not re.fullmatch(r"[\d.,]+", s):
        raise ValueError(f"bad amount: {text!r}")
    plain = _plain_number(s, decimal_separator or i18n.meta()["decimal"])
    try:
        return round_cents(Decimal(("-" if negative else "") + plain) * 100)
    except InvalidOperation as exc:
        raise ValueError(f"bad amount: {text!r}") from exc


def fmt_percent(value) -> str:
    """Plain decimal text for a percentage: 10 -> '10', 7.5 -> '7.5', 0 -> '0' (never '1E+1')."""
    value = D(value)
    return format(value.normalize(), "f") if value else "0"


def quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def quarter_end(year: int, q: int) -> date:
    month = q * 3
    day = 31 if month in (3, 12) else 30
    return date(year, month, day)


# ----------------------------------------------------------------- data

@dataclass(frozen=True)
class TaxSettings:
    """User parameters that drive deductions."""
    # "Grado de afectacion" of the home to the activity, in percent, exactly as declared in
    # modelo 036 (casilla 423). The surface of the home is informational and not used in the maths.
    home_affected_pct: Decimal = Decimal(0)
    supplies_factor: Decimal = Decimal("0.30")  # art. 30.2.5a LIRPF
    # IRPF rule per kind of home cost: 'share' = invoice x affected %, 'share_factor' = also x supplies_factor.
    # Supplies (electricity, gas, water, internet): the general rule is share_factor (art. 30.2.5a LIRPF).
    # Rent, IBI, community fees, home insurance: the law allows 'share'; many gestors play safe with 'share_factor'.
    supplies_rule: str = "share_factor"
    property_rule: str = "share"
    # 'share' = IVA of supplies deducted by the affected share only (default);
    # 'share_factor' = also multiply by supplies_factor, which deducts less;
    # 'none' = no IVA deduction, the full invoice is the IRPF cost. VERIFY with gestor.
    vat_home_mode: str = "share"
    difficult_enabled: bool = True  # apply gastos de dificil justificacion (only in simplificada)
    difficult_pct: Decimal = Decimal("0.05")
    difficult_cap_cents: int = 200_000
    prev_year_net_yield_cents: Optional[int] = None  # for 130 casilla 13
    irpf_rate: Decimal = Decimal("0.20")  # 130 rate (20% general, 8% Ceuta/Melilla)
    irpf_method: str = "simplificada"  # 'simplificada' | 'normal' (estimacion directa)
    vat_registered: bool = True  # False: activity without IVA (no deductions, no 303)
    # Years in which the 20 % reduction for starting an activity applies in the Renta (empty = it is off).
    start_reduction_years: Tuple[int, ...] = ()

    @property
    def affected_share(self) -> Decimal:
        """The affected part of the home as a fraction 0..1 (11 % -> 0.11)."""
        return min(max(self.home_affected_pct / 100, Decimal(0)), Decimal(1))

    def home_share(self, rule: str) -> Decimal:
        """The part of a home cost that is deductible under a rule: the affected %, times the supplies
        factor (30 %) when the rule is 'share_factor'."""
        return self.affected_share * (self.supplies_factor if rule == "share_factor" else Decimal(1))


@dataclass(frozen=True)
class Entry:
    """One income or expense record, joined with its category rules."""
    kind: str  # 'income' | 'expense'
    date: date
    regime: str
    base_cents: int
    vat_rate: Decimal = Decimal(0)
    vat_cents: int = 0
    withholding_cents: int = 0
    irpf_mode: str = "full"
    vat_mode: str = "full"
    category: str = ""
    description: str = ""
    category_cap_cents: Optional[int] = None  # annual cap of the category's IRPF deduction
    category_code: str = ""
    counterparty: str = ""
    vat_id: str = ""


# ----------------------------------------------------------------- per entry

def compute_vat(regime: str, base_cents: int, rate: Decimal, entered_vat_cents: Optional[int]) -> int:
    """Return the VAT amount (cents) to store for an entry.

    - domestic: the amount printed on the invoice if given, else base*rate.
    - reverse-charge expenses: always self-assessed base*rate.
    - everything else: 0.
    """
    if regime in REVERSE_REGIMES:
        return round_cents(D(base_cents) * D(rate) / 100)
    if regime == "domestic":
        if entered_vat_cents is not None:
            return entered_vat_cents
        return round_cents(D(base_cents) * D(rate) / 100)
    return 0


def vat_deductible(e: Entry, s: TaxSettings) -> int:
    """IVA soportado that can be deducted in the 303 for an expense entry."""
    if e.kind != "expense" or e.regime == "no_vat" or e.vat_cents == 0 or not s.vat_registered:
        return 0
    v = D(e.vat_cents)
    if e.vat_mode == "full":
        return e.vat_cents
    if e.vat_mode == "none":
        return 0
    share = s.affected_share
    if e.vat_mode == "home_property":
        return round_cents(v * share)
    if e.vat_mode == "home_supply":
        if s.vat_home_mode == "none":
            return 0  # the whole invoice, IVA included, goes into the IRPF cost
        factor = s.supplies_factor if s.vat_home_mode == "share_factor" else Decimal(1)
        return round_cents(v * share * factor)
    raise ValueError(f"unknown vat_mode {e.vat_mode!r}")


def irpf_deductible(e: Entry, s: TaxSettings) -> int:
    """Amount of the expense deductible for IRPF (130 / Renta).

    Non-recoverable IVA is part of the expense (cost = base + VAT not deducted).
    """
    if e.kind != "expense":
        return 0
    cost = D(e.base_cents + e.vat_cents - vat_deductible(e, s))
    if e.irpf_mode == "full":
        return round_cents(cost)
    if e.irpf_mode == "none":
        return 0
    if e.irpf_mode == "home_property":
        return round_cents(cost * s.home_share(s.property_rule))
    if e.irpf_mode == "home_supply":
        return round_cents(cost * s.home_share(s.supplies_rule))
    raise ValueError(f"unknown irpf_mode {e.irpf_mode!r}")


def irpf_deductions(entries: Iterable[Entry], s: TaxSettings) -> List[int]:
    """IRPF-deductible amount of every entry, in order, honouring category annual caps.

    Entries must be chronological. A category cap (e.g. health insurance) limits the
    running total per calendar year; once used up, later entries deduct 0.
    """
    used: Dict[tuple, int] = {}
    out: List[int] = []
    for e in entries:
        amount = irpf_deductible(e, s)
        if amount and e.category_cap_cents is not None:
            key = (e.date.year, e.category)
            remaining = max(e.category_cap_cents - used.get(key, 0), 0)
            amount = min(amount, remaining)
            used[key] = used.get(key, 0) + amount
        out.append(amount)
    return out


# ----------------------------------------------------------------- modelo 303

@dataclass
class Vat303:
    year: int
    quarter: int
    # devengado, regimen general: rate -> [base, cuota]
    by_rate: Dict[int, List[int]] = field(default_factory=dict)
    c10: int = 0  # base adquisiciones intracomunitarias
    c11: int = 0  # cuota
    c12: int = 0  # base otras ISP
    c13: int = 0  # cuota
    c14: int = 0  # modificacion de bases (facturas rectificativas emitidas), con signo
    c15: int = 0  # modificacion de cuotas, con signo
    c27: int = 0  # total cuota devengada
    c28: int = 0  # base deducible interiores corrientes
    c29: int = 0  # cuota
    c36: int = 0  # base deducible intracomunitarias corrientes
    c37: int = 0  # cuota
    c40: int = 0  # rectificacion de deducciones: base (facturas rectificativas recibidas), con signo
    c41: int = 0  # rectificacion de deducciones: cuota, con signo
    c45: int = 0  # total a deducir
    c46: int = 0  # resultado regimen general
    c64: int = 0
    c66: int = 0
    c78: int = 0  # compensacion aplicada de periodos anteriores
    c69: int = 0
    c71: int = 0  # resultado de la autoliquidacion
    c72: int = 0  # a compensar (si negativo)
    carry_in: int = 0  # cuotas a compensar pendientes al empezar el periodo (casilla 110)
    carry_out: int = 0  # pendiente de compensar tras este trimestre
    info_eu_services_base: int = 0  # ventas UE B2B (casilla 59, VERIFY)
    info_non_eu_base: int = 0  # ventas fuera UE no sujetas
    info_exempt_base: int = 0  # exentas sin IVA / tipo 0
    warnings: List[Msg] = field(default_factory=list)


def _whole_rate(rate: Decimal) -> Optional[int]:
    """The rate as an int when it is a whole number (21 -> 21), None for 10.5 and the like."""
    return int(rate) if rate == rate.to_integral_value() else None


def build_303(entries: Iterable[Entry], s: TaxSettings, year: int, quarter: int,
              carry_in_cents: int = 0) -> Vat303:
    """Build a quarter's 303 from entries (entries outside the quarter ignored)."""
    r = Vat303(year=year, quarter=quarter, by_rate={4: [0, 0], 10: [0, 0], 21: [0, 0]},
               carry_in=carry_in_cents)
    for e in entries:
        if e.date.year != year or quarter_of(e.date) != quarter:
            continue
        if e.kind == "income":
            if e.regime == "domestic":
                rate = _whole_rate(e.vat_rate)
                if e.base_cents < 0 or e.vat_cents < 0:
                    # credit note / rectification: "modificacion de bases y cuotas" (casillas 14-15)
                    r.c14 += e.base_cents
                    r.c15 += e.vat_cents
                elif rate in r.by_rate:
                    r.by_rate[rate][0] += e.base_cents
                    r.by_rate[rate][1] += e.vat_cents
                elif e.vat_cents != 0:
                    r.warnings.append(Msg("warn.vat_rate_unsupported", name=e.description, rate=e.vat_rate))
                else:
                    r.info_exempt_base += e.base_cents
            elif e.regime == "exempt":
                r.info_exempt_base += e.base_cents
            elif e.regime == "eu_b2b":
                r.info_eu_services_base += e.base_cents
            elif e.regime == "non_eu":
                r.info_non_eu_base += e.base_cents
        else:  # expense
            if e.regime == "eu_reverse":
                r.c10 += e.base_cents
                r.c11 += e.vat_cents
                ded = vat_deductible(e, s)
                r.c36 += e.base_cents
                r.c37 += ded
            elif e.regime == "non_eu_reverse":
                r.c12 += e.base_cents
                r.c13 += e.vat_cents
                ded = vat_deductible(e, s)
                r.c28 += e.base_cents
                r.c29 += ded
            elif e.regime == "domestic" and e.vat_cents != 0:
                ded = vat_deductible(e, s)
                if ded:
                    # base column: proportional base for the deductible part
                    base = e.base_cents if ded == e.vat_cents else round_cents(
                        D(e.base_cents) * D(ded) / D(e.vat_cents))
                    if e.base_cents < 0 or e.vat_cents < 0:
                        # credit note received: "rectificacion de deducciones" (casillas 40-41)
                        r.c40 += base
                        r.c41 += ded
                    else:
                        r.c28 += base
                        r.c29 += ded
    r.c27 = sum(v[1] for v in r.by_rate.values()) + r.c11 + r.c13 + r.c15
    r.c45 = r.c29 + r.c37 + r.c41
    r.c46 = r.c27 - r.c45
    r.c64 = r.c46
    r.c66 = r.c64  # 100% territorio comun
    r.c78 = min(carry_in_cents, max(r.c66, 0))
    r.c69 = r.c66 - r.c78
    r.c71 = r.c69
    if r.c71 < 0:
        r.c72 = -r.c71
    r.carry_out = carry_in_cents - r.c78 + r.c72
    if r.c10:
        r.warnings.append(Msg("warn.349_purchases"))
    if r.info_eu_services_base:
        r.warnings.append(Msg("warn.349_sales"))
    return r


def build_303_year(entries: List[Entry], s: TaxSettings, year: int, upto: int = 4,
                   opening_carry_cents: int = 0) -> List[Vat303]:
    """Sequential 303 reports Q1..upto, carrying negative results forward."""
    out: List[Vat303] = []
    carry = opening_carry_cents
    for q in range(1, upto + 1):
        rep = build_303(entries, s, year, q, carry)
        carry = rep.carry_out
        out.append(rep)
    return out


# ----------------------------------------------------------------- modelo 130

def minoracion_110_3c(prev_year_net_yield_cents: Optional[int]) -> int:
    """Casilla 13 amount from previous year's net yield (<= 12.000 EUR)."""
    if prev_year_net_yield_cents is None:
        return 0
    y = prev_year_net_yield_cents
    if y <= 900_000:
        return 10_000
    if y <= 1_000_000:
        return 7_500
    if y <= 1_100_000:
        return 5_000
    if y <= 1_200_000:
        return 2_500
    return 0


def difficult_provision(net_before_cents: int, s: TaxSettings) -> int:
    """5% of positive net yield, capped (gastos de dificil justificacion)."""
    if not s.difficult_enabled or s.irpf_method != "simplificada" or net_before_cents <= 0:
        return 0
    return min(round_cents(D(net_before_cents) * s.difficult_pct), s.difficult_cap_cents)


@dataclass
class Irpf130:
    year: int
    quarter: int
    income: int = 0
    expenses_gross: int = 0  # deductible expenses before difficult-justification
    difficult: int = 0
    c01: int = 0
    c02: int = 0
    c03: int = 0
    c04: int = 0
    c05: int = 0
    c06: int = 0
    c07: int = 0
    c12: int = 0
    c13: int = 0
    c14: int = 0
    c15: int = 0
    c16: int = 0
    c17: int = 0
    c19: int = 0
    pool_out: int = 0  # negative results still to deduct in later quarters
    warnings: List[Msg] = field(default_factory=list)


def build_130_year(entries: List[Entry], s: TaxSettings, year: int, upto: int = 4) -> List[Irpf130]:
    """Sequential 130 reports Q1..upto (each YTD, linked through 05 and 15)."""
    out: List[Irpf130] = []
    prev_c07_pos_sum = 0
    prev_c16_sum = 0
    pool = 0
    for q in range(1, upto + 1):
        end = quarter_end(year, q)
        ytd = [e for e in entries if e.date.year == year and e.date <= end]
        r = Irpf130(year=year, quarter=q)
        r.income = sum(e.base_cents for e in ytd if e.kind == "income")
        r.expenses_gross = sum(irpf_deductions(ytd, s))
        r.difficult = difficult_provision(r.income - r.expenses_gross, s)
        r.c01 = r.income
        r.c02 = r.expenses_gross + r.difficult
        r.c03 = r.c01 - r.c02
        r.c04 = round_cents(D(max(r.c03, 0)) * s.irpf_rate)
        r.c05 = prev_c07_pos_sum - prev_c16_sum
        r.c06 = sum(e.withholding_cents for e in ytd if e.kind == "income")
        r.c07 = r.c04 - r.c05 - r.c06
        r.c12 = max(r.c07, 0)
        r.c13 = minoracion_110_3c(s.prev_year_net_yield_cents)
        r.c14 = r.c12 - r.c13
        r.c15 = min(pool, max(r.c14, 0))
        r.c16 = 0  # deduccion vivienda habitual (hipoteca pre-2013): not supported
        r.c17 = r.c14 - r.c15 - r.c16
        r.c19 = r.c17
        pool = pool - r.c15 + (-r.c19 if r.c19 < 0 else 0)
        r.pool_out = pool
        prev_c07_pos_sum += max(r.c07, 0)
        prev_c16_sum += r.c16
        out.append(r)
    return out


# ----------------------------------------------------------------- annual

@dataclass
class Annual:
    year: int
    income: int = 0
    expenses_by_category: Dict[str, int] = field(default_factory=dict)
    expenses_total: int = 0
    net_before_difficult: int = 0
    difficult: int = 0
    net_yield: int = 0
    start_reduction: int = 0  # 20 % reduction for starting an activity (art. 32.3 LIRPF), in the Renta only
    net_yield_reduced: int = 0
    withholdings: int = 0
    pagos_fraccionados_calc: int = 0
    vat_year_result: int = 0


START_REDUCTION_RATE = Decimal("0.20")
START_REDUCTION_YIELD_CAP_CENTS = 10_000_000  # the reduction is computed on at most 100 000 EUR of net yield


def start_reduction(net_yield_cents: int, year: int, s: TaxSettings) -> int:
    """The 20 % reduction of the net yield for starting an activity (art. 32.3 LIRPF), in cents.

    Zero unless the user switched it on and `year` is one of the years it applies to; only a positive yield is
    reduced, and only the first 100 000 EUR of it. Whether the user is entitled to it is not checked here.
    """
    if year not in s.start_reduction_years or net_yield_cents <= 0:
        return 0
    return round_cents(D(min(net_yield_cents, START_REDUCTION_YIELD_CAP_CENTS)) * START_REDUCTION_RATE)


# ----------------------------------------------------------------- modelo 115 (retentions on rent)

RENT_CATEGORY_CODES = ("rent", "office_rent")  # expenses whose retention is declared in modelo 115


@dataclass
class Rent115:
    """One quarter of modelo 115: what the user withheld from landlords (not cumulative)."""
    year: int
    quarter: int
    payees: int = 0  # casilla 01, distinct recipients
    base: int = 0  # casilla 02, payments subject to retention
    withheld: int = 0  # casilla 03, retentions
    lines: List[Entry] = field(default_factory=list)
    other_withheld: int = 0  # entries with a retention that are not rent (modelo 111 territory), not included

    @property
    def result(self) -> int:
        """Casilla 05, the amount to pay (casilla 04, a previous return of the same period, is not supported)."""
        return self.withheld


def build_115_year(entries: List[Entry], year: int) -> List[Rent115]:
    """Modelo 115 for the four quarters of a year.

    An expense counts when it carries a retention and its category is a rent category; the entry date is taken
    as the payment date. A recipient is identified by NIF, or by name when there is no NIF.
    """
    quarters = [Rent115(year, q) for q in (1, 2, 3, 4)]
    recipients: List[set] = [set() for _ in quarters]
    for e in entries:
        if e.date.year != year or e.kind != "expense" or e.withholding_cents <= 0:
            continue
        r = quarters[quarter_of(e.date) - 1]
        if e.category_code not in RENT_CATEGORY_CODES:
            r.other_withheld += 1
            continue
        r.base += e.base_cents
        r.withheld += e.withholding_cents
        r.lines.append(e)
        recipients[r.quarter - 1].add((e.vat_id or e.counterparty).strip().upper())
    for r, who in zip(quarters, recipients):
        r.payees = len(who)
    return quarters


def build_annual(entries: List[Entry], s: TaxSettings, year: int, opening_vat_carry: int = 0) -> Annual:
    """Yearly summary for the Renta (rendimientos de actividades economicas)."""
    a = Annual(year=year)
    ye = [e for e in entries if e.date.year == year]
    a.income = sum(e.base_cents for e in ye if e.kind == "income")
    for e, amt in zip(ye, irpf_deductions(ye, s)):
        if e.kind == "expense" and amt:
            a.expenses_by_category[e.category] = a.expenses_by_category.get(e.category, 0) + amt
    a.expenses_total = sum(a.expenses_by_category.values())
    a.net_before_difficult = a.income - a.expenses_total
    a.difficult = difficult_provision(a.net_before_difficult, s)
    a.net_yield = a.net_before_difficult - a.difficult
    a.start_reduction = start_reduction(a.net_yield, year, s)
    a.net_yield_reduced = a.net_yield - a.start_reduction
    a.withholdings = sum(e.withholding_cents for e in ye if e.kind == "income")
    q130 = build_130_year(entries, s, year)
    a.pagos_fraccionados_calc = sum(max(r.c19, 0) for r in q130)
    q303 = build_303_year(entries, s, year, 4, opening_vat_carry)
    a.vat_year_result = sum(r.c71 for r in q303)
    return a
