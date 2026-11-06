"""A negative IVA result of the 4th quarter: compensate in later periods or ask for a refund."""
import pytest

from taxhelper import db
from taxhelper.aeat import model303, service
from taxhelper.i18n import UserError
from taxhelper.repositories import EntryRepository
from taxhelper.reports import TaxReports
from taxhelper.settings import SettingsRepository
from tests.support import category_id, load_settings, set_settings, text
from tests.test_profile import form

IBAN = "ES9121000418450200051332"


def purchase(client, on, base="1000"):
    client.post("/entries/new/expense", data=dict(date=on, counterparty="Proveedor SL", vat_id="B12345678", country="ES",
                                                   regime="domestic", category_id=category_id("equipment"), base=base, vat_rate="21", withholding_rate="0",
                                                   currency="EUR"))


def build(quarter, **settings):
    set_settings(holder_nif="X1234567L", holder_surname="Garcia", holder_firstname="Ana", **settings)
    con = db.connect()
    try:
        s = SettingsRepository(con)
        return service.build_file(s.load(), TaxReports(s, EntryRepository(con)), "303", 2026, quarter)
    finally:
        con.close()


def test_compensation_is_the_default(client):
    purchase(client, "2026-11-10")
    built = build(4, holder_iban=IBAN)
    assert built.tipo == "C" and built.result_cents < 0 and built.iban == ""


def test_a_refund_in_the_fourth_quarter_is_type_d_with_the_account_in_the_file(client):
    purchase(client, "2026-11-10")
    built = build(4, holder_iban=IBAN, vat_negative_4t="refund", payment_method="nrc")
    assert built.tipo == "D" and built.iban == IBAN and IBAN in built.text
    assert built.text.count(IBAN) == 1  # only in the bank page


def test_other_quarters_can_only_compensate(client):
    purchase(client, "2026-02-10")
    built = build(1, holder_iban=IBAN, vat_negative_4t="refund")
    assert built.tipo == "C" and IBAN not in built.text


def test_a_positive_result_is_not_a_refund(client):
    client.post("/entries/new/income", data=dict(date="2026-11-10", counterparty="Cliente", country="ES", regime="domestic",
                                                  base="1000", vat_rate="21", withholding_rate="0", currency="EUR"))
    built = build(4, holder_iban=IBAN, vat_negative_4t="refund", payment_method="nrc")
    assert built.tipo == "I" and IBAN not in built.text


def test_the_refund_needs_a_valid_iban(client):
    purchase(client, "2026-11-10")
    with pytest.raises(UserError) as exc:
        build(4, holder_iban="", vat_negative_4t="refund")
    assert "IBAN" in str(exc.value)
    with pytest.raises(UserError):
        build(4, holder_iban="LOGO " + IBAN, vat_negative_4t="refund")


def test_a_bad_iban_does_not_block_a_declaration_that_does_not_use_it(client):
    purchase(client, "2026-11-10")
    assert build(4, holder_iban="LOGO " + IBAN).tipo == "C"


def test_declaration_type_rules():
    class Rep:
        quarter, c71 = 4, -100
    assert model303.declaration_type(Rep, "", IBAN) == "D"
    assert model303.declaration_type(Rep, "", "") == "C"
    Rep.quarter = 3
    assert model303.declaration_type(Rep, "", IBAN) == "C"
    Rep.quarter, Rep.c71 = 4, 0
    assert model303.declaration_type(Rep, "", IBAN) == "N"


def test_the_setting_is_saved_and_a_refund_without_an_iban_is_refused(client):
    page = text(client.post("/settings", data=form(vat_negative_4t="refund", holder_iban=""), follow_redirects=True))
    assert "IBAN in Settings to request the IVA refund" in page and load_settings().vat_negative_4t == "compensate"
    client.post("/settings", data=form(vat_negative_4t="refund", holder_iban=IBAN))
    assert load_settings().vat_negative_4t == "refund"
    client.post("/settings", data=form(vat_negative_4t="anything", holder_iban=IBAN))
    assert load_settings().vat_negative_4t == "compensate"


def test_settings_and_report_pages_render_in_all_languages(client):
    purchase(client, "2026-11-10")
    for lang in ("ru", "es", "en"):
        client.post("/lang", data={"lang": lang, "next": "/"})
        assert client.get("/settings").status_code == 200 and client.get("/reports/303?year=2026&q=4").status_code == 200
