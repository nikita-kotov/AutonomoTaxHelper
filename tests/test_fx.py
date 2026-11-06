"""Foreign-currency invoices: conversion, rate lookup and caching, the entry form flow."""
import io
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal

import pytest
from support import category_id, set_settings, text

from taxhelper import db, fx
from taxhelper.fx import FrankfurterProvider, FxError, Rate, RateService, convert_to_eur_cents
from taxhelper.repositories import FxRateCache


# ---------------------------------------------------------------- conversion

@pytest.mark.parametrize("cents,rate,euros", [
    (11_000, "1.10", 10_000), (100_000, "1.1805", 84_710), (5, "2", 3),   # 0.025 -> half-up to 3
    (1, "3", 0), (-11_000, "1.10", -10_000), (10_000, "1", 10_000)])
def test_conversion_divides_and_rounds_half_up(cents, rate, euros):
    assert convert_to_eur_cents(cents, rate) == euros


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", "abc", ""])
def test_rates_must_be_positive_and_finite(bad):
    with pytest.raises(ValueError):
        convert_to_eur_cents(100, bad) if bad not in ("abc", "") else fx.parse_rate(bad)
    with pytest.raises(ValueError):
        fx.parse_rate(bad)


def test_parse_rate_accepts_comma():
    assert fx.parse_rate("1,1805") == Decimal("1.1805")


# ---------------------------------------------------------------- service and cache

class CountingProvider:
    def __init__(self):
        self.calls = 0

    def fetch(self, currency, on):
        self.calls += 1
        return Rate(currency, Decimal("1.25"), on)


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path))
    con = db.connect()
    db.init_db(con)
    yield FxRateCache(con)
    con.close()


def test_lookup_fetches_once_then_uses_the_cache(cache):
    provider = CountingProvider()
    service = RateService(cache, provider)
    first = service.lookup("USD", date(2026, 3, 2))
    second = service.lookup("USD", date(2026, 3, 2))
    assert provider.calls == 1 and first == second and first.per_eur == Decimal("1.25")
    service.lookup("USD", date(2026, 3, 3))
    assert provider.calls == 2  # another date is another lookup


def test_euro_needs_no_provider_and_manual_mode_never_goes_online(cache):
    assert RateService(cache, None).lookup("EUR", date(2026, 3, 2)).per_eur == 1
    with pytest.raises(FxError):
        RateService(cache, None).lookup("USD", date(2026, 3, 2))


def test_cache_keeps_the_publication_date(cache):
    cache.put(Rate("USD", Decimal("1.1805"), date(2026, 2, 27)), date(2026, 3, 1))
    got = cache.get("USD", date(2026, 3, 1))
    assert got == Rate("USD", Decimal("1.1805"), date(2026, 2, 27), "ecb")
    assert cache.get("USD", date(2026, 3, 2)) is None


# ---------------------------------------------------------------- the online provider

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def serve(monkeypatch, body=None, error=None):
    def fake_urlopen(url, timeout=0):
        fake_urlopen.url = url
        if error:
            raise error
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return fake_urlopen


def test_provider_parses_rates_without_float_errors(monkeypatch):
    seen = serve(monkeypatch, {"amount": 1.0, "base": "EUR", "date": "2026-02-27", "rates": {"USD": 1.1805}})
    rate = FrankfurterProvider().fetch("USD", date(2026, 3, 1))
    assert rate == Rate("USD", Decimal("1.1805"), date(2026, 2, 27), "ecb")
    assert "2026-03-01" in seen.url and "symbols=USD" in seen.url and "base=EUR" in seen.url


@pytest.mark.parametrize("code", [404, 422])
def test_provider_unknown_currency_or_date(monkeypatch, code):
    serve(monkeypatch, error=urllib.error.HTTPError("u", code, "nf", {}, None))
    with pytest.raises(FxError) as exc:
        FrankfurterProvider().fetch("XXX", date(2026, 3, 1))
    assert exc.value.msg.key == "fx.err_unknown"


@pytest.mark.parametrize("error", [urllib.error.URLError("offline"), TimeoutError(), OSError("down"),
                                   urllib.error.HTTPError("u", 500, "boom", {}, None)])
def test_provider_network_problems(monkeypatch, error):
    serve(monkeypatch, error=error)
    with pytest.raises(FxError) as exc:
        FrankfurterProvider().fetch("USD", date(2026, 3, 1))
    assert exc.value.msg.key == "fx.err_network"


@pytest.mark.parametrize("body", [{}, {"rates": {}, "date": "2026-02-27"}, {"rates": {"USD": 0}, "date": "2026-02-27"},
                                  {"rates": {"USD": 1.1}, "date": "not-a-date"}])
def test_provider_rejects_malformed_replies(monkeypatch, body):
    serve(monkeypatch, body)
    with pytest.raises(FxError):
        FrankfurterProvider().fetch("USD", date(2026, 3, 1))


# ---------------------------------------------------------------- /api/fx

def test_fx_endpoint_returns_rate_and_publication_date(client, rates):
    j = client.get("/api/fx?currency=usd&date=2026-03-01").get_json()  # a Sunday
    assert j == {"ok": True, "rate": "1.1", "rate_date": "2026-02-27", "source": "ecb"}
    client.get("/api/fx?currency=USD&date=2026-03-01")
    assert len(rates.requests) == 1  # second request came from the cache


@pytest.mark.parametrize("query,reason", [
    ("currency=JPY&date=2026-03-02", "No ECB rate for JPY"), ("currency=US&date=2026-03-02", "3-letter code"),
    ("currency=USD&date=nope", "valid date"), ("currency=&date=2026-03-02", "3-letter code")])
def test_fx_endpoint_errors(client, query, reason):
    j = client.get(f"/api/fx?{query}").get_json()
    assert j["ok"] is False and reason in j["reason"]


def test_fx_endpoint_respects_manual_mode(client, rates):
    set_settings(fx_mode="manual")
    j = client.get("/api/fx?currency=USD&date=2026-03-02").get_json()
    assert j["ok"] is False and "switched off" in j["reason"] and not rates.requests


# ---------------------------------------------------------------- entry form

def post_expense(client, **over):
    data = {"date": "2026-03-02", "counterparty": "Anthropic", "regime": "non_eu_reverse", "vat_rate": "21",
            "category_id": str(category_id("software")), "currency": "USD", "orig_amount": "110,00",
            "fx_rate": "1.10", "fx_rate_date": "2026-03-02", "fx_source": "ecb"}
    data.update(over)
    return client.post("/entries/new/expense", data=data, follow_redirects=True)


def stored():
    con = db.connect()
    try:
        return dict(con.execute("SELECT * FROM entries ORDER BY id DESC").fetchone())
    finally:
        con.close()


def test_blank_base_is_computed_from_amount_and_rate(client):
    r = post_expense(client, base="")
    assert "Entry added." in text(r)
    row = stored()
    assert (row["base_cents"], row["orig_amount_cents"], row["currency"]) == (10_000, 11_000, "USD")
    assert (row["fx_rate"], row["fx_rate_date"], row["fx_source"]) == ("1.1", "2026-03-02", "ecb")
    assert row["vat_cents"] == 2_100  # reverse charge: 21% of the euro base


def test_entered_base_wins_but_a_mismatch_is_warned_about(client):
    r = post_expense(client, base="95,00")
    page = text(r)
    assert "Entry added." in page and "differs from the converted amount" in page and "€95.00" in page
    assert stored()["base_cents"] == 9_500
    r = post_expense(client, base="100,00")  # equal to the conversion: no warning
    assert "differs" not in text(r)


def test_typed_rate_is_stored_as_manual(client):
    post_expense(client, base="", fx_rate="1,25", fx_source="ecb", fx_rate_date="")  # ecb without a date cannot be trusted
    row = stored()
    assert row["fx_source"] == "manual" and row["fx_rate_date"] == "" and row["base_cents"] == 8_800


@pytest.mark.parametrize("over,message", [
    ({"fx_rate": ""}, "enter the amount in that currency and the exchange rate"),
    ({"orig_amount": ""}, "enter the amount in that currency and the exchange rate"),
    ({"fx_rate": "0"}, "Invalid exchange rate"), ({"fx_rate": "NaN"}, "Invalid exchange rate"),
    ({"orig_amount": "abc"}, "Invalid amount in the invoice currency"),
    ({"currency": "DOLLAR"}, "3-letter code")])
def test_foreign_invoice_validation(client, over, message):
    assert message in text(post_expense(client, base="", **over))


def test_euro_entries_ignore_stale_fx_fields(client):
    post_expense(client, currency="EUR", base="50", regime="domestic")
    row = stored()
    assert row["orig_amount_cents"] is None and row["fx_rate"] == "" and row["fx_source"] == "" and row["currency"] == "EUR"


def test_preview_derives_the_base_unless_it_was_typed(client):
    payload = {"kind": "expense", "regime": "domestic", "vat_rate": "21", "currency": "USD", "orig_amount": "110",
               "fx_rate": "1.10", "base": "", "category_id": category_id("software"), "date": "2026-03-02"}
    j = client.post("/api/preview", json=payload).get_json()
    assert j["ok"] and j["base_text"] == "100.00" and j["total"] == "€121.00"
    j = client.post("/api/preview", json={**payload, "base": "50", "base_manual": True}).get_json()
    assert j["base_text"] is None and j["total"] == "€60.50"


def test_edit_page_and_list_show_the_foreign_amount(client):
    post_expense(client, base="")
    eid = stored()["id"]
    page = text(client.get(f"/entries/{eid}/edit"))
    assert 'name="orig_amount" id="orig_amount" value="110.00"' in page and 'value="1.1"' in page
    assert "110.00 USD @ 1.1" in text(client.get("/entries?year=2026"))
    csv = client.get("/export/entries.csv?year=2026").get_data(as_text=True)
    assert "moneda;importe_divisa;tipo_cambio" in csv and "USD;110,00;1.1;2026-03-02;ecb" in csv


def test_manual_mode_hides_the_lookup_button(client):
    assert 'id="fx-get"' in text(client.get("/entries/new/expense"))
    set_settings(fx_mode="manual")
    assert 'id="fx-get"' not in text(client.get("/entries/new/expense"))


# ---------------------------------------------------------------- migration of the old free-text note

def test_old_free_text_amounts_are_migrated(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path))
    con = sqlite3.connect(tmp_path / "taxhelper.db")
    con.executescript("""
        CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, date TEXT NOT NULL,
            number TEXT NOT NULL DEFAULT '', counterparty TEXT NOT NULL DEFAULT '', country TEXT NOT NULL DEFAULT 'ES',
            vat_id TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', category_id INTEGER,
            regime TEXT NOT NULL DEFAULT 'domestic', base_cents INTEGER NOT NULL, vat_rate TEXT NOT NULL DEFAULT '0',
            vat_cents INTEGER NOT NULL DEFAULT 0, withholding_rate TEXT NOT NULL DEFAULT '0',
            withholding_cents INTEGER NOT NULL DEFAULT 0, currency TEXT NOT NULL DEFAULT 'EUR',
            orig_amount TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', document TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO entries (kind, date, base_cents, currency, orig_amount) VALUES ('expense','2026-01-05',1800,'USD','20.00 USD');
        INSERT INTO entries (kind, date, base_cents, currency, orig_amount) VALUES ('expense','2026-01-06',500,'EUR','');
    """)
    con.commit()
    con.close()
    con = db.connect()
    db.init_db(con)
    rows = con.execute("SELECT currency, orig_amount_cents, fx_rate FROM entries ORDER BY id").fetchall()
    columns = {r["name"] for r in con.execute("PRAGMA table_info(entries)")}
    con.close()
    assert [tuple(r) for r in rows] == [("USD", 2000, ""), ("EUR", None, "")]
    assert "orig_amount" not in columns and "fx_source" in columns
