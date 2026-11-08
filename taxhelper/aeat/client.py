"""Client for the AEAT validation/print service ("ServValiDos", test environment).

The service is a pre-check: it takes a declaration file without any certificate, verifies it
against the official layout and rules, and returns a PDF of the pre-filled form. Filing is a
separate step.
Spec: "Especificaciones de los servicios de Presentacion Directa, Consulta de
Declaraciones Presentadas y Validacion de Impresion" (AEAT, section 3).
"""
from __future__ import annotations

import base64
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..i18n import Msg

VALIDATION_URL = "https://prewww2.aeat.es/wlpl/PFTW-PICW/ServValiDos"


@dataclass
class ValidationResult:
    """Outcome of one validation call."""
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    pdf: Optional[bytes] = None


def clean_file(content: str) -> str:
    """The service rejects CR/LF/TAB inside the flat file; strip them."""
    return content.replace("\r", "").replace("\n", "").replace("\t", "")


def build_payload(model: str, year: int, period: str, content: str, lang: str = "ES",
                  print_only: bool = False) -> dict:
    payload = {"MODELO": model, "EJERCICIO": str(year), "PERIODO": period, "IDI": lang,
               "F01": clean_file(content)}
    if print_only:
        payload["SINVL"] = ""
    return payload


def lower_keys(d: dict) -> dict:
    """The dict with its keys trimmed and in lower case: the services mix RESPUESTA/respuesta, and a published
    example has a key with a trailing space."""
    return {str(k).strip().lower(): v for k, v in d.items()}


def texts(value) -> List[str]:
    """The service lists messages as strings; anything else is shown as text so nothing is lost."""
    if not isinstance(value, list):
        return []
    return [v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for v in value]


def parse_response(text: str) -> ValidationResult:
    """Interpret the JSON returned by ServValiDos."""
    try:
        data = json.loads(text)
    except ValueError:
        return ValidationResult(False, errors=[str(Msg("aeat.err_not_json"))])
    body = lower_keys(data).get("respuesta") if isinstance(data, dict) else None
    if not isinstance(body, dict):
        return ValidationResult(False, errors=[str(Msg("aeat.err_unexpected"))])
    body = lower_keys(body)
    errors, warnings = texts(body.get("errores")), texts(body.get("avisos"))
    pdf_parts = body.get("pdf") or []
    pdf = None
    if pdf_parts:
        try:
            pdf = base64.b64decode("".join(pdf_parts))
        except (ValueError, TypeError):
            errors.append(str(Msg("aeat.err_pdf_corrupt")))
    if not errors and pdf is None:
        # a success reply always carries the pre-filled PDF; without it there is nothing to show the user
        errors.append(str(Msg("aeat.err_no_pdf")))
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, pdf=pdf)


def validate(model: str, year: int, period: str, content: str, lang: str = "ES",
             print_only: bool = False, timeout: int = 60, url: str = VALIDATION_URL) -> ValidationResult:
    """Send the flat file to the AEAT validator. Network errors become a failed result."""
    body = json.dumps(build_payload(model, year, period, content, lang, print_only)).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json;charset=UTF-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return ValidationResult(False, errors=[str(Msg("aeat.err_http", code=exc.code))])
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        return ValidationResult(False, errors=[str(Msg("aeat.err_network", detail=exc))])
    return parse_response(text)


# ----------------------------------------------------------------- books (libros registro) validation

LIBROS_VALIDATION_URL = "https://www2.agenciatributaria.gob.es/wlpl/PACM-SERV/ServletValidarLLRSI"


@dataclass
class LibrosValidation:
    """Verdict of AEAT's books validation service ("Servicio de validación de ficheros de LLR")."""
    ok: bool
    message: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    info: List[str] = field(default_factory=list)
    periods: Dict[str, List[str]] = field(default_factory=dict)  # accepted periods by use: iva, renta


def _periods(value) -> Dict[str, List[str]]:
    if not isinstance(value, dict):
        return {}
    return {str(k): [str(p) for p in v] for k, v in value.items() if isinstance(v, list)}


def _decode(data: bytes) -> str:
    """The books service answers in UTF-8 or in a Western Windows code page; accents must survive either way."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def parse_libros_response(text: str) -> LibrosValidation:
    """Interpret the JSON of the books validator: {resultado: {valido, mensaje}, errores, avisos, informacion}."""
    try:
        data = json.loads(text)
    except ValueError:
        return LibrosValidation(False, errors=[str(Msg("aeat.err_not_json"))])
    result = data.get("resultado") if isinstance(data, dict) else None
    if not isinstance(result, dict) or "valido" not in result:
        return LibrosValidation(False, errors=[str(Msg("aeat.err_unexpected"))])
    valid = result["valido"] is True or str(result["valido"]).strip().lower() == "true"
    return LibrosValidation(ok=valid, message=str(result.get("mensaje") or ""),
                            errors=texts(data.get("errores")), warnings=texts(data.get("avisos")),
                            info=texts(data.get("informacion")), periods=_periods(data.get("periodosvalidos")))


def validate_libros(content: bytes, year: int, timeout: int = 120, url: str = LIBROS_VALIDATION_URL) -> LibrosValidation:
    """Send the XLSX books to the AEAT validator (no certificate). Network errors become a failed result."""
    body = urllib.parse.urlencode({"EJER": str(year), "FIC": base64.b64encode(content).decode("ascii")}).encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = _decode(resp.read())
    except urllib.error.HTTPError as exc:
        return LibrosValidation(False, errors=[str(Msg("aeat.err_http", code=exc.code))])
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        return LibrosValidation(False, errors=[str(Msg("aeat.err_network", detail=exc))])
    return parse_libros_response(text)
