"""CSV import of entries: parsing, validation and the preview plan (no database writes here).

Every row goes through the same `parse_entry` validation as the entry form, so an imported
entry is exactly what the form would have stored. Foreign-currency rows without a rate get the
ECB rate of the invoice date, like the form's "get rate" button.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from werkzeug.datastructures import MultiDict

from .. import calc, fx
from ..fx import FxError
from ..i18n import t
from ..repositories import CategoryRepository
from ..dates import parse_date
from .forms import parse_entry

MAX_ROWS = 2000
MAX_BYTES = 2 * 1024 * 1024

# canonical column -> accepted header spellings (English names, plus those of the app's own CSV export)
COLUMNS: Dict[str, tuple] = {
    "date": ("date", "fecha"),
    "kind": ("kind", "type", "tipo"),
    "number": ("number", "numero", "invoice_number"),
    "counterparty": ("counterparty", "party", "supplier", "customer", "contraparte"),
    "country": ("country", "pais"),
    "vat_id": ("vat_id", "nif"),
    "description": ("description", "descripcion"),
    "category": ("category", "categoria"),
    "regime": ("regime", "regimen_iva"),
    "base": ("base",),
    "vat_rate": ("vat_rate", "tipo_iva"),
    "vat_amount": ("vat_amount", "cuota_iva"),
    "withholding_rate": ("withholding_rate",),
    "currency": ("currency", "moneda"),
    "orig_amount": ("orig_amount", "importe_divisa"),
    "fx_rate": ("fx_rate", "tipo_cambio"),
    "fx_rate_date": ("fx_rate_date", "fecha_cambio"),
    "fx_source": ("fx_source", "fuente_cambio"),
    "notes": ("notes", "notas"),
}
_ALIAS = {alias: name for name, aliases in COLUMNS.items() for alias in aliases}
TEMPLATE_HEADER = ["date", "kind", "number", "counterparty", "country", "vat_id", "description", "category", "regime",
                   "currency", "orig_amount", "base", "vat_rate", "vat_amount", "notes"]
TEMPLATE_ROWS = [
    ["2026-01-23", "expense", "INV-001", "Example Gestoria SL", "ES", "B12345678", "Monthly fee", "professional",
     "domestic", "EUR", "", "59.00", "21", "12.39", ""],
    ["2026-02-28", "expense", "A-77", "Example Cloud Inc", "US", "", "Subscription", "software",
     "non_eu_reverse", "USD", "20.00", "", "21", "", ""],
]
_KINDS = {"expense": "expense", "gasto": "expense", "income": "income", "ingreso": "income"}


@dataclass
class ImportRow:
    """One data row of the file: what will be stored (if valid) and what the preview shows."""
    index: int  # 1-based position among the file's data rows
    shown: Dict[str, str]
    values: Optional[dict] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duplicate: str = ""  # '' | 'ledger' | 'file'

    @property
    def ok(self) -> bool:
        return not self.errors and self.values is not None

    @property
    def preselected(self) -> bool:
        return self.ok and not self.duplicate

    @property
    def total_cents(self) -> int:
        """What the invoice adds up to (a reverse-charge IVA is self-assessed, not paid)."""
        v = self.values
        return v["base_cents"] + (0 if v["regime"] in calc.REVERSE_REGIMES else v["vat_cents"])


@dataclass
class ImportPlan:
    rows: List[ImportRow] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)  # problems with the file as a whole

    def count(self, state: str) -> int:
        if state == "ok":
            return sum(1 for r in self.rows if r.preselected)
        if state == "duplicate":
            return sum(1 for r in self.rows if r.ok and r.duplicate)
        return sum(1 for r in self.rows if not r.ok)

    def totals(self) -> Dict[str, int]:
        ready = [r for r in self.rows if r.preselected]
        return {"base": sum(r.values["base_cents"] for r in ready), "vat": sum(r.values["vat_cents"] for r in ready),
                "total": sum(r.total_cents for r in ready)}


def decode(data: bytes) -> str:
    """Text of an uploaded file: UTF-8 (with or without BOM), else the Windows code page Excel uses."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def template_csv() -> str:
    """A small example file with the supported columns."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(TEMPLATE_HEADER)
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def _read_table(text: str):
    """(rows as dicts keyed by canonical column, error list). The delimiter is detected from the header line."""
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delimiter = max((";", ",", "\t"), key=first.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        return [], [t("import.err_empty")]
    names = [_ALIAS.get(h.strip().lower().replace(" ", "_")) for h in header]
    missing = []
    if "date" not in names:
        missing.append("date")
    if "base" not in names and "orig_amount" not in names:
        missing.append("base")
    if missing:
        return [], [t("import.err_header", columns=", ".join(missing))]
    rows = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        row = {}
        for name, cell in zip(names, cells):
            if name and name not in row:
                row[name] = cell.strip()
        rows.append(row)
    return rows, []


def _iso_date(text: str) -> str:
    """ISO text for YYYY-MM-DD or DD/MM/YYYY (also with '-' or '.'); anything else is returned unchanged."""
    text = (text or "").strip()
    try:
        return parse_date(text).isoformat()
    except ValueError:
        pass
    for sep in ("/", ".", "-"):
        parts = text.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts) and len(parts[2]) == 4:  # a two-digit year is not guessed
            try:
                return parse_date(f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}").isoformat()
            except ValueError:
                break
    return text


def _find_category(categories, name: str):
    wanted = name.strip().casefold()
    if not wanted:
        return None
    for c in categories:
        if wanted in (c["name"].casefold(), (c["code"] or "").casefold(),
                      CategoryRepository.label(c["code"], c["name"]).casefold()):
            return c
    return None


def _duplicate_key(values) -> tuple:
    """Same kind, date, party, base and invoice number: works on an import row's values and on a database row."""
    return (values["kind"], values["date"], values["counterparty"].casefold(), values["base_cents"],
            values["number"].casefold())


def build_plan(text: str, services) -> ImportPlan:
    """Validate the whole file against the current settings, categories and ledger."""
    plan = ImportPlan()
    if len(text.encode("utf-8")) > MAX_BYTES:
        plan.errors.append(t("import.err_too_big", rows=MAX_ROWS))
        return plan
    table, errors = _read_table(text)
    plan.errors.extend(errors)
    if errors:
        return plan
    if not table:
        plan.errors.append(t("import.err_empty"))
        return plan
    if len(table) > MAX_ROWS:
        plan.errors.append(t("import.err_too_big", rows=MAX_ROWS))
        return plan

    settings = services.settings.load()
    categories = services.categories.all()
    contacts = {(c["kind"], c["name"].casefold()): c for c in services.contacts.all()}
    fx_service = services.fx
    for number, raw in enumerate(table, start=1):
        plan.rows.append(_build_row(number, raw, settings, categories, contacts, fx_service))

    years = {r.values["date"][:4] for r in plan.rows if r.ok}
    in_ledger = {_duplicate_key(row) for y in years for row in services.entries.year_rows(int(y))}
    seen = set()
    for row in plan.rows:
        if not row.ok:
            continue
        key = _duplicate_key(row.values)
        row.duplicate = "ledger" if key in in_ledger else ("file" if key in seen else "")
        seen.add(key)
    return plan


def _build_row(number: int, raw: Dict[str, str], settings, categories, contacts, fx_service) -> ImportRow:
    kind = _KINDS.get((raw.get("kind") or "expense").strip().lower())
    row = ImportRow(number, shown={"date": raw.get("date", ""), "party": raw.get("counterparty", ""),
                                   "number": raw.get("number", ""), "category": raw.get("category", "")})
    if kind is None:
        row.errors.append(t("import.err_kind"))
        return row

    # blank cells come from the saved contact of that name, then from the Settings defaults
    contact = contacts.get((kind, raw.get("counterparty", "").strip().casefold()))

    def known(column: str) -> str:
        return raw.get(column) or ((contact[column] or "") if contact else "")

    form = MultiDict({key: raw.get(key, "") for key in COLUMNS if key not in ("kind", "category")})
    form["date"] = _iso_date(raw.get("date", ""))
    form["country"] = known("country") or "ES"
    form["vat_id"] = known("vat_id")
    form["description"] = known("description")
    form["regime"] = known("regime") or settings.default_regime(kind)
    form["vat_rate"] = known("vat_rate") or calc.fmt_percent(settings.default_vat_rate)
    if kind == "income":
        form["withholding_rate"] = known("withholding_rate") or calc.fmt_percent(settings.default_withholding_rate)
    else:  # a retention the user withholds from a landlord (modelo 115): from the file or the supplier's default
        form["withholding_rate"] = known("withholding_rate")
    form["currency"] = (raw.get("currency") or fx.EUR).upper()

    if kind == "expense":
        category = _find_category(categories, raw.get("category", ""))
        if category is None and not raw.get("category") and contact and contact["category_id"]:
            category = next((c for c in categories if c["id"] == contact["category_id"]), None)
        if category is None:
            row.errors.append(t("import.err_category_unknown", name=raw.get("category", "")) if raw.get("category")
                              else t("err.category"))
        else:
            form["category_id"] = str(category["id"])
            row.shown["category"] = CategoryRepository.label(category["code"], category["name"])

    if form["currency"] != fx.EUR and not form.get("fx_rate") and not row.errors:
        try:
            on = date.fromisoformat(form["date"])
            rate = fx_service.lookup(form["currency"], on)
            form["fx_rate"] = format(rate.per_eur.normalize(), "f")
            form["fx_rate_date"] = rate.rate_date.isoformat()
            form["fx_source"] = rate.source
        except ValueError:
            pass  # the date is reported by parse_entry
        except FxError as exc:
            row.errors.append(str(exc))

    result = parse_entry(form, kind)
    row.errors.extend(result.errors)
    row.warnings.extend(result.warnings)
    if not row.errors:
        row.values = result.values
    return row
