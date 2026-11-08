"""Unit tests for the pure tax calculations. Expected numbers are worked out by hand."""

from datetime import date
from decimal import Decimal

import pytest

from taxhelper import calc, i18n
from taxhelper.calc import Entry, TaxSettings
from tests.support import entry


HOME = TaxSettings(home_affected_pct=Decimal(20))  # 20% share


def expense(base, vat=0, rate=0, d=date(2026, 2, 10), regime="domestic", irpf="full", vatm="full", **kw):
    return Entry(kind="expense", date=d, regime=regime, base_cents=base, vat_rate=Decimal(rate),
                 vat_cents=vat, irpf_mode=irpf, vat_mode=vatm, **kw)


def income(base, vat=0, rate=0, wh=0, d=date(2026, 2, 10), regime="domestic"):
    return Entry(kind="income", date=d, regime=regime, base_cents=base, vat_rate=Decimal(rate),
                 vat_cents=vat, withholding_cents=wh)


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize("text,cents", [
    ("1.234,56", 123456), ("1234.56", 123456), ("1 234,56", 123456), ("12", 1200),
    ("-12,5", -1250), ("0,005", 1), ("1,234.56", 123456), ("20 €", 2000),
])
def test_parse_money(text, cents):
    assert calc.parse_money(text) == cents


@pytest.mark.parametrize("bad", ["", None, "abc", "1,2,3x"])
def test_parse_money_rejects_garbage(bad):
    with pytest.raises(ValueError):
        calc.parse_money(bad)


@pytest.mark.parametrize("value,text", [(10, "10"), (21, "21"), (4, "4"), (0, "0"), (Decimal("7.50"), "7.5"),
                                        (Decimal("100"), "100"), (Decimal("20.00"), "20")])
def test_fmt_percent_never_uses_exponents(value, text):
    assert calc.fmt_percent(value) == text


def test_round_half_up():
    assert calc.round_cents(Decimal("2.5")) == 3
    assert calc.round_cents(Decimal("2.4")) == 2


def test_quarter_helpers():
    assert [calc.quarter_of(date(2026, m, 1)) for m in (1, 3, 4, 6, 7, 9, 10, 12)] == [1, 1, 2, 2, 3, 3, 4, 4]
    assert calc.quarter_end(2026, 2) == date(2026, 6, 30)
    assert calc.quarter_end(2026, 4) == date(2026, 12, 31)


# ---------------------------------------------------------------- per-entry rules

def test_affected_share_bounds():
    assert TaxSettings().affected_share == 0
    assert TaxSettings(home_affected_pct=Decimal(180)).affected_share == 1


def test_regular_subscription_fully_deductible():
    e = expense(2000, 420, 21)
    assert calc.vat_deductible(e, HOME) == 420
    assert calc.irpf_deductible(e, HOME) == 2000


def test_home_supply_irpf_30_percent_of_share():
    # 100,00 + 21,00 IVA. IVA deductible by share only: 21 * 20% = 4,20.
    # cost = 121,00 - 4,20 = 116,80; IRPF = 116,80 * 20% * 30% = 7,008 -> 7,01
    e = expense(10000, 2100, 21, irpf="home_supply", vatm="home_supply")
    assert calc.vat_deductible(e, HOME) == 420
    assert calc.irpf_deductible(e, HOME) == 701


def test_home_supply_vat_with_factor_mode():
    s = TaxSettings(home_affected_pct=Decimal(20), vat_home_mode="share_factor")
    e = expense(10000, 2100, 21, irpf="home_supply", vatm="home_supply")
    # IVA ded = 21 * 20% * 30% = 1,26 ; cost = 121 - 1,26 = 119,74 ; IRPF = 119,74 * 6% = 7,1844 -> 7,18
    assert calc.vat_deductible(e, s) == 126
    assert calc.irpf_deductible(e, s) == 718


def test_home_property_uses_share_without_30_percent():
    e = expense(80000, 0, 0, irpf="home_property", vatm="none")  # rent 800
    assert calc.irpf_deductible(e, HOME) == 16000  # 20%


def test_home_rules_give_zero_when_office_not_configured():
    e = expense(10000, 2100, 21, irpf="home_supply", vatm="home_supply")
    assert calc.vat_deductible(e, TaxSettings()) == 0
    assert calc.irpf_deductible(e, TaxSettings()) == 0


def test_non_deductible_vat_becomes_cost():
    e = expense(10000, 2100, 21, vatm="none")  # IVA not recoverable -> part of expense
    assert calc.vat_deductible(e, HOME) == 0
    assert calc.irpf_deductible(e, HOME) == 12100


def test_none_mode_and_income_are_zero():
    assert calc.irpf_deductible(expense(5000, irpf="none"), HOME) == 0
    assert calc.irpf_deductible(income(5000), HOME) == 0
    assert calc.vat_deductible(income(5000, 1050, 21), HOME) == 0


def test_reverse_charge_vat_is_self_assessed_and_cost_is_base_only():
    vat = calc.compute_vat("eu_reverse", 2000, Decimal(21), None)
    assert vat == 420
    e = expense(2000, vat, 21, regime="eu_reverse")
    assert calc.vat_deductible(e, HOME) == 420
    assert calc.irpf_deductible(e, HOME) == 2000


def test_compute_vat_domestic_prefers_entered_amount():
    assert calc.compute_vat("domestic", 10000, Decimal(21), None) == 2100
    assert calc.compute_vat("domestic", 10000, Decimal(21), 2099) == 2099
    assert calc.compute_vat("no_vat", 10000, Decimal(21), 2100) == 0
    assert calc.compute_vat("eu_b2b", 10000, Decimal(21), None) == 0


# ---------------------------------------------------------------- modelo 303

def test_303_basic_quarter():
    entries = [
        income(10000, 2100, 21),
        expense(4000, 840, 21),
        income(5000, 1050, 21, d=date(2026, 5, 1)),  # other quarter, ignored
    ]
    r = calc.build_303(entries, TaxSettings(), 2026, 1)
    assert r.by_rate[21] == [10000, 2100]
    assert r.c27 == 2100
    assert (r.c28, r.c29, r.c45) == (4000, 840, 840)
    assert r.c46 == 1260 and r.c71 == 1260 and r.c72 == 0


def test_303_eu_reverse_charge_nets_to_zero_and_warns_349():
    e = expense(2000, 420, 21, regime="eu_reverse")
    r = calc.build_303([e], TaxSettings(), 2026, 1)
    assert (r.c10, r.c11, r.c36, r.c37) == (2000, 420, 2000, 420)
    assert r.c27 == 420 and r.c45 == 420 and r.c71 == 0
    assert any(w.key == "warn.349_purchases" for w in r.warnings)


def test_303_non_eu_reverse_uses_12_13_and_28_29():
    e = expense(2000, 420, 21, regime="non_eu_reverse")
    r = calc.build_303([e], TaxSettings(), 2026, 1)
    assert (r.c12, r.c13, r.c28, r.c29) == (2000, 420, 2000, 420)
    assert r.c71 == 0


def test_303_home_supply_deducts_only_share_of_vat():
    e = expense(10000, 2100, 21, irpf="home_supply", vatm="home_supply")
    r = calc.build_303([e], HOME, 2026, 1)
    assert r.c29 == 420 and r.c71 == -420 and r.c72 == 420


def test_303_negative_result_is_carried_and_applied():
    entries = [expense(4000, 840, 21), income(10000, 2100, 21, d=date(2026, 5, 2))]
    q1, q2 = calc.build_303_year(entries, TaxSettings(), 2026, 2)
    assert q1.c71 == -840 and q1.c72 == 840 and q1.carry_out == 840
    assert q2.c66 == 2100 and q2.c78 == 840 and q2.c71 == 1260 and q2.carry_out == 0


def test_303_opening_carry_from_previous_year():
    entries = [income(10000, 2100, 21)]
    r = calc.build_303_year(entries, TaxSettings(), 2026, 1, opening_carry_cents=500)[0]
    assert r.c78 == 500 and r.c71 == 1600


def test_303_foreign_income_goes_to_info_fields():
    entries = [income(7000, regime="eu_b2b"), income(3000, regime="non_eu"), income(1000, regime="exempt")]
    r = calc.build_303(entries, TaxSettings(), 2026, 1)
    assert (r.info_eu_services_base, r.info_non_eu_base, r.info_exempt_base) == (7000, 3000, 1000)
    assert r.c27 == 0


# ---------------------------------------------------------------- modelo 130

def test_130_q1_and_q2_cumulative():
    entries = [
        income(1_000_000, d=date(2026, 2, 1)), expense(400_000, d=date(2026, 2, 2)),
        income(1_000_000, d=date(2026, 5, 1)), expense(400_000, d=date(2026, 5, 2)),
    ]
    q1, q2 = calc.build_130_year(entries, TaxSettings(), 2026, 2)
    # Q1: 5% of 600.000 = 30.000 difficult; c02=430.000; c03=570.000; c04=114.000
    assert (q1.difficult, q1.c02, q1.c03, q1.c04, q1.c07, q1.c19) == (30_000, 430_000, 570_000, 114_000, 114_000, 114_000)
    # Q2: YTD income 2.000.000, exp 800.000; difficult 5% of 1.200.000 = 60.000
    assert (q2.difficult, q2.c02, q2.c03, q2.c04) == (60_000, 860_000, 1_140_000, 228_000)
    assert q2.c05 == 114_000 and q2.c07 == 114_000 and q2.c19 == 114_000


def test_130_difficult_justification_cap_and_switch():
    big = TaxSettings()
    assert calc.difficult_provision(10_000_000, big) == 200_000  # 5% of 100k EUR capped at 2.000 EUR
    assert calc.difficult_provision(-5, big) == 0
    assert calc.difficult_provision(100_000, TaxSettings(difficult_enabled=False)) == 0


def test_130_withholdings_exceeding_tax_give_zero_and_are_not_double_counted():
    entries = [
        income(100_000, wh=30_000, d=date(2026, 2, 1)),
        income(100_000, d=date(2026, 5, 1)),
    ]
    q1, q2 = calc.build_130_year(entries, TaxSettings(), 2026, 2)
    # Q1: c02=5.000 (5%), c03=95.000, c04=19.000, c06=30.000 -> c07=-11.000, but c12 floors at 0
    assert q1.c07 == -11_000 and q1.c12 == 0 and q1.c19 == 0
    # Q2 is cumulative, so the surplus withholding is already absorbed through c06:
    # c01=200.000, c02=10.000, c03=190.000, c04=38.000, c05=0 (negative c07 not counted), c06=30.000
    assert q2.c05 == 0 and q2.c07 == 8_000 and q2.c19 == 8_000


def test_130_negative_after_minoracion_is_carried_via_casilla_15():
    s = TaxSettings(prev_year_net_yield_cents=800_000)  # <= 9.000 EUR -> 100 EUR minoracion
    entries = [
        income(100_000, wh=30_000, d=date(2026, 2, 1)),
        income(1_000_000, d=date(2026, 5, 1)),
    ]
    q1, q2 = calc.build_130_year(entries, s, 2026, 2)
    # Q1: c12=0, c13=10.000 -> c14=-10.000 = c19, carried
    assert q1.c13 == 10_000 and q1.c14 == -10_000 and q1.c19 == -10_000
    # Q2: YTD income 1.100.000, c02=55.000, c03=1.045.000, c04=209.000, c06=30.000 -> c07=179.000
    assert q2.c07 == 179_000 and q2.c14 == 169_000
    assert q2.c15 == 10_000 and q2.c17 == 159_000 and q2.c19 == 159_000


def test_130_loss_gives_zero_payment():
    entries = [income(100_000), expense(500_000)]
    (q1,) = calc.build_130_year(entries, TaxSettings(), 2026, 1)
    assert q1.c03 == -400_000 and q1.c04 == 0 and q1.c19 == 0 and q1.difficult == 0


def test_130_minoracion_table():
    f = calc.minoracion_110_3c
    assert f(None) == 0 and f(900_000) == 10_000 and f(900_001) == 7_500
    assert f(1_000_000) == 7_500 and f(1_100_000) == 5_000 and f(1_200_000) == 2_500 and f(1_200_001) == 0


def test_130_home_office_expense_flows_through():
    entries = [income(1_000_000), expense(10000, 2100, 21, irpf="home_supply", vatm="home_supply")]
    (q1,) = calc.build_130_year(entries, HOME, 2026, 1)
    assert q1.expenses_gross == 701  # see test_home_supply_irpf_30_percent_of_share


# ---------------------------------------------------------------- annual

def test_annual_summary():
    entries = [
        income(1_000_000, wh=150_000, d=date(2026, 3, 1)),
        expense(100_000, 21_000, 21, d=date(2026, 3, 2), category="Софт"),
        expense(50_000, 0, 0, d=date(2026, 6, 2), category="RETA", vatm="none"),
        income(999, d=date(2025, 12, 31)),  # other year, ignored
    ]
    a = calc.build_annual(entries, TaxSettings(), 2026)
    assert a.income == 1_000_000
    assert a.expenses_by_category == {"Софт": 100_000, "RETA": 50_000}
    assert a.net_before_difficult == 850_000
    assert a.difficult == 42_500  # 5% of 850.000
    assert a.net_yield == 807_500
    assert a.withholdings == 150_000


@pytest.mark.parametrize("rate", ["10.5", "4.9", "5", "7.5", "21.5"])
def test_non_whole_rates_are_not_forced_into_a_standard_box(rate):
    r = calc.build_303([entry("income", 10_000, 1_050, rate)], TaxSettings(), 2026, 1)
    assert all(v == [0, 0] for v in r.by_rate.values()) and r.c27 == 0
    assert [w.key for w in r.warnings] == ["warn.vat_rate_unsupported"]


@pytest.mark.parametrize("rate,box", [("21", 21), ("21.00", 21), ("10", 10), ("4.0", 4)])
def test_whole_rates_still_land_in_their_box(rate, box):
    r = calc.build_303([entry("income", 10_000, 1_000, rate)], TaxSettings(), 2026, 1)
    assert r.by_rate[box] == [10_000, 1_000] and not r.warnings


def test_credit_note_with_partial_home_deduction_is_proportional():
    s = TaxSettings(home_affected_pct=Decimal(20))
    e = Entry(kind="expense", date=date(2026, 2, 1), regime="domestic", base_cents=-10_000, vat_cents=-2_100,
              vat_rate=Decimal(21), vat_mode="home_supply", category="x")
    r = calc.build_303([e], s, 2026, 1)
    assert r.c41 == -420 and r.c40 == -2_000


def test_non_finite_amounts_are_rejected():
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError):
            calc.parse_money(bad)


@pytest.mark.parametrize("language,typed,cents", [
    ("es", "1.500", 150_000), ("es", "1,500", 150), ("es", "12.345", 1_234_500), ("es", "1.234.567", 123_456_700),
    ("es", "0,500", 50), ("es", ",5", 50), ("es", "1,5", 150), ("es", "1.234,50", 123_450),
    ("en", "1,500", 150_000), ("en", "1.500", 150), ("en", "1,234,567", 123_456_700), ("en", "0.500", 50),
    ("en", "1234.567", 123_457), ("en", "1.5", 150), ("en", ".5", 50), ("en", "-1,234.50", -123_450),
    ("ru", "1.500", 150_000), ("ru", "1,5", 150), ("ru", "1 234,56", 123_456), ("ru", "1 234,56", 123_456),
    ("ru", "20 €", 2_000), ("ru", "+7", 700)])
def test_a_lone_separator_follows_the_users_language(language, typed, cents):
    i18n.set_language(language)
    assert calc.parse_money(typed) == cents


def test_an_explicit_decimal_separator_overrides_the_language():
    i18n.set_language("es")
    assert calc.parse_money("1.500", decimal_separator=".") == 150
    assert calc.parse_money("1,500", decimal_separator=",") == 150


@pytest.mark.parametrize("bad", ["1.2.3", "12.34.567", "1,23,456", "1..5", "1.5.", "1,5,", "1.234.56", "abc", "1e5",
                                 "--5", "+-5", "", " ", "€", "NaN", "Infinity", "1 2 a"])
def test_malformed_amounts_are_rejected(bad):
    with pytest.raises(ValueError):
        calc.parse_money(bad)
