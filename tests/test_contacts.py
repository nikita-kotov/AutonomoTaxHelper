"""Clients and suppliers: storage, pages, form autofill data, remembering from entries, import defaults."""
import json
import re

from taxhelper import db
from taxhelper.repositories import ContactRepository
from tests.support import category_id, text
from tests.test_import import commit, preview

CLIENT = {"name": "ACME CAPITAL", "country": "SC", "vat_id": "1234567-8", "email": "ap@acme.example",
          "address": "Office 1, Plaisance", "regime": "non_eu", "vat_rate": "", "withholding_rate": "0",
          "description": "Monthly work", "notes": ""}


def contacts():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM contacts ORDER BY id")]
    finally:
        con.close()


def entry_form(**over):
    base = {"date": "2026-03-01", "counterparty": "Acme SL", "country": "ES", "vat_id": "B1", "description": "x",
            "category_id": str(category_id("software")), "regime": "domestic", "base": "100", "vat_rate": "21",
            "currency": "EUR"}
    base.update(over)
    return base


def test_add_edit_and_delete_a_client(client):
    r = client.post("/contacts/new/income", data=CLIENT, follow_redirects=True)
    assert "Contact added" in text(r) and "ACME CAPITAL" in text(r)
    row = contacts()[0]
    assert (row["kind"], row["country"], row["regime"], row["withholding_rate"]) == ("income", "SC", "non_eu", "0")
    client.post(f"/contacts/{row['id']}/edit", data={**CLIENT, "vat_id": "NEW-ID"})
    assert contacts()[0]["vat_id"] == "NEW-ID"
    assert "Contact deleted" in text(client.post(f"/contacts/{row['id']}/delete", follow_redirects=True))
    assert contacts() == []


def test_a_supplier_keeps_its_category_and_rate(client):
    cid = category_id("software")
    client.post("/contacts/new/expense", data={"name": "Cloud Inc", "country": "us", "regime": "non_eu_reverse",
                                              "category_id": str(cid), "vat_rate": "21"})
    row = contacts()[0]
    assert (row["kind"], row["country"], row["category_id"], row["vat_rate"]) == ("expense", "US", cid, "21")


def test_names_are_unique_per_kind_ignoring_case(client):
    client.post("/contacts/new/income", data={"name": "Acme"})
    again = client.post("/contacts/new/income", data={"name": "ACME"})
    assert "already exists" in text(again) and len(contacts()) == 1
    client.post("/contacts/new/expense", data={"name": "Acme"})  # a supplier of the same name is another contact
    assert len(contacts()) == 2


def test_bad_input_is_reported_and_nothing_is_stored(client):
    bad = client.post("/contacts/new/income", data={"name": "", "email": "nope", "vat_rate": "abc", "regime": "eu_reverse"})
    page = text(bad)
    for message in ("Enter a name.", "Enter a valid email address.", "Invalid IVA treatment.", "Invalid percentage: abc"):
        assert message in page
    assert contacts() == []
    assert client.get("/contacts/new/other").status_code == 404
    assert client.get("/contacts/999/edit").status_code == 404


def test_the_entry_form_offers_only_contacts_of_its_kind(client):
    client.post("/contacts/new/income", data={"name": "Client One", "country": "IL"})
    client.post("/contacts/new/expense", data={"name": "Supplier One"})
    income, expense = text(client.get("/entries/new/income")), text(client.get("/entries/new/expense"))
    assert 'option value="Client One"' in income and "Supplier One" not in income
    assert 'option value="Supplier One"' in expense and "Client One" not in expense
    data = json.loads(re.search(r"const CONTACTS = (\{.*?\});\n", income, re.S).group(1))
    assert data["client one"]["country"] == "IL"
    assert "const AUTOFILL = true" in income


def test_editing_an_entry_does_not_autofill_or_offer_to_save(client):
    client.post("/entries/new/expense", data=entry_form())
    page = text(client.get("/entries/1/edit"))
    assert "const AUTOFILL = false" in page and 'name="save_contact"' not in page
    assert 'name="save_contact"' in text(client.get("/entries/new/expense"))


def test_a_new_entry_can_save_its_party_as_a_contact(client):
    client.post("/entries/new/expense", data={**entry_form(), "save_contact": "1"})
    row = contacts()[0]
    assert (row["kind"], row["name"], row["country"], row["category_id"], row["regime"], row["vat_rate"]) == (
        "expense", "Acme SL", "ES", category_id("software"), "domestic", "21")
    client.post("/entries/new/expense", data={**entry_form(date="2026-04-01", counterparty="acme sl"), "save_contact": "1"})
    assert len(contacts()) == 1  # same name: no duplicate
    client.post("/entries/new/expense", data=entry_form(counterparty="Other SL"))
    assert len(contacts()) == 1  # the box was not ticked


def test_income_contact_remembers_withholding_and_no_rate_for_a_foreign_client(client):
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "Acme Trading", "country": "SC", "regime": "non_eu",
                                             "base": "5000", "vat_rate": "21", "withholding_rate": "0", "currency": "EUR",
                                             "save_contact": "1"})
    row = contacts()[0]
    assert (row["kind"], row["regime"], row["vat_rate"], row["withholding_rate"]) == ("income", "non_eu", "", "0")


def test_contacts_can_be_created_from_existing_entries_using_the_latest_one(client):
    client.post("/entries/new/expense", data=entry_form(date="2026-01-10", vat_id="OLD"))
    client.post("/entries/new/expense", data=entry_form(date="2026-02-10", vat_id="NEWER"))
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "Acme Trading", "country": "SC", "regime": "non_eu",
                                             "base": "5000", "currency": "EUR"})
    r = client.post("/contacts/from-entries", follow_redirects=True)
    assert "2 contacts added" in text(r)
    rows = {c["name"]: c for c in contacts()}
    assert rows["Acme SL"]["vat_id"] == "NEWER" and rows["Acme Trading"]["kind"] == "income"
    assert "0 contacts added" in text(client.post("/contacts/from-entries", follow_redirects=True))


def test_the_list_shows_how_many_entries_use_a_contact(client):
    client.post("/contacts/new/expense", data={"name": "Acme SL"})
    client.post("/entries/new/expense", data=entry_form())
    client.post("/entries/new/expense", data=entry_form(date="2026-05-01"))
    con = db.connect()
    try:
        assert ContactRepository(con).usage() == {1: 2}
    finally:
        con.close()


def test_deleting_an_unused_category_clears_it_from_contacts(client):
    client.post("/categories", data={"name": "Temp", "irpf_mode": "full", "vat_mode": "full"})
    con = db.connect()
    cid = con.execute("SELECT id FROM categories WHERE name = 'Temp'").fetchone()["id"]
    con.close()
    client.post("/contacts/new/expense", data={"name": "Acme", "category_id": str(cid)})
    client.post(f"/categories/{cid}/delete")
    assert contacts()[0]["category_id"] is None


def test_import_blank_cells_come_from_the_saved_contact(client):
    client.post("/contacts/new/expense", data={"name": "Cloud Inc", "country": "US", "regime": "non_eu_reverse",
                                              "category_id": str(category_id("software")), "vat_rate": "21",
                                              "description": "Subscription"})
    csv_text = "date;counterparty;currency;orig_amount\n2026-03-02;cloud inc;USD;20.00\n"
    assert "1 ready to import" in text(preview(client, csv_text))
    commit(client, csv_text, [1])
    con = db.connect()
    row = dict(con.execute("SELECT * FROM entries").fetchone())
    con.close()
    assert (row["country"], row["regime"], row["description"], row["category_id"]) == (
        "US", "non_eu_reverse", "Subscription", category_id("software"))
    assert row["vat_cents"] > 0 and row["currency"] == "USD"


def test_pages_render_in_every_language_and_the_menu_links_to_contacts(client):
    client.post("/contacts/new/income", data=CLIENT)
    assert "/contacts" in text(client.get("/entries"))
    titles = {"ru": "Клиенты и поставщики", "es": "Clientes y proveedores", "en": "Clients and suppliers"}
    for code, title in titles.items():
        client.post("/lang", data={"lang": code, "next": "/contacts"})
        assert title in text(client.get("/contacts"))
        assert client.get("/contacts/new/income").status_code == 200
        assert client.get("/contacts/1/edit").status_code == 200
        assert client.get("/entries/new/income").status_code == 200
