"""Closed sets of codes: members equal their stored text, unknown stored codes are caught on load."""
import json

import pytest

from support import expense_form, load_settings, set_settings
from taxhelper import calc, db
from taxhelper.codes import DeductionMode, FilingStatus, FxMode, HomeRule, IrpfMethod, Kind, PaymentMethod, Regime
from taxhelper.repositories import EntryRepository
from taxhelper.codes import VatHomeMode, VatNegative
from taxhelper.settings import decode

ALL = [Kind, Regime, DeductionMode, HomeRule, VatHomeMode, IrpfMethod, PaymentMethod, VatNegative, FxMode, FilingStatus]


@pytest.mark.parametrize("enum", ALL)
def test_members_equal_their_stored_text_and_round_trip(enum):
    for member in enum:
        assert member == member.value and str(member) == member.value and enum(member.value) is member
        assert json.loads(json.dumps(member)) == member.value
    with pytest.raises(ValueError):
        enum("no-such-code")


def test_the_stored_codes_are_the_ones_the_database_and_locales_use():
    """A renamed member value would orphan saved data and translations: the values are fixed here."""
    assert [m.value for m in Regime] == ["domestic", "exempt", "eu_b2b", "non_eu", "eu_reverse", "non_eu_reverse", "no_vat"]
    assert [m.value for m in DeductionMode] == ["full", "home_supply", "home_property", "none"]
    assert [m.value for m in FilingStatus] == ["pending", "filed", "not_required"]
    assert [m.value for m in Kind] == ["income", "expense"]
    assert [m.value for m in PaymentMethod] == ["nrc", "domiciliacion"]
    assert [m.value for m in VatNegative] == ["compensate", "refund"]
    assert [m.value for m in FxMode] == ["auto", "manual"]
    assert [m.value for m in IrpfMethod] == ["simplificada", "normal"]
    assert [m.value for m in HomeRule] == ["share", "share_factor"]
    assert [m.value for m in VatHomeMode] == ["share", "share_factor", "none"]


def test_regime_lists_cover_the_enum():
    assert set(calc.INCOME_REGIMES) | set(calc.EXPENSE_REGIMES) == set(Regime)


def test_settings_hold_members_and_are_stored_as_plain_text(client):
    saved = set_settings(payment_method="domiciliacion", vat_negative_4t="refund", supplies_rule="share", fx_mode="manual")
    assert saved.payment_method is PaymentMethod.DOMICILIACION and saved.vat_negative_4t is VatNegative.REFUND
    con = db.connect()
    stored = dict(con.execute("SELECT key, value FROM settings").fetchall())
    con.close()
    assert stored["payment_method"] == "domiciliacion" and stored["fx_mode"] == "manual"
    reloaded = load_settings()
    assert reloaded.supplies_rule is HomeRule.SHARE and reloaded.fx_mode is FxMode.MANUAL


def test_an_unknown_setting_value_falls_back_to_the_default():
    loaded = decode({"payment_method": "bitcoin", "irpf_method": "x", "fx_mode": "", "vat_negative_4t": "?", "vat_home_mode": "?"})
    assert loaded.payment_method is PaymentMethod.NRC and loaded.irpf_method is IrpfMethod.SIMPLIFICADA
    assert loaded.fx_mode is FxMode.AUTO and loaded.vat_negative_4t is VatNegative.COMPENSATE
    assert loaded.vat_home_mode is VatHomeMode.SHARE


@pytest.mark.parametrize("column, bad", [("regime", "reverse"), ("kind", "other")])
def test_an_entry_with_an_unknown_code_is_reported_by_id_when_loaded(client, column, bad):
    client.post("/entries/new/expense", data=expense_form(base="10"))
    con = db.connect()
    con.execute("PRAGMA ignore_check_constraints = ON")  # the `kind` column has a CHECK; damaged files may lack it
    con.execute(f"UPDATE entries SET {column} = ?", (bad,))
    con.commit()
    year = con.execute("SELECT substr(date, 1, 4) FROM entries").fetchone()[0]
    with pytest.raises(ValueError, match=rf"entry 1 of 2026-02-10: '{bad}' is not a valid"):
        EntryRepository(con).year_entries(int(year))
    con.close()


def test_an_unknown_category_mode_is_reported_when_loaded(client):
    client.post("/entries/new/expense", data=expense_form(base="10"))
    con = db.connect()
    con.execute("UPDATE categories SET irpf_mode = 'sometimes'")
    con.commit()
    with pytest.raises(ValueError, match="entry 1 .*'sometimes' is not a valid DeductionMode"):
        EntryRepository(con).year_entries(2026)
    con.close()


def test_filing_status_codes_match_the_database_check(client):
    con = db.connect()
    for status in FilingStatus:
        con.execute("INSERT INTO filings (year, model, period, opens, due_date, status) VALUES (2026, '303', ?, '2026-04-01', '2026-04-20', ?)",
                    (f"p-{status}", status))
    assert con.execute("SELECT COUNT(*) FROM filings WHERE period LIKE 'p-%'").fetchone()[0] == len(FilingStatus)
    con.close()
