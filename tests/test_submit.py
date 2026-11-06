"""Submission to the AEAT test environment: transport, request, answer, gates and pages (all offline)."""
import json
import subprocess
from datetime import datetime

import pytest

from taxhelper import db
from taxhelper.aeat import client as aeat_client
from taxhelper.aeat import submission
from taxhelper.aeat.transport import (Certificate, TransportError, TransportResponse, WindowsStoreTransport,
                                      parse_subject)
from tests.support import set_settings, text

THUMB = "AB" * 20
CERT = Certificate(thumbprint=THUMB, subject="CN=GARCIA LOPEZ ANA - X1234567L", not_before="2025-01-01",
                   not_after="2029-01-01", nif="X1234567L", name="GARCIA LOPEZ ANA")
OK_ANSWER = {"respuesta": {"correcta": {"CodigoSeguroVerificacion ": "ABCDEF0123456789", "Fecha": "2026-11-06",
                                         "Hora": "10:30", "Expediente": "2026111111111111", "Justificante": "1307000000001",
                                         "urlPdf": "https://www2.aeat.example/pdf/1", "avisos": ["20932 - revise"]}}}


class FakeTransport:
    def __init__(self, certs=(CERT,), post=None, pdf=b"%PDF-1.4 fake"):
        self.certs, self.pdf, self.calls = list(certs), pdf, []
        self.post = post if post is not None else TransportResponse(200, json.dumps(OK_ANSWER).encode())

    def certificates(self):
        return self.certs

    def post_json(self, url, payload, thumbprint, timeout=90):
        self.calls.append((url, payload, thumbprint))
        if isinstance(self.post, Exception):
            raise self.post
        return self.post

    def get(self, url, thumbprint, timeout=90):
        return TransportResponse(200 if self.pdf else 404, self.pdf or b"")


@pytest.fixture()
def transport(fresh_app):
    fake = FakeTransport()
    fresh_app.config["AEAT_TRANSPORT"] = fake
    return fake


@pytest.fixture()
def ready(client, transport, monkeypatch):
    """A taxpayer with a 130 for 1T, the certificate chosen and the file validated in the AEAT service."""
    set_settings(holder_nif="X1234567L", holder_surname="Garcia Lopez", holder_firstname="Ana", payment_method="nrc",
                 aeat_certificate=THUMB)
    client.post("/entries/new/income", data=dict(date="2026-02-05", counterparty="Cliente", country="ES", regime="domestic",
                                                  base="1000", vat_rate="21", withholding_rate="0", currency="EUR"))
    monkeypatch.setattr(aeat_client, "validate", lambda *a, **k: aeat_client.ValidationResult(True, pdf=b"%PDF-1.4 x"))
    client.post("/aeat/check/130", data={"year": "2026", "q": "1"})
    return client


def send(client, **over):
    form = {"model": "130", "year": "2026", "q": "1", "confirm": "1", "nrc": "1" * 22}
    form.update(over)
    if "sha" not in form:
        page = text(client.get("/submit/review?model=130&year=2026&q=1"))
        form["sha"] = page.split('name="sha" value="')[1].split('"')[0]
    return client.post("/submit/send", data=form, follow_redirects=True)


def stored():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM submissions ORDER BY id")]
    finally:
        con.close()


# ---------------------------------------------------------------- the request and the answer

def test_the_request_follows_the_published_layout(ready, transport):
    send(ready)
    [(url, payload, thumbprint)] = transport.calls
    assert url == "https://prewww1.aeat.es/wlpl/PFTW-PICW/PresBasicaDos" and thumbprint == THUMB
    assert list(payload) == ["MODELO", "EJERCICIO", "PERIODO", "NRC", "IDI", "F01", "FIR", "FIRNIF", "FIRNOMBRE"]
    assert (payload["MODELO"], payload["EJERCICIO"], payload["PERIODO"]) == ("130", "2026", "1T")
    assert (payload["FIR"], payload["FIRNIF"], payload["FIRNOMBRE"]) == ("FirmaBasica", "X1234567L", "GARCIA LOPEZ ANA")
    assert payload["NRC"] == "1" * 22 and payload["IDI"] == "ES"
    assert "\n" not in payload["F01"] and "\r" not in payload["F01"] and "\t" not in payload["F01"]
    assert payload["F01"].startswith("<T13002026")


def test_an_accepted_declaration_is_recorded_with_its_receipt(ready, transport):
    page = text(send(ready))
    assert "recorded the declaration" in page and "ABCDEF0123456789" in page and "20932 - revise" in page
    [row] = stored()
    assert (row["env"], row["status"], row["csv"], row["justificante"]) == ("test", "accepted", "ABCDEF0123456789", "1307000000001")
    assert row["pdf"]
    assert (db.documents_dir() / row["pdf"]).read_bytes().startswith(b"%PDF")
    assert "ABCDEF0123456789" in text(ready.get("/submit"))                       # and shown in the history


def test_a_rejected_declaration_lists_the_errors(ready, transport):
    transport.post = TransportResponse(200, json.dumps({"respuesta": {"errores": ["E00 - algo", "E01 - otra cosa"]}}).encode())
    page = text(send(ready))
    assert "rejected the declaration" in page and "E00 - algo" in page
    assert [r["status"] for r in stored()] == ["rejected"]


def test_an_unusable_answer_is_recorded_as_an_error(ready, transport):
    transport.post = TransportResponse(302, b"", location="https://sede.example/erro4033.html")
    page = text(send(ready))
    assert "no usable answer" in page and "erro4033" in page
    assert [r["status"] for r in stored()] == ["error"]


def test_a_failed_call_records_nothing(ready, transport):
    transport.post = TransportError("submit.err_transport", detail="timed out")
    assert "timed out" in text(send(ready))
    assert stored() == []


def test_a_missing_receipt_pdf_does_not_hide_the_acceptance(ready, transport):
    transport.pdf = None
    page = text(send(ready))
    assert "recorded the declaration" in page and "PDF was not available" in page
    assert stored()[0]["pdf"] == ""


def test_parse_response_handles_each_shape():
    ok = submission.parse_response(TransportResponse(200, json.dumps(OK_ANSWER).encode()))
    assert ok.accepted and ok.csv == "ABCDEF0123456789" and ok.warnings == ["20932 - revise"]
    assert not submission.parse_response(TransportResponse(200, b'{"respuesta":{"correcta":{"Fecha":"x"}}}')).accepted
    assert submission.parse_response(TransportResponse(200, b"<html>boom</html>")).status == "error"
    assert submission.parse_response(TransportResponse(200, b'{"hola":1}')).status == "error"
    rejected = submission.parse_response(TransportResponse(200, b'{"respuesta":{"errores":["E1",{"a":1}]}}'))
    assert rejected.status == "rejected" and rejected.errors[0] == "E1" and '"a"' in rejected.errors[1]


# ---------------------------------------------------------------- the gates

def test_nothing_is_sent_without_the_confirmation(ready, transport):
    assert "Tick the confirmation" in text(send(ready, confirm=""))
    assert transport.calls == [] and stored() == []


def test_a_changed_file_must_be_reviewed_again(ready, transport):
    assert "changed since you reviewed" in text(send(ready, sha="0" * 64))
    assert transport.calls == []


def test_the_file_must_have_been_validated_in_its_current_form(ready, transport):
    ready.post("/entries/new/expense", data=dict(date="2026-02-06", counterparty="X", country="ES", regime="no_vat",
                                                  base="10", vat_rate="0", currency="EUR", category_id="1"))
    page = text(ready.get("/submit/review?model=130&year=2026&q=1"))
    assert "has not been validated" in page and 'name="confirm"' not in page


def test_the_certificate_must_be_chosen_valid_and_the_taxpayers_own(ready, transport):
    sha = text(ready.get("/submit/review?model=130&year=2026&q=1")).split('name="sha" value="')[1].split('"')[0]
    set_settings(aeat_certificate="")
    assert "Choose a certificate" in text(ready.get("/submit/review?model=130&year=2026&q=1"))
    set_settings(aeat_certificate=THUMB)
    transport.certs = [Certificate(THUMB, "CN=A - Z9999999Z", "2020-01-01", "2021-01-01", "Z9999999Z", "OTHER PERSON")]
    page = text(ready.get("/submit/review?model=130&year=2026&q=1"))
    assert "expired on 2021-01-01" in page and "Z9999999Z" in page and "only your own declarations" in page
    assert "cannot be sent yet" in text(send(ready, sha=sha)) and transport.calls == []


def test_a_payment_declaration_needs_the_nrc(ready, transport):
    assert "22-character NRC" in text(send(ready, nrc=""))
    assert "22-character NRC" in text(send(ready, nrc="123"))
    assert transport.calls == []


def test_a_declaration_without_payment_sends_no_nrc(client, transport, monkeypatch):
    set_settings(holder_nif="X1234567L", holder_surname="Garcia Lopez", holder_firstname="Ana", aeat_certificate=THUMB)
    monkeypatch.setattr(aeat_client, "validate", lambda *a, **k: aeat_client.ValidationResult(True, pdf=b"%PDF-1.4 x"))
    client.post("/aeat/check/130", data={"year": "2026", "q": "1"})
    assert 'name="nrc"' not in text(client.get("/submit/review?model=130&year=2026&q=1"))
    send(client, nrc="")
    assert transport.calls[0][1]["NRC"] == ""


def test_only_the_test_environment_is_enabled():
    assert submission.environment_url("test").startswith("https://prewww1.aeat.es/")
    with pytest.raises(Exception):
        submission.environment_url("production")


@pytest.mark.parametrize("when,outside", [
    (datetime(2026, 11, 6, 10, 0), False), (datetime(2026, 11, 6, 7, 59), True), (datetime(2026, 11, 6, 15, 0), True),
    (datetime(2026, 11, 7, 10, 0), True), (datetime(2026, 11, 8, 10, 0), True)])
def test_the_test_environment_works_on_weekdays_from_8_to_15(when, outside):
    assert submission.outside_test_hours(when) is outside


# ---------------------------------------------------------------- certificates and pages

def test_the_certificate_is_chosen_from_the_store(client, transport):
    page = text(client.get("/submit"))
    assert "GARCIA LOPEZ ANA" in page and "valid until 2029-01-01" in page
    client.post("/submit/certificate", data={"thumbprint": THUMB.lower()})
    from tests.support import load_settings
    assert load_settings().aeat_certificate == THUMB
    assert "not in your Windows store" in text(client.post("/submit/certificate", data={"thumbprint": "FF" * 20}, follow_redirects=True))


def test_a_store_that_cannot_be_read_is_explained(client, transport):
    class Broken(FakeTransport):
        def certificates(self):
            raise TransportError("submit.err_powershell", detail="blocked")
    client.application.config["AEAT_TRANSPORT"] = Broken()
    assert "blocked" in text(client.get("/submit"))


def test_the_pages_exist_in_every_language_and_the_cards_link_to_them(ready, transport):
    assert "/submit/review?model=130" in text(ready.get("/reports/130?year=2026&q=1"))
    send(ready)
    for code in ("ru", "es", "en"):
        ready.post("/lang", data={"lang": code, "next": "/submit"})
        for path in ("/submit", "/submit/review?model=130&year=2026&q=1", "/submit/1"):
            assert ready.get(path).status_code == 200, (code, path)
    assert ready.get("/submit/review?model=999").status_code == 404 and ready.get("/submit/99").status_code == 404


# ---------------------------------------------------------------- the Windows transport

def run_returning(stdout: bytes, calls):
    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout, b"")
    return runner


def test_certificates_come_back_from_powershell_json():
    calls = []
    payload = json.dumps([{"thumbprint": "ab12", "subject": "CN=SURNAME NAME - 12345678Z, SN=SURNAME, G=NAME, "
                           "SERIALNUMBER=IDCES-12345678Z, C=ES", "notBefore": "2025-01-01", "notAfter": "2029-01-01"}])
    transport = WindowsStoreTransport(runner=run_returning(b"\xef\xbb\xbf" + payload.encode(), calls))
    [cert] = transport.certificates()
    assert (cert.thumbprint, cert.nif, cert.name) == ("AB12", "12345678Z", "SURNAME NAME") and not cert.expired()
    assert calls[0][:5] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]


def test_a_single_certificate_unwrapped_by_powershell_is_still_a_list():
    row = {"thumbprint": "ab", "subject": "CN=X", "notBefore": "2025-01-01", "notAfter": "2029-01-01"}
    assert len(WindowsStoreTransport(runner=run_returning(json.dumps(row).encode(), [])).certificates()) == 1


def test_the_body_goes_through_a_temporary_file_that_is_removed():
    import base64
    import os
    seen = {}

    def runner(command, **kwargs):
        body_file = command[command.index("-BodyFile") + 1]
        seen["content"] = json.loads(open(body_file, encoding="utf-8").read())
        seen["file"] = body_file
        assert "F01" not in " ".join(command)  # the declaration never appears on a command line
        out = {"status": 200, "location": "", "contentType": "application/json", "body": base64.b64encode(b'{"a":1}').decode()}
        return subprocess.CompletedProcess(command, 0, json.dumps(out).encode(), b"")

    response = WindowsStoreTransport(runner=runner).post_json("https://x.example", {"F01": "ñ€"}, "AB")
    assert (response.status, response.text) == (200, '{"a":1}') and seen["content"] == {"F01": "ñ€"}
    assert not os.path.exists(seen["file"])


def test_powershell_problems_become_readable_errors():
    with pytest.raises(TransportError) as exc:
        WindowsStoreTransport(runner=run_returning(b'{"error":"The certificate is not in the Windows store"}', [])).post_json("u", {}, "AB")
    assert "not in the Windows store" in str(exc.value)
    with pytest.raises(TransportError):
        WindowsStoreTransport(runner=run_returning(b"not json at all", [])).certificates()

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)
    with pytest.raises(TransportError):
        WindowsStoreTransport(runner=timeout).certificates()


@pytest.mark.parametrize("subject,expected", [
    ("CN=GARCIA LOPEZ ANA - Z1111111Z, SN=GARCIA LOPEZ, G=ANA, SERIALNUMBER=IDCES-Z1111111Z, C=ES", ("Z1111111Z", "GARCIA LOPEZ ANA")),
    ("CN=NOMBRE ANA APELLIDOS GARCIA - NIF 12345678Z", ("12345678Z", "NOMBRE ANA APELLIDOS GARCIA")),
    ("CN=ACME SL - B12345674, SERIALNUMBER=VATES-B12345674", ("B12345674", "ACME SL")),
    ("CN=something", ("", "something"))])
def test_the_holder_is_read_from_the_certificate_subject(subject, expected):
    assert parse_subject(subject) == expected


# ---- IBAN ----

def test_iban_check_accepts_real_format_and_rejects_pasted_text():
    from taxhelper.settings import is_valid_iban
    assert is_valid_iban("ES9121000418450200051332") and is_valid_iban("es91 2100 0418 4502 0005 1332")
    assert not is_valid_iban("") and not is_valid_iban("ES9121000418450200051333")  # wrong checksum
    assert not is_valid_iban("LOGO ES9121000418450200051332")  # text copied together with the number
    assert not is_valid_iban("ES91")


def test_settings_refuse_an_invalid_iban_and_the_file_is_not_built(client):
    from tests.test_profile import form
    page = text(client.post("/settings", data=form(payment_method="domiciliacion", holder_iban="LOGO ES9121000418450200051332"),
                            follow_redirects=True))
    assert "IBAN is not valid" in page
    set_settings(holder_nif="X1234567L", holder_surname="Garcia", holder_firstname="Ana", payment_method="domiciliacion",
                 holder_iban="LOGO ES9121000418450200051332")
    page = text(client.get("/reports/130?year=2026&q=1"))
    assert "IBAN is not valid" in page  # the report page explains it instead of offering a file the AEAT will refuse
    from taxhelper.aeat import service
    from taxhelper.i18n import UserError
    from taxhelper.reports import TaxReports
    con = db.connect()
    try:
        from taxhelper.repositories import EntryRepository
        from taxhelper.settings import SettingsRepository
        s = SettingsRepository(con)
        with pytest.raises(UserError):
            service.build_file(s.load(), TaxReports(s, EntryRepository(con)), "130", 2026, 1)
    finally:
        con.close()
