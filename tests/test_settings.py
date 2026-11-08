"""The typed settings object and its storage."""

from dataclasses import fields
from datetime import date
from decimal import Decimal

import pytest

from taxhelper import db
from taxhelper.settings import Settings, SettingsRepository, decode, encode
from tests.support import category_id, text
from tests.test_profile import form


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path))
    con = db.connect()
    db.init_db(con)
    yield SettingsRepository(con)
    con.close()


def test_defaults_when_nothing_is_stored(repo):
    s = repo.load()
    assert s == Settings()
    assert s.tax().irpf_rate == Decimal("0.20") and s.tax().vat_registered and s.auto_fx and not s.uses_direct_debit


def test_every_field_survives_a_save_and_load(repo):
    original = Settings(
        language="es", holder_nif="X1234567L", holder_surname="GARCIA LOPEZ", holder_firstname="ANA",
        holder_iban="ES9121000418450200051332", payment_method="domiciliacion", irpf_method="normal",
        irpf_rate_pct=Decimal("8"), vat_registered=False, tracked_models=("130", "100"),
        extra_holidays=(date(2026, 12, 8), date(2027, 1, 6)), activity_start=date(2026, 3, 1),
        default_vat_rate=Decimal("10"), default_withholding_rate=Decimal("7.5"), fx_mode="manual",
        home_total_m2=Decimal("88"), home_affected_pct=Decimal("11"), supplies_factor=Decimal("0.25"),
        supplies_rule="share", property_rule="share_factor", vat_home_mode="share_factor", difficult_enabled=False, difficult_pct=Decimal("0.07"),
        difficult_cap_cents=123_456, prev_year_net_yield_cents=987_650, backup_dir="D:/cloud",
        last_backup_at="2026-11-04 12:00", last_backup_error="boom",
        renta_personal_minimum_cents=655_000, renta_other_income_cents=120_050, renta_regional_scale="0 9.5\n12500 12.5")
    repo.save(original)
    assert repo.load() == original
    # nothing is forgotten when encoding (the money fields are stored in euros, hence the renamed keys)
    renamed = {"difficult_cap_cents": "difficult_cap_eur", "prev_year_net_yield_cents": "prev_year_net_yield_eur",
               "renta_personal_minimum_cents": "renta_personal_minimum_eur", "renta_other_income_cents": "renta_other_income_eur"}
    assert {renamed.get(f.name, f.name) for f in fields(Settings)} == set(encode(original))


def test_garbage_in_storage_falls_back_to_defaults():
    s = decode({"irpf_rate_pct": "lots", "payment_method": "cheque", "irpf_method": "módulos", "fx_mode": "?",
                "vat_home_mode": "x", "supplies_rule": "?", "property_rule": "x", "home_total_m2": "NaN", "difficult_cap_eur": "many", "activity_start": "yesterday",
                "extra_holidays": "08.12.2026", "tracked_models": "303,999,130", "default_vat_rate": "Infinity",
                "prev_year_net_yield_eur": "n/a", "vat_registered": "yes"})
    d = Settings()
    assert (s.irpf_rate_pct, s.payment_method, s.irpf_method, s.fx_mode) == (d.irpf_rate_pct, "nrc", "simplificada", "auto")
    assert (s.vat_home_mode, s.home_total_m2, s.difficult_cap_cents, s.activity_start) == ("share", 0, 200_000, None)
    assert (s.supplies_rule, s.property_rule) == ("share_factor", "share")
    assert s.extra_holidays == () and s.tracked_models == ("303", "130") and s.default_vat_rate == d.default_vat_rate
    assert s.prev_year_net_yield_cents is None and s.vat_registered is True  # only '0'/'1' are understood


def test_an_empty_tracked_list_means_track_nothing(repo):
    repo.update(tracked_models=())
    assert repo.load().tracked_models == ()


def test_update_changes_only_the_named_fields(repo):
    repo.update(language="ru", holder_nif="X1234567L")
    s = repo.update(irpf_method="normal")
    assert (s.language, s.holder_nif, s.irpf_method) == ("ru", "X1234567L", "normal")
    assert repo.load() == s


def test_update_rejects_unknown_fields(repo):
    with pytest.raises(TypeError):
        repo.update(colour="blue")


def test_old_office_area_is_converted_to_the_declared_percentage_once():
    # earlier versions stored the office area; the percentage is what modelo 036 declares
    assert decode({"home_total_m2": "88", "home_affected_m2": "9.68"}).home_affected_pct == Decimal("11.00")
    assert decode({"home_total_m2": "100", "home_affected_m2": "20"}).home_affected_pct == Decimal("20.00")
    assert decode({"home_total_m2": "50", "home_affected_m2": "80"}).home_affected_pct == Decimal(100)  # capped
    assert decode({"home_total_m2": "0", "home_affected_m2": "5"}).home_affected_pct == 0  # no surface, no share
    # once a percentage is saved it wins over any leftover area
    assert decode({"home_total_m2": "88", "home_affected_m2": "40", "home_affected_pct": "11"}).home_affected_pct == 11


def test_the_percentage_is_stored_as_typed_and_drives_the_share(repo):
    repo.update(home_total_m2=Decimal("88"), home_affected_pct=Decimal("11.5"))
    s = repo.load()
    assert (s.home_total_m2, s.home_affected_pct) == (Decimal("88"), Decimal("11.5"))
    assert s.tax().affected_share == Decimal("0.115")
    assert encode(s)["home_affected_pct"] == "11.5"


def test_old_storage_formats_are_still_read():
    # values written by earlier versions of the app: euros as text with commas, 1/0 flags, percent strings
    s = decode({"difficult_cap_eur": "2.000,00", "prev_year_net_yield_eur": "8500,50", "supplies_factor": "0.3",
                "difficult_pct": "0.05", "irpf_rate_pct": "20", "vat_registered": "0", "difficult_enabled": "1"})
    assert (s.difficult_cap_cents, s.prev_year_net_yield_cents) == (200_000, 850_050)
    assert (s.supplies_factor, s.difficult_pct, s.vat_registered) == (Decimal("0.3"), Decimal("0.05"), False)


def test_carry_in_is_kept_per_year(repo):
    assert repo.carry_in(2026) == 0 and repo.carry_in_text(2026) == ""
    repo.set_carry_in(2026, " 1.234,56 ")
    repo.set_carry_in(2027, "10")
    assert repo.carry_in(2026) == 123_456 and repo.carry_in(2027) == 1_000 and repo.carry_in(2025) == 0
    repo.set_carry_in(2026, "not a number")
    assert repo.carry_in(2026) == 0  # unreadable text counts as no balance, it never crashes the reports


@pytest.mark.parametrize("bad", ["NaN", "nan", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_numbers_are_rejected_not_500(client, bad):
    r = client.post("/settings", data=form(irpf_rate_pct=bad))
    assert r.status_code == 200 and "Invalid percentage" in text(r)
    r = client.post("/settings", data=form(home_total_m2=bad))
    assert r.status_code == 200 and "Areas must be numbers." in text(r)
    r = client.post("/settings", data=form(home_affected_pct=bad))
    assert r.status_code == 200 and "Invalid percentage" in text(r)
    r = client.post("/entries/new/expense", data={"date": "2026-02-10", "base": "10", "regime": "domestic",
                                                 "vat_rate": bad, "category_id": str(category_id("software"))})
    assert r.status_code == 200 and "Invalid percentage" in text(r)
    r = client.post("/api/preview", json={"kind": "expense", "regime": "domestic", "base": "10", "vat_rate": bad})
    assert r.status_code == 200 and r.get_json() == {"ok": False}


def test_the_settings_cache_is_dropped_on_every_write(repo):
    assert repo.load().language == ""
    repo.set_raw("language", "es")
    assert repo.load().language == "es"
    repo.update(language="ru")
    assert repo.load().language == "ru"
    repo.set_carry_in(2026, "10")
    assert repo.carry_in(2026) == 1_000
    assert repo.load() is repo.load()  # reads once between writes


@pytest.mark.parametrize("registered,kind,expected", [
    (True, "expense", "domestic"), (True, "income", "domestic"),
    (False, "expense", "no_vat"), (False, "income", "exempt")])
def test_default_regime_follows_the_iva_registration(registered, kind, expected):
    assert Settings(vat_registered=registered).default_regime(kind) == expected
