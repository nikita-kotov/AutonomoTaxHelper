"""The year overview of the dashboard and the estimate of the annual Renta."""
import json
import re

import pytest

from taxhelper import db, i18n, renta
from taxhelper.repositories import EntryRepository
from taxhelper.reports import TaxReports
from taxhelper.settings import SettingsRepository
from tests.support import load_settings, set_settings, text
from tests.test_profile import form

CATALONIA = "0 9.5\n12500 12.5\n22000 16\n33000 19\n53000 21.5\n90000 23.5\n120000 24.5\n175000 25.5"


def income(client, on, base, vat="0", regime="non_eu"):
    client.post("/entries/new/income", data=dict(date=on, counterparty="Client", country="SC", regime=regime, base=base,
                                                  vat_rate=vat, withholding_rate="0", currency="EUR"))


# ---------------------------------------------------------------- the scale and the estimate

def test_a_scale_taxes_each_slice_at_its_own_rate():
    assert renta.scale_tax(1_000_000, renta.STATE_SCALE) == 95_000  # 10 000 at 9.5 %
    assert renta.scale_tax(1_500_000, renta.STATE_SCALE) == 118_275 + 30_600  # 12 450 at 9.5 %, 2 550 at 12 %
    assert renta.scale_tax(0, renta.STATE_SCALE) == 0 and renta.scale_tax(-5, renta.STATE_SCALE) == 0


def test_scale_text_is_read():
    scale = renta.parse_scale(CATALONIA)
    assert scale[1] == (1_250_000, renta.Decimal("12.5")) and len(scale) == 8
    assert renta.parse_scale("   ") is None
    assert renta.parse_scale("0 9,5; 12500 12,5") == renta.parse_scale("0 9.5\n12500 12.5")


@pytest.mark.parametrize("bad", ["12500 12", "0", "0 9.5 x", "0 abc", "0 9.5\n0 12", "0 9.5\n20000 12\n10000 15", "0 101", "-5 9.5",
                                 "0 9.5\n12,500 12.5", "0 9.5\n12.500 12.5"])  # the last two: thousands separators
def test_an_unreadable_scale_is_refused(bad):
    with pytest.raises(ValueError):
        renta.parse_scale(bad)


def test_the_estimate_takes_off_the_minimum_the_withholdings_and_the_advances():
    cat = renta.parse_scale(CATALONIA)
    e = renta.estimate_renta(3_000_000, 0, 555_000, 20_000, 500_000, cat)
    gross = renta.scale_tax(3_000_000, renta.STATE_SCALE) + renta.scale_tax(3_000_000, cat)
    minimum = renta.scale_tax(555_000, renta.STATE_SCALE) + renta.scale_tax(555_000, cat)
    assert e.tax == gross - minimum and e.result == e.tax - 20_000 - 500_000 and e.base == 3_000_000


def test_a_base_below_the_minimum_pays_nothing_and_advances_come_back():
    e = renta.estimate_renta(300_000, 0, 555_000, 0, 40_000)
    assert e.tax == 0 and e.result == -40_000


def test_other_income_is_added_to_the_base():
    assert renta.estimate_renta(2_000_000, 500_000, 555_000, 0, 0).base == 2_500_000


def test_without_a_regional_scale_the_state_scale_is_used_twice():
    e = renta.estimate_renta(3_000_000, 0, 0, 0, 0)
    assert e.tax == 2 * renta.scale_tax(3_000_000, renta.STATE_SCALE)


# ---------------------------------------------------------------- quarterly figures

def test_overview_gives_each_quarter_its_own_figures(client):
    income(client, "2026-02-05", "1000")
    income(client, "2026-05-10", "2000")
    con = db.connect()
    try:
        rows = TaxReports(SettingsRepository(con), EntryRepository(con)).overview(2026)
    finally:
        con.close()
    assert [r.income for r in rows] == [100_000, 200_000, 0, 0]
    # 20 % of the yield after the 5 % for difficult-to-justify expenses: 950, then 2 850 less the first payment
    assert [r.irpf for r in rows][:2] == [19_000, 38_000]
    assert sum(r.irpf for r in rows) == 57_000  # the quarters add up to the payment on the whole year


# ---------------------------------------------------------------- the page

def chart_data(page):
    return json.loads(re.search(r'id="overview-data">(.*?)</script>', page, re.S).group(1))


def test_dashboard_shows_the_year_figures_chart_data_and_the_estimate(client):
    income(client, "2026-02-05", "10000")
    page = client.get("/?year=2026").get_data(as_text=True)
    assert "€10,000" in page and "Taxes of the year" in page and "overview.js" in page
    data = chart_data(page)
    assert [s["key"] for s in data["series"]] == ["income", "expenses", "irpf", "vat"]
    assert [q["label"] for q in data["quarters"]] == ["1T", "2T", "3T", "4T"]
    assert data["quarters"][0]["cells"]["income"] == {"cents": 1_000_000, "text": "€10,000.00"}
    assert "Renta 2026" in page and "Quarterly payments (130)" in page and ("To pay when you file" in page or "Refund" in page)
    assert "Regional scale not set" in page


def test_the_vat_series_is_left_out_for_someone_who_does_not_charge_iva(client):
    set_settings(vat_registered=False)
    assert [s["key"] for s in chart_data(client.get("/?year=2026").get_data(as_text=True))["series"]] == ["income", "expenses", "irpf"]


def test_the_estimate_uses_the_filed_result_of_a_quarter_instead_of_the_calculated_one(client):
    income(client, "2026-02-05", "10000")
    set_settings(renta_regional_scale=CATALONIA)
    before = text(client.get("/?year=2026"))
    assert "Regional scale not set" not in before
    client.get("/filings")
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE year=2026 AND model='130' AND period='1T'").fetchone()["id"]
    con.close()
    client.post(f"/filings/{fid}/edit", data={"status": "filed", "due_date": "2026-04-20", "filed_date": "2026-04-18",
                                              "reference": "", "result": "100.00", "notes": ""})
    after = text(client.get("/?year=2026"))
    assert "Quarterly payments (130)" in after and "-€100.00" in after.replace("−", "-")


def test_dashboard_pages_render_in_all_languages(client):
    income(client, "2026-02-05", "1000")
    for lang in ("ru", "es", "en"):
        client.post("/lang", data={"lang": lang, "next": "/"})
        assert client.get("/").status_code == 200


# ---------------------------------------------------------------- settings

def test_settings_keep_the_renta_estimate_fields(client):
    client.post("/settings", data=form(renta_personal_minimum_eur="6700", renta_other_income_eur="1200,50",
                                       renta_regional_scale=CATALONIA))
    s = load_settings()
    assert (s.renta_personal_minimum_cents, s.renta_other_income_cents) == (670_000, 120_050)
    assert s.renta_regional_scale == CATALONIA and s.regional_scale[0] == (0, renta.Decimal("9.5"))


def test_empty_fields_fall_back_to_the_default_minimum(client):
    client.post("/settings", data=form(renta_personal_minimum_eur="", renta_other_income_eur="", renta_regional_scale=""))
    s = load_settings()
    assert s.renta_personal_minimum_cents == 555_000 and s.renta_other_income_cents == 0 and s.regional_scale is None


def test_a_bad_scale_is_refused_with_a_message(client):
    page = text(client.post("/settings", data=form(renta_regional_scale="10 5"), follow_redirects=True))
    assert "regional scale needs one bracket per line" in page
    assert load_settings().renta_regional_scale == ""


def test_money_can_be_shown_in_whole_euros():
    assert i18n.format_money(3_713_202, "en", whole=True) == "€37,132"
    assert i18n.format_money(-10_100, "en", whole=True) == "-€101"
    assert i18n.format_money(50, "en", whole=True) == "€1" and i18n.format_money(49, "en", whole=True) == "€0"


def test_a_negative_personal_minimum_is_refused(client):
    client.post("/settings", data=form(renta_personal_minimum_eur="-100"))
    assert load_settings().renta_personal_minimum_cents == 555_000
