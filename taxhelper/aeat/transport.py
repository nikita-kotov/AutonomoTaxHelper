"""How the app calls AEAT's authenticated services.

AEAT identifies the presenter by a TLS client certificate. The certificate of a Spanish citizen lives in
the Windows store with a non-exportable private key, which Python's `ssl` module cannot use; a small
PowerShell helper (SChannel) signs the handshake instead. Tests replace the transport with a fake one.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional, Protocol, Tuple

from ..i18n import Msg

HERE = Path(__file__).resolve().parent
LIST_CERTS_SCRIPT = HERE / "list_certs.ps1"
POST_SCRIPT = HERE / "post_json.ps1"

_ID_PREFIXES = "IDCES|VATES|PNOES|TINES"


@dataclass
class Certificate:
    """A client certificate found in the Windows store, with the holder read from its subject."""
    thumbprint: str
    subject: str
    not_before: str
    not_after: str
    nif: str = ""
    name: str = ""

    def expired(self, today: Optional[date] = None) -> bool:
        return self.not_after < (today or date.today()).isoformat()


@dataclass
class TransportResponse:
    status: int
    body: bytes
    location: str = ""
    content_type: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


class TransportError(Exception):
    """The call could not be made or answered; the message is meant for the user."""

    def __init__(self, key: str, **params):
        self.msg = Msg(key, **params)
        super().__init__(key)

    def __str__(self) -> str:
        return str(self.msg)


class Transport(Protocol):
    def certificates(self) -> List[Certificate]: ...

    def post_json(self, url: str, payload: dict, thumbprint: str, timeout: int = 90) -> TransportResponse: ...

    def get(self, url: str, thumbprint: str, timeout: int = 90) -> TransportResponse: ...


def parse_subject(subject: str) -> Tuple[str, str]:
    """(NIF, 'SURNAMES NAME') of the holder of a Spanish certificate, from its subject text.

    FNMT person certificates read e.g. `CN=SURNAME NAME - 12345678Z, SN=SURNAME, G=NAME, SERIALNUMBER=IDCES-12345678Z`.
    """
    nif = ""
    match = re.search(rf"SERIALNUMBER=(?:{_ID_PREFIXES})-([A-Z0-9]+)", subject, re.I)
    if match:
        nif = match.group(1).upper()
    surname = re.search(r"(?:^|,\s*)SN=([^,]+)", subject)
    given = re.search(r"(?:^|,\s*)G=([^,]+)", subject)
    if surname and given:
        return nif, f"{surname.group(1).strip()} {given.group(1).strip()}"
    cn = re.search(r"CN=([^,]+)", subject)
    name = cn.group(1).strip() if cn else ""
    tail = re.search(r"\s*-\s*(?:NIF\s+)?([A-Z0-9]{9})$", name, re.I)
    if tail:
        nif = nif or tail.group(1).upper()
        name = name[:tail.start()].strip()
    return nif, name


def certificate_from(row: dict) -> Certificate:
    nif, name = parse_subject(row.get("subject", ""))
    return Certificate(thumbprint=str(row.get("thumbprint", "")).upper(), subject=row.get("subject", ""),
                       not_before=row.get("notBefore", ""), not_after=row.get("notAfter", ""), nif=nif, name=name)


class WindowsStoreTransport:
    """Calls through PowerShell and the Windows certificate store (Windows only)."""

    def __init__(self, runner=subprocess.run, powershell: str = "powershell.exe"):
        self._run = runner
        self._powershell = powershell

    def _powershell_json(self, script: Path, args: List[str], timeout: int):
        if sys.platform != "win32" and self._run is subprocess.run:
            raise TransportError("submit.err_platform")
        command = [self._powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
        try:
            proc = self._run(command, capture_output=True, timeout=timeout,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError("submit.err_powershell", detail=str(exc)) from exc
        out = proc.stdout.decode("utf-8-sig", "replace").strip()
        try:
            return json.loads(out)
        except ValueError as exc:
            detail = (proc.stderr.decode("utf-8", "replace") or out)[:300]
            raise TransportError("submit.err_powershell", detail=detail) from exc

    def certificates(self) -> List[Certificate]:
        data = self._powershell_json(LIST_CERTS_SCRIPT, [], timeout=60)
        if isinstance(data, dict):  # a single object when PowerShell unwraps a one-item array
            data = [data]
        if not isinstance(data, list):
            raise TransportError("submit.err_powershell", detail=str(data)[:300])
        return [certificate_from(row) for row in data if isinstance(row, dict)]

    def _call(self, url: str, thumbprint: str, method: str, body_file: str, timeout: int) -> TransportResponse:
        args = ["-Url", url, "-Thumbprint", thumbprint, "-Method", method, "-TimeoutSec", str(timeout)]
        if body_file:
            args += ["-BodyFile", body_file]
        data = self._powershell_json(POST_SCRIPT, args, timeout=timeout + 60)
        if not isinstance(data, dict) or "error" in data:
            raise TransportError("submit.err_transport", detail=str(data.get("error") if isinstance(data, dict) else data)[:300])
        try:
            body = base64.b64decode(data.get("body", ""))
        except ValueError as exc:
            raise TransportError("submit.err_transport", detail="unreadable response") from exc
        return TransportResponse(status=int(data.get("status", 0)), body=body, location=data.get("location") or "",
                                 content_type=data.get("contentType") or "")

    def post_json(self, url: str, payload: dict, thumbprint: str, timeout: int = 90) -> TransportResponse:
        handle, path = tempfile.mkstemp(suffix=".json", prefix="taxhelper-")
        try:
            with os.fdopen(handle, "wb") as f:  # the declaration is financial data: it lives in this file only briefly
                f.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            return self._call(url, thumbprint, "POST", path, timeout)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def get(self, url: str, thumbprint: str, timeout: int = 90) -> TransportResponse:
        return self._call(url, thumbprint, "GET", "", timeout)
