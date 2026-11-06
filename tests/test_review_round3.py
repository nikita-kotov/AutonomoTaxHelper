"""Regression tests for the third code review (import, contacts, books)."""
from decimal import Decimal

import pytest

from taxhelper import db
from taxhelper.aeat import client as aeat_client
from taxhelper.aeat import libros
from taxhelper.repositories import ContactRepository
from taxhelper.settings import Settings
from tests.support import category_id, set_settings, text
from tests.test_import import commit, preview
from tests.test_libros import PROFILE, build, lines, post_entry, read_workbook


def custom_category(client, name, irpf, vat):
    client.post("/categories", data={"name": name, "irpf_mode": irpf, "vat_mode": vat})
    con = db.connect()
    try:
        return con.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]
    finally:
        con.close()


# ---------------------------------------------------------------- books

def test_expense_above_the_base_is_capped_and_the_total_matches_the_file(client):
    set_settings(**PROFILE)
    cid = custom_category(client, "Nonrecoverable", "full", "none")
    post_entry(client, "expense", date="2026-03-02", counterparty="Supplier SL", vat_id="B67817262", number="N-1",
               category_id=str(cid), base="100.00", vat_rate="21", vat_amount="21.00")
    built = build()
    [row] = lines(read_workbook(built.content)["RECIBIDAS_GASTOS"])
    assert row[7] == Decimal("100.00")                       # the AEAT never accepts more than the base
    assert built.expense_deductible_cents == 10_000          # the page total equals what the file carries
    warning = [str(w) for w in built.warnings if "limited" in str(w)]
    assert warning and "€21.00" in warning[0]                # and says what has to be added by hand


def test_credit_notes_are_rectifying_invoices_with_negative_amounts(client):
    set_settings(**PROFILE)
    post_entry(client, "income", date="2026-02-01", counterparty="Spanish SL", vat_id="B12345674", number="A-2",
               base="-200.00", vat_rate="21", vat_amount="-42.00", withholding_rate="0")
    post_entry(client, "expense", date="2026-02-02", counterparty="Aigues", vat_id="A66098435", number="W-9",
               category_id=str(category_id("water")), base="-61.34", vat_rate="10", vat_amount="-3.26")
    wb = read_workbook(build().content)
    [income] = lines(wb["EXPEDIDAS_INGRESOS"])
    assert income[5] == "R1" and income[21] == Decimal("-200.00") and income[23] == Decimal("-42.00")
    expense = lines(wb["RECIBIDAS_GASTOS"])
    assert [r[5] for r in expense] == ["R1", "R1"]                                   # both lines of the split credit note
    assert [r[26] for r in expense] == [Decimal("-32.60"), Decimal("-28.74")]        # split keeps the sign
    assert all(r[6] == "G15" for r in expense) and sum(r[7] for r in expense) < 0


def test_a_long_name_is_reported_once_even_when_the_invoice_has_two_lines(client):
    set_settings(**PROFILE)
    post_entry(client, "expense", date="2026-04-23", counterparty="N" * 45, vat_id="A66098435", number="W1",
               category_id=str(category_id("water")), base="61.34", vat_rate="10", vat_amount="3.26")
    built = build()
    assert built.expense_lines == 2 and sum("cut to 40" in str(w) for w in built.warnings) == 1


def test_an_unknown_book_column_fails_loudly():
    with pytest.raises(ValueError):
        libros._line(libros.INCOME_COLUMNS, nonsense=1)


def test_the_download_of_an_incomplete_profile_goes_back_to_the_page(client):
    r = client.get("/libros/file?year=2026")
    assert r.status_code == 302 and "/libros" in r.headers["Location"]


def test_the_aeat_verdict_needs_a_real_true():
    assert not aeat_client.parse_libros_response('{"resultado":{"valido":"false"}}').ok
    assert not aeat_client.parse_libros_response('{"resultado":{"valido":"N"}}').ok
    assert not aeat_client.parse_libros_response('{"resultado":{"valido":0}}').ok
    assert aeat_client.parse_libros_response('{"resultado":{"valido":true}}').ok
    assert aeat_client.parse_libros_response('{"resultado":{"valido":"true"}}').ok


# ---------------------------------------------------------------- dates

def test_a_date_with_a_two_digit_year_is_not_guessed_by_the_import(client):
    csv_text = "date;category;base\n23/01/26;software;10.00\n2026-01-23;software;10.00\n"
    page = text(preview(client, csv_text))
    assert "1 ready to import" in page and "1 with errors" in page
    commit(client, csv_text, [1, 2])
    con = db.connect()
    try:
        assert [r["date"] for r in con.execute("SELECT date FROM entries")] == ["2026-01-23"]
    finally:
        con.close()


def test_the_entry_form_refuses_a_year_that_is_a_typo(client):
    r = client.post("/entries/new/expense", data=dict(date="0026-01-23", counterparty="X", base="10", vat_rate="0",
                                                       regime="no_vat", category_id=str(category_id("software")),
                                                       country="ES", currency="EUR"))
    assert r.status_code == 200 and "valid date" in text(r)


# ---------------------------------------------------------------- contacts

def test_contact_usage_ignores_the_case_and_spacing_of_the_name_in_entries(client):
    client.post("/contacts/new/expense", data={"name": "Acme SL"})
    for name in ("ACME SL", "Acme  SL"):  # another case, a double space: both are the same supplier
        client.post("/entries/new/expense", data=dict(date="2026-02-01", counterparty=name, base="10", vat_rate="0",
                                                       regime="no_vat", category_id=str(category_id("software")),
                                                       country="ES", currency="EUR"))
    con = db.connect()
    try:
        assert ContactRepository(con).usage() == {1: 2}
    finally:
        con.close()


def test_remembering_the_same_party_twice_is_harmless(client):
    con = db.connect()
    try:
        repo = ContactRepository(con)
        entry = {"kind": "expense", "counterparty": "Acme  SL", "country": "ES", "vat_id": "", "regime": "domestic",
                 "category_id": None, "vat_rate": "21", "withholding_rate": "0"}
        assert repo.remember(entry) is True
        assert repo.remember({**entry, "counterparty": "ACME SL"}) is False   # same name, other case and spacing
        assert [r["name"] for r in repo.all()] == ["Acme SL"]
    finally:
        con.close()


# ---------------------------------------------------------------- settings

@pytest.mark.parametrize("registered,kind,expected", [
    (True, "expense", "domestic"), (True, "income", "domestic"),
    (False, "expense", "no_vat"), (False, "income", "exempt")])
def test_default_regime_follows_the_iva_registration(registered, kind, expected):
    assert Settings(vat_registered=registered).default_regime(kind) == expected


def test_imported_rows_start_from_the_default_regime_of_a_non_iva_taxpayer(client):
    set_settings(vat_registered=False)
    csv_text = "date;kind;counterparty;base\n2026-03-01;income;Client;100.00\n"
    commit(client, csv_text, [1])
    con = db.connect()
    try:
        assert con.execute("SELECT regime, vat_cents FROM entries").fetchone()["regime"] == "exempt"
    finally:
        con.close()


# ---------------------------------------------------------------- dates typed by the user

@pytest.mark.parametrize("bad", ["12345-01-23", "0026-01-23", "2101-01-01", "1999-12-31", "20260123", "2026-1-3",
                                 "2026-02-30", "", None, " "])
def test_typed_dates_must_be_four_digit_iso_years_in_range(bad):
    from taxhelper.dates import parse_date
    with pytest.raises(ValueError):
        parse_date(bad)


def test_a_good_date_is_accepted_with_surrounding_spaces():
    from datetime import date
    from taxhelper.dates import parse_date
    assert parse_date(" 2026-01-23 ") == date(2026, 1, 23)


def test_the_activity_start_refuses_a_five_digit_year(client):
    from tests.support import load_settings
    from tests.test_profile import form
    page = text(client.post("/settings", data=form(activity_start="12345-01-23")))
    assert "start date" in page.lower() or "activity" in page.lower()
    assert load_settings().activity_start is None
    client.post("/settings", data=form(activity_start="2026-01-23"))
    assert str(load_settings().activity_start) == "2026-01-23"
    client.post("/settings", data=form(activity_start="0026-01-23"))
    assert str(load_settings().activity_start) == "2026-01-23"  # the earlier good value stays


def test_holidays_and_filing_dates_refuse_impossible_years(client):
    from tests.support import load_settings
    from tests.test_profile import form
    client.post("/settings", data=form(extra_holidays="12345-01-01"))
    assert load_settings().extra_holidays == ()
    client.post("/filings/new", data={"fyear": "2026", "model": "349", "period": "1T", "due_date": "12345-04-20"})
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM filings WHERE model = '349'").fetchone()[0] == 0
    finally:
        con.close()
    page = text(client.post("/filings/bulk-filed", data={"upto": "20260101"}, follow_redirects=True))
    assert "date" in page.lower()


def test_every_date_field_limits_the_browser_to_four_digit_years(client):
    for path in ("/settings", "/entries/new/expense", "/filings"):
        page = text(client.get(path))
        fields = [chunk.split(">")[0] for chunk in page.split('type="date"')[1:]]
        assert fields and all('min="2000-01-01"' in f and 'max="2100-12-31"' in f for f in fields), path
