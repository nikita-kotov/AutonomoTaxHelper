"""Glue between the app and the AEAT modules: build the file for a period and
run/record validation checks against the AEAT test service."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from .. import db
from ..reports import TaxReports
from ..repositories import CheckRepository
from ..i18n import UserError
from ..settings import Settings, is_valid_iban
from . import client, model130, model303
from .fixed import Taxpayer

MODELS = ("130", "303")


@dataclass
class BuiltFile:
    model: str
    year: int
    quarter: int
    period: str
    text: str
    tipo: str
    sha256: str
    result_cents: int
    iban: str
    filename: str  # AEAT import name: NIF-year-period.model


def taxpayer_from(settings: Settings) -> Taxpayer:
    tp = Taxpayer(nif=settings.holder_nif.strip().upper(), surname=settings.holder_surname.strip(),
                  first_name=settings.holder_firstname.strip())
    tp.validate()
    return tp


def build_file(settings: Settings, reports: TaxReports, model: str, year: int, quarter: int) -> BuiltFile:
    """Build the flat file for a quarter from the current data. Raises UserError if the profile is incomplete."""
    if model not in MODELS:
        raise ValueError(f"model {model} is not supported")
    tp = taxpayer_from(settings)
    account = settings.holder_iban.replace(" ", "").upper()
    iban = account if settings.uses_direct_debit else ""
    if iban and not is_valid_iban(iban):
        raise UserError("err.iban_invalid")  # the AEAT refuses a malformed IBAN (error 2917)
    if model == "130":
        rep = reports.irpf_year(year, quarter)[-1]
        tipo = model130.declaration_type(rep, iban)
        text, result = model130.build(rep, tp, tipo, iban), rep.c19
    else:
        rep = reports.vat_year(year, quarter)[-1]
        refund = settings.vat_negative_4t == "refund" and quarter == 4 and rep.c71 < 0
        if refund and not account:
            raise UserError("err.iban_refund")
        if refund and not is_valid_iban(account):
            raise UserError("err.iban_invalid")
        tipo = model303.declaration_type(rep, iban, account if refund else "")
        iban = account if tipo == "D" else iban
        text, result = model303.build(rep, tp, tipo, iban), rep.c71
    period = f"{quarter}T"
    return BuiltFile(model=model, year=year, quarter=quarter, period=period, text=text, tipo=tipo,
                     sha256=hashlib.sha256(text.encode("latin-1", "replace")).hexdigest(),
                     result_cents=result, iban=iban, filename=f"{tp.nif}-{year}-{period}.{model}")


def run_check(checks: CheckRepository, built: BuiltFile):
    """Validate the built file with the AEAT test validator and store the outcome."""
    result = client.validate(built.model, built.year, built.period, built.text)
    pdf_name = ""
    if result.pdf:
        pdf_name = f"aeat_{built.model}_{built.year}_{built.period}_{built.sha256[:8]}.pdf"
        (db.documents_dir() / pdf_name).write_bytes(result.pdf)
    previous = checks.latest(built.model, built.year, built.period)
    row = checks.add(model=built.model, year=built.year, period=built.period, file_sha256=built.sha256,
                     tipo=built.tipo, result_cents=built.result_cents, ok=result.ok,
                     errors_json=json.dumps(result.errors, ensure_ascii=False),
                     warnings_json=json.dumps(result.warnings, ensure_ascii=False), pdf=pdf_name,
                     checked_at=datetime.now().strftime("%Y-%m-%d %H:%M"))
    if previous and previous["pdf"] and previous["pdf"] != pdf_name:
        stale = db.documents_dir() / previous["pdf"]
        if stale.is_file():
            stale.unlink()
    return row
