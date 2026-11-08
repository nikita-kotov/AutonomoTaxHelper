"""Entry pages: forms, edit page, list and search, the live preview, report messages."""

import pytest

from taxhelper import db
from taxhelper.repositories import EntryRepository
from tests.support import category_id, expense_form, set_settings, text


def set_identity():
    set_settings(holder_nif="X1234567L", holder_surname="GARCIA LOPEZ", holder_firstname="ANA")


def test_editing_an_entry_with_an_unusual_rate_does_not_change_it(client):
    client.post("/entries/new/expense", data={
        "date": "2026-02-10", "counterparty": "X", "regime": "domestic", "base": "100", "vat_rate": "5",
        "vat_amount": "5.00", "category_id": str(category_id("software")), "currency": "EUR"})
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()["id"]
    con.close()
    page = text(client.get(f"/entries/{eid}/edit"))
    assert '<option value="5" selected>' in page
    client.post(f"/entries/{eid}/edit", data={
        "date": "2026-02-10", "counterparty": "X", "regime": "domestic", "base": "100", "vat_rate": "5",
        "vat_amount": "5.00", "category_id": str(category_id("software")), "currency": "EUR"})
    con = db.connect()
    assert con.execute("SELECT vat_rate FROM entries").fetchone()["vat_rate"] == "5"
    con.close()


def test_report_page_shows_the_translated_negative_amount_message(client):
    set_identity()
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "Refund", "regime": "exempt",
                                            "base": "-50", "currency": "EUR"})
    page = text(client.get("/reports/130?year=2026&q=1"))
    assert "A negative amount cannot be written into this field" in page


def test_a_thousands_amount_typed_in_the_form_is_not_shrunk_a_thousandfold(client):
    set_settings(language="es")
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "C", "regime": "exempt",
                                            "base": "1.500", "currency": "EUR"})
    con = db.connect()
    assert con.execute("SELECT base_cents FROM entries").fetchone()[0] == 150_000
    con.close()


def test_entry_search_is_case_insensitive_for_every_alphabet(client):
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "Петров Иван", "regime": "exempt",
                                            "base": "100", "currency": "EUR"})
    client.post("/entries/new/income", data={"date": "2026-02-02", "counterparty": "Other", "regime": "exempt",
                                            "base": "5", "currency": "EUR"})
    page = text(client.get("/entries?year=2026&text=ПЕТРОВ"))
    assert "Петров Иван" in page and "Other" not in page


def test_entries_list_is_newest_first_and_caps_apply_across_filters(client):
    cid = category_id("health")
    con = db.connect()
    con.execute("UPDATE categories SET annual_cap_cents=50000 WHERE id=?", (cid,))
    con.commit()
    con.close()
    for month in (1, 2):
        client.post("/entries/new/expense", data={"date": f"2026-0{month}-10", "counterparty": f"Insurer{month}",
                                                 "regime": "no_vat", "base": "400", "category_id": str(cid)})
    page = text(client.get("/entries?year=2026&q=1"))
    assert "€400.00" in page  # January is fully deducted
    page = text(client.get("/entries?year=2026&kind=expense&text=insurer1"))
    assert "Insurer1" in page and "Insurer2" not in page
    page = text(client.get("/entries?year=2026&text=insurer2"))  # filtered view still shows the capped amount
    assert "€100.00" in page and "€400.00" in page  # base 400, deductible only the remaining 100
    both = text(client.get("/entries?year=2026"))
    assert both.index("Insurer2") < both.index("Insurer1")  # newest first
    assert "€100.00" in both  # February is capped to the remaining 100 EUR


def test_a_new_entry_with_an_unknown_category_is_an_error_not_a_crash(client):
    r = client.post("/entries/new/expense", data=expense_form(category_id="9999"))
    assert r.status_code == 200 and "Choose an expense category." in text(r)


def test_an_explicit_zero_vat_is_shown_on_the_edit_page(client):
    client.post("/entries/new/expense", data=expense_form(vat_amount="0"))
    con = db.connect()
    row = con.execute("SELECT id, vat_cents FROM entries").fetchone()
    con.close()
    assert row["vat_cents"] == 0
    assert 'id="vat_amount" value="0.00"' in text(client.get(f"/entries/{row['id']}/edit"))


def test_resaving_a_reverse_charge_entry_keeps_its_self_assessed_vat(client):
    client.post("/entries/new/expense", data=expense_form(regime="eu_reverse"))
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()[0]
    con.close()
    page = text(client.get(f"/entries/{eid}/edit"))
    assert 'id="vat_amount" value="21.00"' in page  # hidden for this regime; the server recomputes it anyway
    client.post(f"/entries/{eid}/edit", data=expense_form(regime="eu_reverse", vat_amount="21.00"))
    con = db.connect()
    assert con.execute("SELECT vat_cents FROM entries WHERE id=?", (eid,)).fetchone()[0] == 2_100
    con.close()


def test_the_entry_form_refuses_a_year_that_is_a_typo(client):
    r = client.post("/entries/new/expense", data=dict(date="0026-01-23", counterparty="X", base="10", vat_rate="0",
                                                       regime="no_vat", category_id=str(category_id("software")),
                                                       country="ES", currency="EUR"))
    assert r.status_code == 200 and "valid date" in text(r)


@pytest.mark.parametrize("payload", [[], "x", 5, None, {}, {"kind": "expense", "regime": "nope", "base": "1"},
                                     {"kind": "x", "base": "1"}, {"kind": "expense", "regime": "domestic", "base": [1]},
                                     {"kind": "expense", "regime": "domestic", "base": "10", "vat_rate": {"a": 1}},
                                     {"kind": "expense", "regime": "domestic", "base": "10", "category_id": True},
                                     {"kind": "expense", "regime": "domestic", "base": "10", "vat_amount": True,
                                      "vat_manual": True, "currency": True}])
def test_preview_never_returns_a_server_error(client, payload):
    r = client.post("/api/preview", json=payload)
    assert r.status_code == 200 and r.get_json()["ok"] in (True, False)


def test_preview_accepts_numbers_and_odd_category_ids(client):
    r = client.post("/api/preview", json={"kind": "expense", "regime": "domestic", "base": 100, "vat_rate": 21,
                                          "category_id": [1], "date": "2026-02-01", "vat_manual": False})
    assert r.get_json()["ok"] is True and r.get_json()["total"] == "€121.00"


def test_preview_survives_a_non_json_body(client):
    r = client.post("/api/preview", data="not json", content_type="text/plain")
    assert r.status_code == 200 and r.get_json() == {"ok": False}


def test_a_year_is_read_once_until_the_data_changes(client):
    client.post("/entries/new/income", data=dict(date="2026-02-05", counterparty="C", country="ES", regime="exempt",
                                                  base="100", currency="EUR"))
    con = db.connect()
    reads = []
    con.set_trace_callback(lambda sql: reads.append(sql) if sql.lstrip().upper().startswith("SELECT E.*") else None)
    try:
        repo = EntryRepository(con)
        assert len(repo.year_entries(2026)) == 1 and len(repo.year_rows(2026)) == 1 and len(repo.year_entries(2026)) == 1
        assert len(reads) == 1  # three calls, one read
        copy = repo.year_entries(2026)
        copy.clear()
        assert len(repo.year_entries(2026)) == 1  # a caller emptying its list does not touch the cached one
        category = con.execute("SELECT id FROM categories WHERE code = 'software'").fetchone()["id"]
        repo.insert(dict(kind="expense", date="2026-03-01", number="", counterparty="X", country="ES", vat_id="",
                         description="", category_id=category, regime="no_vat", base_cents=1000, vat_rate="0", vat_cents=0,
                         withholding_rate="0", withholding_cents=0, currency="EUR", orig_amount_cents=None, fx_rate="",
                         fx_rate_date="", fx_source="", notes=""))
        assert len(repo.year_entries(2026)) == 2 and len(reads) == 2  # a write is seen at once
        con.execute("UPDATE categories SET irpf_mode = 'none' WHERE id = ?", (category,))
        assert [e.irpf_mode for e in repo.year_entries(2026)][-1] == "none" and len(reads) == 3  # and so is a category change
    finally:
        con.close()
