"""Per-taxpayer configuration: everything that differs between autonomos is a setting."""
from datetime import date
from decimal import Decimal

import pytest
from support import category_id, load_settings

from taxhelper import calc, db, deadlines
from taxhelper.calc import Entry, TaxSettings

HOME = TaxSettings(home_affected_pct=Decimal(20))


def expense(base, vat=0, d=date(2026, 2, 10), category="x", cap=None, irpf="full", vatm="full"):
    return Entry(kind="expense", date=d, regime="domestic", base_cents=base, vat_cents=vat,
                 vat_rate=Decimal(21 if vat else 0), category=category, category_cap_cents=cap,
                 irpf_mode=irpf, vat_mode=vatm)


def income(base, d=date(2026, 2, 10)):
    return Entry(kind="income", date=d, regime="domestic", base_cents=base)


# ---------------------------------------------------------------- calc

def test_category_annual_cap_limits_the_running_total():
    es = [expense(30_000, category="health", cap=50_000), expense(40_000, category="health", cap=50_000),
          expense(10_000, category="health", cap=50_000), expense(25_000, category="other")]
    assert calc.irpf_deductions(es, TaxSettings()) == [30_000, 20_000, 0, 25_000]


def test_declared_percentage_is_the_share_used_for_home_costs():
    s = TaxSettings(home_affected_pct=Decimal("11"))  # as declared in modelo 036, no surface needed
    electricity = expense(10_000, 2_100, category="power", irpf="home_supply", vatm="home_supply")
    # IVA deducted: 21,00 x 11% = 2,31 ; cost = 121,00 - 2,31 = 118,69 ; IRPF = 118,69 x 11% x 30% = 3,9168 -> 3,92
    assert calc.vat_deductible(electricity, s) == 231
    assert calc.irpf_deductible(electricity, s) == 392
    rent = expense(80_000, irpf="home_property", vatm="none")
    assert calc.irpf_deductible(rent, s) == 8_800  # 11% of 800 EUR


@pytest.mark.parametrize("supplies_rule,property_rule,power,rent", [
    ("share_factor", "share", 392, 8_800),        # defaults: supplies x 30 %, rent by the affected % only
    ("share_factor", "share_factor", 392, 2_640),  # cautious: the 30 % factor on rent as well
    ("share", "share", 1_306, 8_800),              # supplies by the affected % only
    ("share", "share_factor", 1_306, 2_640)])
def test_supplies_and_rent_follow_their_own_rule(supplies_rule, property_rule, power, rent):
    s = TaxSettings(home_affected_pct=Decimal("11"), supplies_rule=supplies_rule, property_rule=property_rule)
    electricity = expense(10_000, 2_100, category="power", irpf="home_supply", vatm="home_supply")
    rent_invoice = expense(80_000, irpf="home_property", vatm="none")
    # electricity: IVA deducted 2,31 -> cost 118,69; x 11 % (x 30 % under share_factor)
    assert calc.irpf_deductible(electricity, s) == power
    assert calc.irpf_deductible(rent_invoice, s) == rent  # 800 EUR x 11 % (x 30 %)


def test_the_factor_value_is_shared_by_both_rules():
    s = TaxSettings(home_affected_pct=Decimal("10"), supplies_factor=Decimal("0.25"), property_rule="share_factor")
    assert calc.irpf_deductible(expense(100_000, irpf="home_property", vatm="none"), s) == 2_500
    assert calc.irpf_deductible(expense(100_000, irpf="home_supply", vatm="none"), s) == 2_500


def test_home_rules_do_not_touch_iva(client):
    s = TaxSettings(home_affected_pct=Decimal("11"), supplies_rule="share", property_rule="share_factor")
    e = expense(10_000, 2_100, irpf="home_supply", vatm="home_supply")
    assert calc.vat_deductible(e, s) == 231  # IVA has its own setting (vat_home_mode)


def test_no_iva_mode_puts_the_whole_supply_invoice_into_the_irpf_cost():
    s = TaxSettings(home_affected_pct=Decimal("11"), vat_home_mode="none")
    electricity = expense(10_000, 2_100, category="power", irpf="home_supply", vatm="home_supply")
    assert calc.vat_deductible(electricity, s) == 0
    assert calc.irpf_deductible(electricity, s) == 399  # 121,00 x 11 % x 30 % = 3,993 -> 3,99
    rent = expense(80_000, irpf="home_property", vatm="none")
    assert calc.irpf_deductible(rent, s) == 8_800  # only supplies change
    full = expense(10_000, 2_100, irpf="full", vatm="full")
    assert calc.vat_deductible(full, s) == 2_100  # other categories are unaffected


def test_vat_home_mode_none_is_saved_and_shown_in_the_303(client):
    client.post("/settings", data=form(home_affected_pct="11", vat_home_mode="none"))
    assert load_settings().vat_home_mode == "none"
    assert '<option value="none" selected>' in text(client.get("/settings")).split('name="vat_home_mode"')[1].split("</select>")[0]
    assert "not deducted" in text(client.get("/reports/303?year=2026&q=1"))
    client.post("/settings", data=form(home_affected_pct="11", vat_home_mode="bogus"))
    assert load_settings().vat_home_mode == "share"


def test_rules_are_saved_and_shown(client):
    client.post("/settings", data=form(home_affected_pct="11", supplies_rule="share", property_rule="share_factor"))
    saved = load_settings()
    assert (saved.supplies_rule, saved.property_rule) == ("share", "share_factor")
    assert (saved.tax().supplies_rule, saved.tax().property_rule) == ("share", "share_factor")
    page = text(client.get("/settings"))
    assert '<option value="share" selected>' in page.split('name="supplies_rule"')[1].split("</select>")[0]
    assert '<option value="share_factor" selected>' in page.split('name="property_rule"')[1].split("</select>")[0]
    report = text(client.get("/reports/130?year=2026&q=1"))
    assert "Supplies: affected %;" in report and "rent/IBI/community: affected % × 30%." in report


def test_unknown_rule_values_fall_back_to_the_defaults(client):
    client.post("/settings", data=form(supplies_rule="nonsense", property_rule=""))
    saved = load_settings()
    assert (saved.supplies_rule, saved.property_rule) == ("share_factor", "share")


def test_changing_the_rent_rule_changes_the_deduction_in_the_ui(client):
    client.post("/settings", data=form(home_affected_pct="11", property_rule="share"))
    client.post("/entries/new/expense", data={"date": "2026-02-01", "counterparty": "Landlord", "regime": "no_vat",
                                             "base": "800", "category_id": str(category_id("rent")), "currency": "EUR"})
    assert "€88.00" in text(client.get("/entries?year=2026"))
    client.post("/settings", data=form(home_affected_pct="11", property_rule="share_factor"))
    page = text(client.get("/entries?year=2026"))
    assert "€26.40" in page and "€88.00" not in page


def test_cap_resets_each_calendar_year():
    es = [expense(50_000, d=date(2025, 12, 1), category="health", cap=50_000),
          expense(50_000, d=date(2026, 1, 5), category="health", cap=50_000)]
    assert calc.irpf_deductions(es, TaxSettings()) == [50_000, 50_000]


def test_cap_flows_into_130_and_annual():
    es = [income(1_000_000), expense(80_000, category="health", cap=50_000, d=date(2026, 2, 1)),
          expense(40_000, category="health", cap=50_000, d=date(2026, 5, 1))]
    s = TaxSettings(difficult_enabled=False)
    q1, q2 = calc.build_130_year(es, s, 2026, 2)
    assert q1.expenses_gross == 50_000 and q2.expenses_gross == 50_000  # the cap is already used up in Q1
    assert calc.build_annual(es, s, 2026).expenses_by_category == {"health": 50_000}


def test_not_registered_for_vat_deducts_no_iva():
    e = expense(10_000, 2_100)
    assert calc.vat_deductible(e, TaxSettings(vat_registered=True)) == 2_100
    s = TaxSettings(vat_registered=False)
    assert calc.vat_deductible(e, s) == 0
    assert calc.irpf_deductible(e, s) == 12_100  # IVA that cannot be recovered becomes part of the expense
    assert calc.build_303([e], s, 2026, 1).c45 == 0


def test_normal_method_has_no_difficult_justification():
    es = [income(1_000_000)]
    simplified = calc.build_130_year(es, TaxSettings(irpf_method="simplificada"), 2026, 1)[0]
    normal = calc.build_130_year(es, TaxSettings(irpf_method="normal"), 2026, 1)[0]
    assert simplified.difficult == 50_000 and normal.difficult == 0
    assert calc.build_annual(es, TaxSettings(irpf_method="normal"), 2026).difficult == 0


def test_irpf_rate_is_configurable():
    es = [income(1_000_000)]
    s = TaxSettings(irpf_method="normal", irpf_rate=Decimal("0.08"))  # Ceuta / Melilla
    assert calc.build_130_year(es, s, 2026, 1)[0].c04 == 80_000


# ---------------------------------------------------------------- deadlines

def test_direct_debit_deadlines():
    assert deadlines.quarter_filing("303", 2026, 1, domiciliacion=True).due == date(2026, 4, 15)
    assert deadlines.quarter_filing("130", 2026, 3, domiciliacion=True).due == date(2026, 10, 15)
    assert deadlines.quarter_filing("303", 2026, 4, domiciliacion=True).due == date(2027, 1, 27)
    assert deadlines.quarter_filing("303", 2026, 4).due == date(2027, 2, 1)  # Jan 30 is a Saturday


def test_holidays_move_deadlines():
    f = deadlines.quarter_filing("303", 2026, 1, holidays=[date(2026, 4, 20)])
    assert f.due == date(2026, 4, 21)
    f = deadlines.quarter_filing("303", 2026, 1, holidays=[date(2026, 4, 20), date(2026, 4, 21)])
    assert f.due == date(2026, 4, 22)


def test_tracked_models_filter():
    only_130 = deadlines.default_filings(2026, models=["130"])
    assert {(f.model) for f in only_130} == {"130"} and len(only_130) == 4
    assert deadlines.default_filings(2026, models=[]) == []
    no_vat = deadlines.default_filings(2026, models=["130", "100"])
    assert sorted((f.model, f.period) for f in no_vat)[-1] == ("130", "4T") and ("100", "ANUAL") in [(f.model, f.period) for f in no_vat]


def test_activity_start_skips_periods_before_it():
    start = date(2026, 5, 10)  # during 2T 2026
    fs = deadlines.default_filings(2026, start=start)
    periods = sorted((f.model, f.period) for f in fs if f.model in ("303", "130"))
    assert ("303", "1T") not in periods and ("130", "1T") not in periods
    assert ("303", "2T") in periods and ("130", "4T") in periods
    assert deadlines.default_filings(2025, start=start) == []  # nothing for years before the start
    assert any(f.model == "100" for f in deadlines.default_filings(2026, start=start))  # first Renta covers the start year


def test_parse_holidays():
    assert deadlines.parse_holidays("2026-12-08, 2027-01-06\n2026-12-08") == [date(2026, 12, 8), date(2027, 1, 6)]
    assert deadlines.parse_holidays("") == []
    with pytest.raises(ValueError):
        deadlines.parse_holidays("08.12.2026")


# ---------------------------------------------------------------- app

def form(**over):
    base = {"language": "en", "vat_registered": "1", "home_total_m2": "0", "home_affected_pct": "0",
            "supplies_factor_pct": "30", "difficult_pct_pct": "5", "difficult_cap_eur": "2000",
            "difficult_enabled": "1", "irpf_method": "simplificada", "irpf_rate_pct": "20",
            "default_vat_rate": "21", "default_withholding_rate": "0", "payment_method": "nrc",
            "tracked_models": ["303", "130", "390", "100"], "extra_holidays": ""}
    base.update(over)
    return base


def filings(model=None):
    con = db.connect()
    try:
        rows = con.execute("SELECT * FROM filings" + (" WHERE model=?" if model else ""),
                           (model,) if model else ()).fetchall()
        return {(r["year"], r["model"], r["period"]): r for r in rows}
    finally:
        con.close()


def text(r):
    return r.get_data(as_text=True)


def test_profile_settings_are_saved(client):
    r = client.post("/settings", data=form(irpf_method="normal", irpf_rate_pct="8", default_vat_rate="10",
                                            default_withholding_rate="7", extra_holidays="2026-12-08 2027-01-06"),
                    follow_redirects=True)
    assert "Settings saved." in text(r)
    saved = load_settings()
    assert saved.irpf_method == "normal" and saved.default_vat_rate == Decimal(10)
    assert saved.extra_holidays == (date(2026, 12, 8), date(2027, 1, 6))
    tax = saved.tax()
    assert tax.irpf_method == "normal" and tax.irpf_rate == Decimal("0.08")


def test_invalid_profile_values_are_rejected(client):
    r = client.post("/settings", data=form(irpf_method="módulos", irpf_rate_pct="150", extra_holidays="08.12.2026",
                                           language="klingon"))
    page = text(r)
    assert "Unknown IRPF method." in page and "between 0 and 100" in page
    assert "Holidays must be dates" in page and "Unknown language." in page


def test_stopping_to_track_a_model_removes_untouched_rows_only(client):
    client.get("/filings")
    year = date.today().year
    assert (year, "303", "1T") in filings()
    # mark one 303 as filed: it must survive even when 303 stops being tracked
    con = db.connect()
    con.execute("UPDATE filings SET status='filed' WHERE year=? AND model='303' AND period='2T'", (year,))
    con.commit()
    con.close()
    client.post("/settings", data=form(tracked_models=["130", "100"]))
    left = filings()
    assert (year, "303", "2T") in left and left[(year, "303", "2T")]["status"] == "filed"
    assert (year, "303", "1T") not in left and (year, "390", "ANUAL") not in left
    assert (year, "130", "1T") in left and (year, "100", "ANUAL") in left


def test_not_registered_for_vat_drops_vat_filings_and_changes_form_defaults(client):
    client.get("/filings")
    client.post("/settings", data={k: v for k, v in form().items() if k != "vat_registered"})
    models = {m for (_y, m, _p) in filings()}
    assert "303" not in models and "390" not in models and "130" in models
    page = text(client.get("/entries/new/income"))
    assert '<option value="exempt" selected>' in page  # income defaults to «no IVA»
    assert "Modelo 303" not in text(client.get("/"))  # dashboard hides the IVA card


def test_direct_debit_recomputes_pending_deadlines_but_not_hand_edited_ones(client):
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE year=? AND model='130' AND period='2T'", (year,)).fetchone()["id"]
    con.close()
    client.post(f"/filings/{fid}/edit", data={"status": "pending", "due_date": f"{year}-07-31", "filed_date": "",
                                              "reference": "", "result": "", "notes": ""})
    client.post("/settings", data=form(payment_method="domiciliacion", holder_iban="ES9121000418450200051332"))
    rows = filings()
    q1 = rows[(year, "303", "1T")]
    assert q1["due_date"] == deadlines.quarter_filing("303", year, 1, domiciliacion=True).due.isoformat()
    assert rows[(year, "130", "2T")]["due_date"] == f"{year}-07-31"  # edited by hand -> kept
    client.post("/settings", data=form(payment_method="nrc"))  # and back
    assert filings()[(year, "303", "1T")]["due_date"] == deadlines.quarter_filing("303", year, 1).due.isoformat()


def test_activity_start_prunes_earlier_untouched_filings_and_validates(client):
    client.get("/filings")
    year = date.today().year
    assert (year - 1, "303", "1T") in filings()
    client.post("/settings", data=form(activity_start=f"{year}-05-10"))
    left = filings()
    assert not any(y < year for (y, _m, _p) in left)  # last year has no filings any more
    assert (year, "303", "1T") not in left and (year, "303", "2T") in left
    bad = client.post("/settings", data=form(activity_start="10/05/2026"))
    assert "Invalid activity start date." in text(bad)


def test_extra_holidays_shift_default_deadlines(client):
    client.get("/filings")
    year = date.today().year
    plain = filings()[(year, "303", "3T")]["due_date"]
    client.post("/settings", data=form(extra_holidays=plain))
    shifted = filings()[(year, "303", "3T")]["due_date"]
    assert shifted > plain


def test_entry_form_uses_configured_defaults(client):
    client.post("/settings", data=form(default_vat_rate="10", default_withholding_rate="7"))
    page = text(client.get("/entries/new/income"))
    assert '<option value="10" selected>' in page and 'name="withholding_rate" id="withholding_rate" value="7"' in page


def test_category_cap_and_rename_via_the_ui(client):
    con = db.connect()
    cid = con.execute("SELECT id FROM categories WHERE code='health'").fetchone()["id"]
    con.close()
    shown = "Health insurance (seguro de salud)"  # the English label the page displays; unchanged -> still follows the language
    r = client.post(f"/categories/{cid}", data={"name": shown, "irpf_mode": "full", "vat_mode": "none",
                                               "annual_cap": "500,00", "notes": ""}, follow_redirects=True)
    assert "Category updated." in text(r)
    con = db.connect()
    row = con.execute("SELECT * FROM categories WHERE id=?", (cid,)).fetchone()
    con.close()
    assert row["annual_cap_cents"] == 50_000 and row["name"] == "health" and row["notes"] == ""  # untouched name/hint follow the language
    # two invoices of 400 € in the category: only 500 € is deductible in total
    for n in (1, 2):
        client.post("/entries/new/expense", data={"date": f"2026-0{n}-10", "counterparty": "Sanitas", "regime": "no_vat",
                                                 "base": "400", "vat_rate": "0", "category_id": str(cid), "currency": "EUR"})
    page = text(client.get("/entries?year=2026"))
    assert "€400.00" in page and "€100.00" in page  # second row is capped
    r = client.post(f"/categories/{cid}", data={"name": "Health", "irpf_mode": "full", "vat_mode": "none",
                                               "annual_cap": "abc"}, follow_redirects=True)
    assert "Invalid annual cap amount." in text(r)


@pytest.mark.parametrize("rate,stored", [("10", "10"), ("21", "21"), ("4", "4"), ("0", "0"), ("7.5", "7.5"), ("100", "100")])
def test_percentages_are_stored_as_plain_numbers(client, rate, stored):
    cid = db.connect().execute("SELECT id FROM categories WHERE code='software'").fetchone()["id"]
    client.post("/entries/new/expense", data={"date": "2026-02-10", "counterparty": "X", "regime": "domestic", "base": "100",
                                             "vat_rate": rate, "category_id": str(cid), "currency": "EUR"})
    con = db.connect()
    row = con.execute("SELECT vat_rate FROM entries").fetchone()
    con.close()
    assert row["vat_rate"] == stored  # never '1E+1'


def test_migration_repairs_exponent_rates(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path))
    con = db.connect()
    db.init_db(con)
    con.execute("INSERT INTO entries (kind, date, base_cents, vat_rate, withholding_rate) VALUES ('income','2026-01-01',100,'1E+1','2E+1')")
    con.commit()
    db.init_db(con)
    row = con.execute("SELECT vat_rate, withholding_rate FROM entries").fetchone()
    con.close()
    assert (row["vat_rate"], row["withholding_rate"]) == ("10", "20")


def test_new_category_with_cap(client):
    r = client.post("/categories", data={"name": "Coworking", "irpf_mode": "full", "vat_mode": "full",
                                         "annual_cap": "1200", "notes": "monthly desk"}, follow_redirects=True)
    assert "Category added." in text(r)
    con = db.connect()
    row = con.execute("SELECT * FROM categories WHERE name='Coworking'").fetchone()
    con.close()
    assert row["annual_cap_cents"] == 120_000 and row["code"] is None
