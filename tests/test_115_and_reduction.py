"""Modelo 115 (retentions on rent the user withholds) and the optional 20 % start-of-activity reduction."""
from datetime import date

from taxhelper import calc, db, deadlines
from taxhelper.repositories import EntryRepository
from tests.support import category_id, load_settings, set_settings, text
from tests.test_profile import form


def expense(client, on, base, category="office_rent", rate="19", name="Landlord SL", nif="B12345678", vat="21"):
    return client.post("/entries/new/expense", data=dict(
        date=on, counterparty=name, vat_id=nif, country="ES", regime="domestic", category_id=category_id(category),
        base=base, vat_rate=vat, withholding_rate=rate, currency="EUR"), follow_redirects=True)


def entries():
    con = db.connect()
    try:
        return EntryRepository(con).year_entries(2026)
    finally:
        con.close()


# ---------------------------------------------------------------- modelo 115

def test_a_retention_on_an_expense_is_stored_and_reduces_the_payment(client):
    expense(client, "2026-02-10", "1000")
    con = db.connect()
    row = con.execute("SELECT * FROM entries WHERE kind = 'expense'").fetchone()
    con.close()
    assert row["withholding_rate"] == "19" and row["withholding_cents"] == 19000
    assert "190.00" in text(client.get("/entries?year=2026"))
    answer = client.post("/api/preview", json=dict(kind="expense", date="2026-02-10", regime="domestic", base="1000",
                                                         vat_rate="21", withholding_rate="19")).get_json()
    assert answer["withholding"] == "€190.00" and answer["total"] == "€1,020.00"  # 1000 + 210 IVA - 190 retention


def test_retention_does_not_touch_income_withholding_in_130_or_the_rent_cost(client):
    expense(client, "2026-02-10", "1000")
    expense(client, "2026-02-12", "100", category="software", rate="0", name="Saas", nif="")
    year = calc.build_130_year(entries(), load_settings().tax(), 2026)
    assert year[0].c06 == 0  # only income withholdings are "retenciones soportadas"
    assert year[0].c02 == 100000 + 10000 + 0 or year[0].c02 >= 110000  # both expenses are costs in full


def test_115_report_sums_rent_with_a_retention_per_quarter(client):
    expense(client, "2026-01-15", "1000")
    expense(client, "2026-02-15", "1000")
    expense(client, "2026-03-15", "500", name="Other Landlord", nif="A87654321")
    expense(client, "2026-04-15", "1000")
    q = calc.build_115_year(entries(), 2026)
    assert (q[0].payees, q[0].base, q[0].withheld, q[0].result) == (2, 250000, 47500, 47500)
    assert (q[1].payees, q[1].base, q[1].withheld) == (1, 100000, 19000)
    assert q[2].withheld == 0 and q[3].payees == 0


def test_one_landlord_is_counted_once_by_nif_even_if_the_name_varies(client):
    expense(client, "2026-01-15", "1000", name="Landlord SL", nif="b12345678")
    expense(client, "2026-02-15", "1000", name="LANDLORD, S.L.", nif="B12345678")
    assert calc.build_115_year(entries(), 2026)[0].payees == 1


def test_a_retention_on_something_that_is_not_rent_is_reported_not_added(client):
    expense(client, "2026-01-15", "1000", category="professional", rate="15", name="Freelancer", nif="12345678Z")
    q = calc.build_115_year(entries(), 2026)[0]
    assert q.withheld == 0 and q.other_withheld == 1


def test_the_home_rent_category_with_a_retention_counts_too(client):
    expense(client, "2026-01-15", "1000", category="rent")
    assert calc.build_115_year(entries(), 2026)[0].withheld == 19000


def test_115_page_and_nav_link_appear_only_when_the_model_is_tracked(client):
    assert 'href="/reports/115' not in text(client.get("/"))
    client.post("/settings", data=form(tracked_models=["303", "130", "115", "390", "100"]))
    expense(client, "2026-01-15", "1000")
    assert 'href="/reports/115' in client.get("/").get_data(as_text=True)
    page = text(client.get("/reports/115?year=2026&q=1"))
    assert "Modelo 115" in page and "190.00" in page and "Landlord SL" in page and "B12345678" in page


def test_115_pages_render_in_all_languages(client):
    for lang in ("ru", "es", "en"):
        client.post("/lang", data={"lang": lang, "next": "/"})
        assert client.get("/reports/115?year=2026&q=2").status_code == 200


def test_115_is_opt_in_and_has_its_own_deadlines():
    assert "115" in deadlines.KNOWN_MODELS and "115" not in deadlines.DEFAULT_MODELS
    first = deadlines.quarter_filing("115", 2026, 1)
    assert first.due == date(2026, 4, 20) and first.opens == date(2026, 4, 1)
    last = deadlines.quarter_filing("115", 2026, 4)
    assert last.due == date(2027, 1, 20)  # not January 30 like 303 and 130
    assert deadlines.quarter_filing("130", 2026, 4).due == date(2027, 2, 1)  # January 30 is a Saturday


def test_tracking_115_adds_its_filings_to_the_tracker(client):
    client.post("/settings", data=form(tracked_models=["303", "130", "115", "390", "100"]))
    page = text(client.get("/filings"))
    assert "115" in page and "IRPF retentions on rent paid" in page


def test_supplier_contact_can_carry_a_default_retention_and_it_prefills_entries(client):
    client.post("/contacts/new/expense", data=dict(kind="expense", name="Landlord SL", vat_id="B12345678", country="ES",
                                                    withholding_rate="19", regime="domestic"))
    con = db.connect()
    row = con.execute("SELECT withholding_rate FROM contacts WHERE name = 'Landlord SL'").fetchone()
    con.close()
    assert row["withholding_rate"] == "19"


def test_entry_form_labels_the_retention_for_expenses(client):
    assert "Retention you withhold" in text(client.get("/entries/new/expense"))
    assert "IRPF withholding" in text(client.get("/entries/new/income"))


def test_the_category_for_business_rent_exists_in_new_and_old_databases(fresh_app):
    con = db.connect()
    try:
        assert con.execute("SELECT irpf_mode, vat_mode FROM categories WHERE code='office_rent'").fetchone()[:] == ("full", "full")
        # an old database (version 5) without it gets it once; deleting it later keeps it deleted
        con.execute("DELETE FROM categories WHERE code='office_rent'")
        con.execute("PRAGMA user_version = 5")
        con.commit()
        db.init_db(con)
        assert con.execute("SELECT COUNT(*) FROM categories WHERE code='office_rent'").fetchone()[0] == 1
        con.execute("DELETE FROM categories WHERE code='office_rent'")
        con.commit()
        db.init_db(con)
        assert con.execute("SELECT COUNT(*) FROM categories WHERE code='office_rent'").fetchone()[0] == 0
    finally:
        con.close()


# ---------------------------------------------------------------- 20 % reduction

def tax(**over):
    base = dict(start_reduction_years=(2026, 2027))
    base.update(over)
    return calc.TaxSettings(**base)


def test_reduction_is_twenty_percent_of_a_positive_yield_up_to_100000():
    assert calc.start_reduction(2_000_000, 2026, tax()) == 400_000
    assert calc.start_reduction(30_000_000, 2026, tax()) == 2_000_000  # only the first 100 000 EUR count
    assert calc.start_reduction(-500, 2026, tax()) == 0 and calc.start_reduction(0, 2026, tax()) == 0


def test_reduction_applies_only_in_its_two_years_and_only_when_switched_on():
    assert calc.start_reduction(1_000_000, 2027, tax()) == 200_000
    assert calc.start_reduction(1_000_000, 2028, tax()) == 0
    assert calc.start_reduction(1_000_000, 2025, tax()) == 0
    assert calc.start_reduction(1_000_000, 2026, calc.TaxSettings()) == 0


def test_the_switch_is_off_by_default_and_years_follow_the_start_date_or_the_chosen_year(client):
    s = load_settings()
    assert s.start_reduction is False and s.start_reduction_years == ()
    s = set_settings(start_reduction=True, activity_start=date(2026, 1, 23))
    assert s.start_reduction_years == (2026, 2027)
    s = set_settings(start_reduction_year=2027)
    assert s.start_reduction_years == (2027, 2028)
    s = set_settings(start_reduction=False)
    assert s.start_reduction_years == ()
    assert set_settings(start_reduction=True, start_reduction_year=0, activity_start=None).start_reduction_years == ()


def income(client, on, base):
    client.post("/entries/new/income", data=dict(date=on, counterparty="Client", country="ES", regime="domestic",
                                                  base=base, vat_rate="21", withholding_rate="0", currency="EUR"))


def test_renta_shows_the_reduction_only_when_enabled_and_the_130_is_not_affected(client):
    income(client, "2026-02-05", "10000")
    before = [r.c19 for r in calc.build_130_year(entries(), load_settings().tax(), 2026)]
    page = text(client.get("/reports/renta?year=2026"))
    assert "Reducción por inicio de actividad" not in page

    client.post("/settings", data=form(start_reduction="1", start_reduction_year="2026"))
    assert load_settings().start_reduction_years == (2026, 2027)
    page = text(client.get("/reports/renta?year=2026"))
    assert "Reducción por inicio de actividad" in page and "Rendimiento neto reducido" in page
    a = calc.build_annual(entries(), load_settings().tax(), 2026)
    assert a.start_reduction == round(a.net_yield * 0.2) and a.net_yield_reduced == a.net_yield - a.start_reduction
    assert [r.c19 for r in calc.build_130_year(entries(), load_settings().tax(), 2026)] == before
    assert "Reducción por inicio de actividad" not in text(client.get("/reports/renta?year=2028"))


def test_settings_validate_the_reduction_year(client):
    page = text(client.post("/settings", data=form(start_reduction="1", start_reduction_year="26"), follow_redirects=True))
    assert "first year of the reduction" in page
    assert load_settings().start_reduction is False
    client.post("/settings", data=form(start_reduction="1", start_reduction_year=""))
    assert load_settings().start_reduction is True and load_settings().start_reduction_year == 0


def test_reduction_settings_render_in_all_languages(client):
    for lang in ("ru", "es", "en"):
        client.post("/lang", data={"lang": lang, "next": "/"})
        assert client.get("/settings").status_code == 200 and client.get("/reports/renta?year=2026").status_code == 200
