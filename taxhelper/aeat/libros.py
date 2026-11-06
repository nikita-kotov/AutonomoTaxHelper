"""The AEAT unified books of invoices and income/expenses (IVA + IRPF) as an XLSX workbook.

Renta WEB, the modelo 130 form and Pre303 can import this one file ("Importar Libros Registro") and
fill their casillas from it. Format: "Formatos de los Libros Registro de IVA e IRPF", specifications
2025-2026 (AEAT, V.18.02.2026), unified books: sheets EXPEDIDAS_INGRESOS and RECIBIDAS_GASTOS, three
header rows, one line per invoice (or per IVA rate), file name <year><NIF><T><name>.xlsx.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import List, Sequence, Tuple

from .. import calc, i18n
from ..i18n import Msg, UserError
from ..repositories import EntryRepository
from ..settings import Settings
from . import libros_layout as layout
from .xlsx import Cell, Sheet, write_xlsx

SHEET_INCOME = "EXPEDIDAS_INGRESOS"
SHEET_EXPENSE = "RECIBIDAS_GASTOS"
BOOK_TYPE = "T"  # all unified books of IVA and IRPF in one file

EU_COUNTRIES = frozenset("AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI SE".split())

# category code -> "Concepto de Gasto" (AEAT list); other categories follow their IRPF mode
EXPENSE_CONCEPTS = {
    "software": "G22", "professional": "G19", "reta": "G45", "health": "G37", "mobile": "G17",
    "electricity": "G14", "gas": "G16", "water": "G15", "internet": "G17", "rent": "G12", "office_rent": "G12", "ibi": "G26",
    "equipment": "G03", "office": "G22", "marketing": "G22", "training": "G22", "transport": "G22",
    "bank": "G24", "other": "G37",
}
MODE_CONCEPTS = {"home_property": "G12", "home_supply": "G18"}
DEFAULT_CONCEPT = "G37"
INCOME_CONCEPT = "I01"

INCOME_COLUMNS = (
    "year period act_code act_type iae tipo_factura concepto computable f_exp f_op serie numero numero_final "
    "nif_tipo nif_pais nif_id nombre clave calif exenta total base tipo_iva cuota tipo_rec cuota_rec "
    "cobro_fecha cobro_importe cobro_medio cobro_id ret_tipo ret_importe acuerdo inm_sit inm_ref ref_ext").split()
EXPENSE_COLUMNS = (
    "year period act_code act_type iae tipo_factura concepto gasto f_exp f_op numero numero_final f_rec rec_num "
    "rec_num_final nif_tipo nif_pais nif_id nombre clave bien_inversion isp posterior ded_year ded_period total base "
    "tipo_iva cuota cuota_deducible tipo_rec cuota_rec pago_fecha pago_importe pago_medio pago_id ret_tipo ret_importe "
    "acuerdo inm_sit inm_ref ref_ext").split()
assert len(INCOME_COLUMNS) == len(layout.INCOME_HEADER[0]) and len(EXPENSE_COLUMNS) == len(layout.EXPENSE_HEADER[0])

_NAME_LIMIT, _NUMBER_LIMIT, _INCOME_NUMBER_LIMIT, _ID_LIMIT = 40, 40, 20, 20
_FILE_FORBIDDEN = re.compile(r'[<>:"/\\|?*]')


@dataclass
class BooksFile:
    """The generated workbook plus what a person should know before importing it."""
    filename: str
    content: bytes
    year: int
    upto: int
    income_lines: int = 0
    expense_lines: int = 0
    income_total_cents: int = 0
    expense_deductible_cents: int = 0
    warnings: List[Msg] = field(default_factory=list)


# ----------------------------------------------------------------- helpers

def _eur(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def _quarter_label(d: date) -> str:
    return f"{calc.quarter_of(d)}T"


def _activity(settings: Settings) -> Tuple[str, str, str]:
    """(code, type, IAE group/epigraph) of the activity for the 'Actividad' columns."""
    key = settings.activity_code
    code, kind = key[0], key[1:]
    # the books want the group or epigraph without the section prefix of the census ("2*763" -> "763")
    epigraph = re.sub(r"^\d\*", "", settings.iae_epigraph.strip())
    return code, kind, (epigraph if code == "A" else "")


def _clip(text: str, limit: int, label: str, what: str, warnings: List[Msg]) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        warnings.append(Msg("libros.warn_clip", entry=label, field=what, n=limit))
        return text[:limit]
    return text


# A supplier outside Spain that prints no tax ID on its invoices: the books still need an identification, and this
# placeholder is what gestors use for it.
PLACEHOLDER_ID = "9999999"


def _party(country: str, vat_id: str, regime: str, label: str, warnings: List[Msg]) -> Tuple[str, str, str]:
    """(identification type, country code, identification) of the other party."""
    country = (country or "ES").strip().upper()[:2] or "ES"
    raw = (vat_id or "").strip()
    if not raw:
        warnings.append(Msg("libros.warn_party", entry=label))
        return "", "" if country == "ES" else country, ""
    if raw == PLACEHOLDER_ID:
        warnings.append(Msg("libros.warn_placeholder", entry=label))
    if country == "ES":  # a Spanish NIF carries no type or country: those columns identify foreign parties
        return "", "", re.sub(r"[\s.\-/]", "", raw).upper()
    if country in EU_COUNTRIES and regime in ("eu_b2b", "eu_reverse"):
        prefix = "EL" if country == "GR" else country
        cleaned = re.sub(r"[\s.\-/]", "", raw).upper()
        return "02", country, cleaned if cleaned.startswith(prefix) else prefix + cleaned
    return "04", country, _clip(raw, _ID_LIMIT, label, "NIF", warnings)


def _iva_lines(base_cents: int, vat_cents: int, rate: Decimal) -> Tuple[List[Tuple[int, Decimal, int]], bool]:
    """Lines (base, rate, IVA) with IVA == base x rate, as the books require.

    An invoice whose IVA covers only part of the amount (water with a waste tax, say) becomes two lines. The flag
    is True when the amounts cannot be made consistent.
    """
    sign = -1 if base_cents < 0 else 1  # a credit note has negative amounts throughout
    base, vat = abs(base_cents), abs(vat_cents)
    expected = calc.round_cents(Decimal(base) * rate / 100)
    if rate == 0 or abs(expected - vat) <= 1:
        return [(base_cents, rate, vat_cents)], False
    taxed = calc.round_cents(Decimal(vat) * 100 / rate)
    if 0 < taxed < base:
        return [(sign * taxed, rate, vat_cents), (sign * (base - taxed), Decimal(0), 0)], False
    return [(base_cents, rate, vat_cents)], True


def _allocate(total_cents: int, weights: Sequence[int]) -> List[int]:
    """Split an amount over lines in proportion to their bases; the last line takes the rounding remainder."""
    whole = sum(weights) or 1
    parts = [calc.round_cents(Decimal(total_cents) * w / whole) for w in weights[:-1]]
    return parts + [total_cents - sum(parts)]


def _line(columns: Sequence[str], **values: Cell) -> List[Cell]:
    unknown = set(values) - set(columns)
    if unknown:  # a mistyped column name would silently drop a value from the book
        raise ValueError(f"unknown book columns: {sorted(unknown)}")
    return [values.get(name) for name in columns]


def _invoice_type(base_cents: int, normal: str) -> str:
    """F1/F6 for ordinary lines, R1 (rectifying invoice, by differences) for a credit note."""
    return "R1" if base_cents < 0 else normal


def _label(row) -> str:
    return f"{row['date']} {row['counterparty'] or row['description'] or ''}".strip()[:60]


def _check_profile(settings: Settings) -> None:
    if not (settings.holder_nif.strip() and settings.holder_surname.strip() and settings.holder_firstname.strip()):
        raise UserError("libros.err_taxpayer")
    code = settings.activity_code
    if code[0] == "A" and not settings.iae_epigraph.strip():
        raise UserError("libros.err_iae")


# ----------------------------------------------------------------- lines of each book

def _income_lines(row, settings: Settings, warnings: List[Msg]) -> List[List[Cell]]:
    label = _label(row)
    on = date.fromisoformat(row["date"])
    act_code, act_type, iae = _activity(settings)
    nif_tipo, nif_pais, nif_id = _party(row["country"], row["vat_id"], row["regime"], label, warnings)
    number = _clip(row["number"], _INCOME_NUMBER_LIMIT, label, "number", warnings)
    if not number:
        warnings.append(Msg("libros.warn_number", entry=label))
    calif, exenta = {"domestic": ("S1", ""), "exempt": ("", "E6")}.get(row["regime"], ("N2", ""))
    rate = Decimal(row["vat_rate"] or "0") if row["regime"] == "domestic" else Decimal(0)
    vat = row["vat_cents"] if row["regime"] == "domestic" else 0
    lines, inconsistent = _iva_lines(row["base_cents"], vat, rate)
    if inconsistent:
        warnings.append(Msg("libros.warn_iva", entry=label))
    withholding = row["withholding_cents"] > 0
    name = _clip(row["counterparty"], _NAME_LIMIT, label, "name", warnings) or None
    out = []
    for position, (base, line_rate, line_vat) in enumerate(lines):
        out.append(_line(
            INCOME_COLUMNS, year=on.year, period=_quarter_label(on), act_code=act_code, act_type=act_type or None,
            iae=iae or None, tipo_factura=_invoice_type(row["base_cents"], "F1"), concepto=INCOME_CONCEPT, computable=_eur(base), f_exp=on, f_op=on,
            numero=number or None, nif_tipo=nif_tipo or None, nif_pais=nif_pais or None, nif_id=nif_id or None,
            nombre=name, clave="01",
            calif=calif or None, exenta=exenta or None, total=_eur(base + line_vat), base=_eur(base),
            tipo_iva=line_rate.quantize(Decimal("0.01")), cuota=_eur(line_vat),
            ret_tipo=(Decimal(row["withholding_rate"] or "0").quantize(Decimal("0.01")) if withholding and position == 0 else None),
            ret_importe=(_eur(row["withholding_cents"]) if withholding and position == 0 else None)))
    return out


def _expense_concept(row, deductible_cents: int) -> str:
    if deductible_cents == 0:
        return ""
    return EXPENSE_CONCEPTS.get(row["category_code"]) or MODE_CONCEPTS.get(row["irpf_mode"], DEFAULT_CONCEPT)


def _expense_lines(row, irpf_cents: int, vat_deductible_cents: int, reception: int, settings: Settings,
                   warnings: List[Msg]) -> Tuple[List[List[Cell]], int]:
    """(lines of the entry, deductible expense written in them)."""
    label = _label(row)
    on = date.fromisoformat(row["date"])
    act_code, act_type, iae = _activity(settings)
    regime = row["regime"]
    nif_tipo, nif_pais, nif_id = _party(row["country"], row["vat_id"], regime, label, warnings)
    number = _clip(row["number"], _NUMBER_LIMIT, label, "number", warnings)
    has_vat = regime != "no_vat"
    if has_vat and not number:
        warnings.append(Msg("libros.warn_number", entry=label))
    # F6: accounting voucher (a fee, a rent receipt) without IVA; R1 for a credit note
    tipo_factura = _invoice_type(row["base_cents"], "F1" if has_vat else "F6")
    concept = _expense_concept(row, irpf_cents)
    reverse = regime in calc.REVERSE_REGIMES
    rate = Decimal(row["vat_rate"] or "0") if has_vat else Decimal(0)
    base_cents = row["base_cents"]
    lines, inconsistent = _iva_lines(base_cents, row["vat_cents"] if has_vat else 0, rate)
    if inconsistent:
        warnings.append(Msg("libros.warn_iva", entry=label))
    if concept and abs(irpf_cents) > abs(base_cents):
        # the books never accept more deductible expense than the base: the rest (non-recoverable IVA) goes by hand
        warnings.append(Msg("libros.warn_gasto", entry=label, lost=i18n.format_money(abs(irpf_cents) - abs(base_cents))))
        irpf_cents = base_cents
    expense_parts = _allocate(irpf_cents, [b for b, _, _ in lines]) if concept else [0] * len(lines)
    vat_parts = _allocate(vat_deductible_cents, [b for b, _, _ in lines])
    name = _clip(row["counterparty"], _NAME_LIMIT, label, "name", warnings) or None
    out = []
    for (base, line_rate, line_vat), expense, deductible_vat in zip(lines, expense_parts, vat_parts):
        values = dict(
            year=on.year, period=_quarter_label(on), act_code=act_code, act_type=act_type or None, iae=iae or None,
            tipo_factura=tipo_factura, concepto=concept or None, gasto=_eur(expense) if concept else None,
            f_exp=on, f_op=on, numero=number or None, f_rec=on, rec_num=str(reception),
            nif_tipo=nif_tipo or None, nif_pais=nif_pais or None, nif_id=nif_id or None,
            nombre=name,
            clave="09" if regime == "eu_reverse" else "01", bien_inversion="N",
            isp="S" if regime == "non_eu_reverse" else "N", posterior="N",
            total=_eur(base if reverse else base + line_vat), base=_eur(base))
        if has_vat:
            values.update(tipo_iva=line_rate.quantize(Decimal("0.01")), cuota=_eur(line_vat),
                          cuota_deducible=_eur(deductible_vat))
        out.append(_line(EXPENSE_COLUMNS, **values))
    return out, sum(expense_parts)


# ----------------------------------------------------------------- the workbook

def _widths(header: Sequence[Sequence[str]]) -> List[float]:
    return [max(11.0, min(30.0, max(len(line) for part in col for line in part.split("\n")) * 1.1 + 2))
            for col in zip(*header)]


def build_books(settings: Settings, rows: Sequence, year: int, upto: int = 4) -> BooksFile:
    """The unified books of `year`, from January 1 to the end of quarter `upto`, from the ledger rows.

    `rows` are the year's entries oldest first (EntryRepository.year_rows). Raises UserError when the taxpayer
    data or the IAE epigraph needed for the books is missing in the settings.
    """
    _check_profile(settings)
    tax = settings.tax()
    entries = [EntryRepository.to_entry(r) for r in rows]
    irpf = calc.irpf_deductions(entries, tax)  # category caps depend on the whole year in order
    last_day = calc.quarter_end(year, upto).isoformat()

    warnings: List[Msg] = []
    income, expense = [], []
    reception = 0  # running receipt number of the received invoices and vouchers
    income_total = expense_total = 0
    for row, entry, irpf_cents in zip(rows, entries, irpf):
        if row["date"] > last_day:
            continue
        if row["kind"] == "income":
            income.extend(_income_lines(row, settings, warnings))
            income_total += row["base_cents"]
            continue
        reception += 1
        lines, written = _expense_lines(row, irpf_cents, calc.vat_deductible(entry, tax), reception, settings, warnings)
        expense.extend(lines)
        expense_total += written

    sheets = [
        Sheet(SHEET_INCOME, [*layout.INCOME_HEADER, *income], header_rows=3, merges=layout.INCOME_MERGES,
              widths=_widths(layout.INCOME_HEADER)),
        Sheet(SHEET_EXPENSE, [*layout.EXPENSE_HEADER, *expense], header_rows=3, merges=layout.EXPENSE_MERGES,
              widths=_widths(layout.EXPENSE_HEADER)),
    ]
    name = f"{settings.holder_surname.strip()}, {settings.holder_firstname.strip()}"
    filename = _FILE_FORBIDDEN.sub("", f"{year}{settings.holder_nif.strip().upper()}{BOOK_TYPE}{name}.xlsx")
    return BooksFile(filename=filename, content=write_xlsx(sheets), year=year, upto=upto, income_lines=len(income),
                     expense_lines=len(expense), income_total_cents=income_total,
                     expense_deductible_cents=expense_total, warnings=warnings)
