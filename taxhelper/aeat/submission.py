"""Direct submission of modelo 130 and 303 to AEAT ("Presentación Directa", service PresBasicaDos).

Spec: "Especificaciones de los servicios de Presentación Directa, Consulta de Declaraciones Presentadas y
Validación de Impresión" (AEAT, v30). A JSON document with the flat file is POSTed over a TLS connection
authenticated with the presenter's certificate ("Firma No Criptográfica"); the answer is JSON with the
secure verification code (CSV) and the URL of the receipt PDF, or a list of errors. A recorded declaration
cannot be annulled, so every step before the call is a gate and the production address is not enabled yet.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .. import db
from ..i18n import Msg, UserError
from ..reports import TaxReports
from ..repositories import CheckRepository, SubmissionRepository
from ..settings import Settings
from . import client
from . import service as aeat_service
from .service import BuiltFile
from .transport import Certificate, Transport, TransportError, TransportResponse

TEST_URL = "https://prewww1.aeat.es/wlpl/PFTW-PICW/PresBasicaDos"
# Only the test environment is enabled. Declarations sent there have no legal effect (and a successful test
# for a period must be followed by complementary declarations for the same period).
ENVIRONMENTS: Dict[str, str] = {"test": TEST_URL}
TEST_HOURS = (8, 15)  # weekdays, local time of Spain

_NRC = re.compile(r"[A-Za-z0-9]{22}")


def environment_url(env: str) -> str:
    try:
        return ENVIRONMENTS[env]
    except KeyError:
        raise UserError("submit.err_environment") from None


def spain_now() -> datetime:
    """The current time in Spain (the test environment's hours are Spanish); the computer's own time when the
    time zone database is not available."""
    try:
        return datetime.now(ZoneInfo("Europe/Madrid"))
    except ZoneInfoNotFoundError:
        return datetime.now()


def outside_test_hours(now: datetime) -> bool:
    """The test environment answers on working days from 8:00 to 15:00."""
    return now.weekday() >= 5 or not TEST_HOURS[0] <= now.hour < TEST_HOURS[1]


# ----------------------------------------------------------------- request and response

def build_payload(built: BuiltFile, certificate: Certificate, nrc: str = "", lang: str = "ES") -> dict:
    """The JSON document of the request. NRC is only for a declaration with payment (type I)."""
    return {"MODELO": built.model, "EJERCICIO": str(built.year), "PERIODO": built.period,
            "NRC": nrc if built.tipo == "I" else "", "IDI": lang, "F01": client.clean_file(built.text),
            "FIR": "FirmaBasica", "FIRNIF": certificate.nif, "FIRNOMBRE": certificate.name}


@dataclass
class SubmissionResult:
    status: str  # accepted | rejected | error
    csv: str = ""
    fecha: str = ""
    hora: str = ""
    expediente: str = ""
    justificante: str = ""
    pdf_url: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


def _normalised(d: dict) -> dict:
    """Keys without stray spaces and in lower case: the published example has a key with a trailing space."""
    return {str(k).strip().lower(): v for k, v in d.items()}


def _strings(value) -> List[str]:
    return [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value] if isinstance(value, list) else []


def parse_response(response: TransportResponse) -> SubmissionResult:
    """Interpret AEAT's answer: accepted (secure verification code), rejected (list of errors) or unusable."""
    raw = response.text[:2000]
    if 300 <= response.status < 400:  # no certificate (or one AEAT does not accept) sends the browser to an error page
        return SubmissionResult("error", errors=[str(Msg("submit.err_redirect", location=response.location))], raw=raw)
    try:
        data = json.loads(response.text)
    except ValueError:
        return SubmissionResult("error", errors=[str(Msg("submit.err_not_json", status=response.status))], raw=raw)
    body = _normalised(data).get("respuesta") if isinstance(data, dict) else None
    if not isinstance(body, dict):
        return SubmissionResult("error", errors=[str(Msg("aeat.err_unexpected"))], raw=raw)
    body = _normalised(body)
    accepted = body.get("correcta")
    if isinstance(accepted, dict):
        fields = _normalised(accepted)
        csv = str(fields.get("codigoseguroverificacion") or "").strip()
        if csv:
            return SubmissionResult(
                "accepted", csv=csv, fecha=str(fields.get("fecha") or ""), hora=str(fields.get("hora") or ""),
                expediente=str(fields.get("expediente") or ""), justificante=str(fields.get("justificante") or ""),
                pdf_url=str(fields.get("urlpdf") or ""),
                warnings=_strings(fields.get("avisos")) + _strings(fields.get("advertencias")), raw=raw)
    errors = _strings(body.get("errores"))
    if errors:
        return SubmissionResult("rejected", errors=errors, raw=raw)
    return SubmissionResult("error", errors=[str(Msg("aeat.err_unexpected"))], raw=raw)


# ----------------------------------------------------------------- gates before sending

@dataclass
class Preflight:
    """Everything the person must see and the program must have verified before a declaration is sent."""
    built: BuiltFile
    certificate: Optional[Certificate]
    issues: List[Msg] = field(default_factory=list)
    needs_nrc: bool = False
    outside_hours: bool = False

    @property
    def can_send(self) -> bool:
        return not self.issues


def preflight(*, transport: Transport, settings: Settings, reports: TaxReports, checks: CheckRepository,
              model: str, year: int, quarter: int, now: Optional[datetime] = None) -> Preflight:
    """Build the file and collect what blocks the submission. Raises UserError when the file cannot be built."""
    built = aeat_service.build_file(settings, reports, model, year, quarter)
    now = now or spain_now()
    pre = Preflight(built=built, certificate=None, needs_nrc=built.tipo == "I", outside_hours=outside_test_hours(now))

    thumbprint = settings.aeat_certificate
    certificate = next((c for c in transport.certificates() if c.thumbprint == thumbprint), None) if thumbprint else None
    pre.certificate = certificate
    if certificate is None:
        pre.issues.append(Msg("submit.issue_no_certificate"))
    else:
        if certificate.expired(now.date()):
            pre.issues.append(Msg("submit.issue_expired", date=certificate.not_after))
        if certificate.nif != settings.holder_nif.strip().upper():
            pre.issues.append(Msg("submit.issue_nif", certificate=certificate.nif or "?", taxpayer=settings.holder_nif))
    check = checks.latest(model, year, built.period)
    if not check or not check["ok"] or check["file_sha256"] != built.sha256:
        pre.issues.append(Msg("submit.issue_not_checked"))
    return pre


def submit(*, transport: Transport, settings: Settings, reports: TaxReports, checks: CheckRepository,
           submissions: SubmissionRepository, model: str, year: int, quarter: int, nrc: str = "",
           env: str = "test", expected_sha: Optional[str] = None, now: Optional[datetime] = None):
    """Send the declaration and record the outcome. Returns the stored submission row.

    `expected_sha` is the checksum of the file the person reviewed: if the data changed since, nothing is sent.
    Raises UserError when a gate blocks it (the caller shows the message) and TransportError when the call
    could not be made, in which case nothing is recorded.
    """
    url = environment_url(env)
    pre = preflight(transport=transport, settings=settings, reports=reports, checks=checks, model=model, year=year,
                    quarter=quarter, now=now)
    if expected_sha is not None and pre.built.sha256 != expected_sha:
        raise UserError("submit.flash_changed")
    if not pre.can_send:
        raise UserError("submit.err_blocked")
    nrc = nrc.strip()
    if pre.needs_nrc and not _NRC.fullmatch(nrc):
        raise UserError("submit.err_nrc")
    built, certificate = pre.built, pre.certificate
    response = transport.post_json(url, build_payload(built, certificate, nrc), certificate.thumbprint)
    result = parse_response(response)

    pdf_name = ""
    if result.accepted and result.pdf_url.startswith("https://"):
        pdf = _download_pdf(transport, result.pdf_url, certificate.thumbprint)
        if pdf:
            code = re.sub(r"[^A-Za-z0-9]", "", result.csv)[:8]  # the answer is not trusted to make a safe file name
            pdf_name = f"aeat_{env}_{built.model}_{built.year}_{built.period}_{code}.pdf"
            (db.documents_dir() / pdf_name).write_bytes(pdf)
    return submissions.add(
        env=env, model=built.model, year=built.year, period=built.period, file_sha256=built.sha256, tipo=built.tipo,
        result_cents=built.result_cents, status=result.status, csv=result.csv, expediente=result.expediente,
        justificante=result.justificante, fecha=result.fecha, hora=result.hora,
        errors_json=json.dumps(result.errors, ensure_ascii=False), warnings_json=json.dumps(result.warnings, ensure_ascii=False),
        pdf=pdf_name, presenter_nif=certificate.nif, submitted_at=(now or spain_now()).strftime("%Y-%m-%d %H:%M"))


def _download_pdf(transport: Transport, url: str, thumbprint: str) -> Optional[bytes]:
    """The receipt PDF, if AEAT has it ready (in the test environment it may be missing)."""
    try:
        response = transport.get(url, thumbprint)
    except TransportError:  # the declaration is recorded either way; a missing receipt must not hide that
        return None
    return response.body if response.status == 200 and response.body[:4] == b"%PDF" else None
