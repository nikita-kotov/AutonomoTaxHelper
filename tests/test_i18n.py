"""Locale files, translation lookup and language handling in the app."""
import json
import re
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from taxhelper import db, deadlines, i18n
from taxhelper.repositories import CategoryRepository

ROOT = Path(__file__).resolve().parent.parent
LOCALE_DIR = ROOT / "locales"
LOCALES = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in LOCALE_DIR.glob("*.json")}


def keys(d):
    return {k for k in d if k != "_meta"}


def placeholders(text):
    return set(re.findall(r"\{(\w+)\}", text))


@pytest.fixture(autouse=True)
def reset_language():
    yield
    i18n.set_language(i18n.FALLBACK)
    i18n.reload()


# ---------------------------------------------------------------- locale files

def test_required_locales_exist():
    assert {"en", "ru", "es"} <= set(LOCALES)


@pytest.mark.parametrize("code", sorted(set(LOCALES) - {"en"}))
def test_locale_has_exactly_the_english_keys(code):
    missing = keys(LOCALES["en"]) - keys(LOCALES[code])
    extra = keys(LOCALES[code]) - keys(LOCALES["en"])
    assert not missing, f"{code} lacks keys: {sorted(missing)[:10]}"
    assert not extra, f"{code} has unknown keys: {sorted(extra)[:10]}"


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_meta_is_complete(code):
    meta = LOCALES[code]["_meta"]
    assert {"name", "date_format", "decimal", "thousands", "money"} <= set(meta)
    assert "{amount}" in meta["money"]
    assert date(2026, 3, 9).strftime(meta["date_format"])


@pytest.mark.parametrize("code", sorted(set(LOCALES) - {"en"}))
def test_placeholders_match_english(code):
    for key in keys(LOCALES["en"]):
        assert placeholders(LOCALES[code][key]) == placeholders(LOCALES["en"][key]), f"{code}:{key}"


@pytest.mark.parametrize("code", sorted(LOCALES))
def test_no_empty_translations(code):
    assert all(isinstance(v, str) and v.strip() for k, v in LOCALES[code].items() if k != "_meta")


def test_every_key_used_in_code_and_templates_exists():
    pattern = re.compile(r"""(?:\bt|\bMsg|\bUserError)\(\s*['"]([a-z0-9_.]+)['"]""")
    sources = list((ROOT / "templates").glob("*.html")) + [ROOT / "app.py"]
    sources += [p for p in (ROOT / "taxhelper").rglob("*.py") if p.name != "i18n.py"]
    en = keys(LOCALES["en"])
    missing = {}
    for p in sources:
        for k in pattern.findall(p.read_text(encoding="utf-8")):
            if k.endswith((".", "_")):  # dynamic prefix such as t('status.' ~ x)
                continue
            if k not in en:
                missing.setdefault(k, []).append(p.name)
    assert not missing, missing


def test_dynamic_key_families_are_complete():
    en = keys(LOCALES["en"])
    expected = set()
    expected |= {f"status.{s}" for s in ("pending", "filed", "not_required", "overdue", "due_soon", "open", "waiting")}
    for kind, codes in (("income", ("domestic", "exempt", "eu_b2b", "non_eu")),
                        ("expense", ("domestic", "eu_reverse", "non_eu_reverse", "no_vat"))):
        for c in codes:
            expected |= {f"regime.{kind}.{c}", f"regime.{kind}.{c}.short"}
    expected |= {f"irpf.{c}" for c in ("full", "home_supply", "home_property", "none")}
    expected |= {f"vat.{c}" for c in ("full", "home_supply", "home_property", "none")}
    expected |= {f"model.{m}" for m in (*deadlines.KNOWN_MODELS, "349")}
    expected |= {f"tipo.{c}" for c in "IUNBC"}
    expected |= {f"cat.{code}" for code, _i, _v in db.DEFAULT_CATEGORIES}
    expected |= {f"form.heading_{a}_{b}" for a in ("new", "edit") for b in ("expense", "income")}
    assert expected <= en, sorted(expected - en)


# ---------------------------------------------------------------- lookup

def test_t_translates_formats_and_falls_back():
    i18n.set_language("ru")
    assert i18n.t("nav.dashboard") == "Панель"
    assert i18n.t("dash.title", year=2026) == "Панель · 2026"
    assert i18n.t("no.such.key") == "no.such.key"
    assert str(i18n.Msg("err.nif_len9")) == "NIF/NIE должен состоять из 9 символов."
    assert "9" in str(i18n.UserError("err.nif_len9"))
    i18n.set_language("zz")  # unknown language -> English
    assert i18n.current_language() == "en" and i18n.t("nav.dashboard") == "Dashboard"


def test_missing_translation_falls_back_to_english(tmp_path, monkeypatch):
    shutil.copy(LOCALE_DIR / "en.json", tmp_path / "en.json")
    (tmp_path / "xx.json").write_text(json.dumps({"_meta": {"name": "Xish"}, "nav.dashboard": "Xpanel"}), encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n.reload()
    assert "xx" in i18n.available() and i18n.available()["xx"] == "Xish"  # a new language needs only a file
    i18n.set_language("xx")
    assert i18n.t("nav.dashboard") == "Xpanel"
    assert i18n.t("nav.entries") == "Entries"  # not translated -> English


def test_unbalanced_placeholders_do_not_crash():
    i18n._cache["tmp"] = {"k": "Hello {name}"}
    i18n.set_language("en")
    i18n._cache["en"] = dict(i18n.load("en"), bad="Hi {missing}")
    assert i18n.t("bad", other=1) == "Hi {missing}"


def test_money_and_date_formats():
    assert i18n.format_money(123456789, "en") == "€1,234,567.89"
    assert i18n.format_money(123456789, "ru") == "1 234 567,89 €"
    assert i18n.format_money(123456789, "es") == "1.234.567,89 €"
    assert i18n.format_money(-5, "en") == "-€0.05"
    assert i18n.format_date("2026-03-09", "ru") == "09.03.2026"
    assert i18n.format_date(date(2026, 3, 9), "es") == "09/03/2026"
    assert i18n.format_date("", "en") == ""


# ---------------------------------------------------------------- category migration

def test_legacy_russian_categories_get_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path))
    con = sqlite3.connect(tmp_path / "taxhelper.db")
    con.executescript("""
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            irpf_mode TEXT NOT NULL DEFAULT 'full', vat_mode TEXT NOT NULL DEFAULT 'full',
            notes TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 100);
        INSERT INTO categories (name, notes) VALUES ('Электричество (дом)', 'старая заметка');
        INSERT INTO categories (name) VALUES ('Моя категория');
        CREATE TABLE filings (id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL, model TEXT NOT NULL,
            period TEXT NOT NULL, opens TEXT NOT NULL, due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', filed_date TEXT NOT NULL DEFAULT '',
            reference TEXT NOT NULL DEFAULT '', result_cents INTEGER, notes TEXT NOT NULL DEFAULT '',
            document TEXT NOT NULL DEFAULT '', UNIQUE (year, model, period));
    """)
    con.commit()
    con.close()
    con = db.connect()
    db.init_db(con)
    rows = {r["name"]: r for r in con.execute("SELECT * FROM categories")}
    con.close()
    assert "electricity" in rows and rows["electricity"]["code"] == "electricity"
    assert rows["electricity"]["notes"] == ""  # old Russian hint dropped; the translated hint is shown instead
    assert rows["Моя категория"]["code"] is None  # custom categories are untouched
    i18n.set_language("es")
    assert CategoryRepository.label("electricity", "electricity") == "Electricidad (vivienda)"
    assert CategoryRepository.label("electricity", "My own name") == "My own name"
    assert CategoryRepository.label(None, "Моя категория") == "Моя категория"


# ---------------------------------------------------------------- app level

def text(resp):
    return resp.get_data(as_text=True)


def test_language_follows_browser_until_chosen(client):
    assert '<html lang="es">' in text(client.get("/", headers={"Accept-Language": "es-ES,es;q=0.9,en;q=0.5"}))
    assert '<html lang="ru">' in text(client.get("/", headers={"Accept-Language": "ru"}))
    assert '<html lang="en">' in text(client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9"}))
    assert '<html lang="en">' in text(client.get("/"))


def test_language_switch_is_saved_and_applied(client):
    r = client.post("/lang", data={"lang": "es", "next": "/entries?year=2026"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/entries?year=2026")
    page = text(client.get("/entries?year=2026", headers={"Accept-Language": "ru"}))
    assert '<html lang="es">' in page and "Operaciones · 2026" in page  # saved choice beats the browser
    client.post("/lang", data={"lang": ""})  # back to automatic
    assert '<html lang="ru">' in text(client.get("/", headers={"Accept-Language": "ru"}))


def test_language_switch_rejects_bad_input(client):
    client.post("/lang", data={"lang": "es"})
    client.post("/lang", data={"lang": "klingon"})  # ignored
    assert '<html lang="es">' in text(client.get("/"))
    r = client.post("/lang", data={"lang": "en", "next": "//evil.example.com/x"})
    assert r.headers["Location"].endswith("/")  # open redirect refused


@pytest.mark.parametrize("code,expect", [("en", "Dashboard"), ("ru", "Панель"), ("es", "Panel")])
def test_every_page_renders_in_every_language(client, code, expect):
    client.post("/lang", data={"lang": code})
    for url in ["/", "/entries", "/reports/303", "/reports/130", "/reports/renta", "/filings",
                "/settings", "/categories", "/entries/new/expense", "/entries/new/income"]:
        page = text(client.get(url))
        assert f'<html lang="{code}">' in page and expect in page, url
        # no raw keys leaked into the page, e.g. «nav.dashboard» or «dash.title»
        assert not re.search(r">\s*(?:nav|dash|form|settings|filings|aeat|r303|r130|renta|cats|entries)\.[a-z_]+\s*<", page), url


def test_categories_follow_language_but_custom_names_stay(client):
    client.post("/lang", data={"lang": "es"})
    assert "Electricidad (vivienda)" in text(client.get("/categories"))
    client.post("/lang", data={"lang": "en"})
    assert "Electricity (home)" in text(client.get("/categories"))
    con = db.connect()
    cid = con.execute("SELECT id FROM categories WHERE code='gas'").fetchone()["id"]
    con.close()
    client.post(f"/categories/{cid}", data={"name": "Natural gas", "irpf_mode": "home_supply", "vat_mode": "home_supply"})
    client.post("/lang", data={"lang": "es"})
    page = text(client.get("/categories"))
    assert "Natural gas" in page and "Gas (vivienda)" not in page  # renamed -> stays as typed


def test_js_texts_and_previews_are_localised(client):
    client.post("/lang", data={"lang": "es"})
    page = text(client.get("/entries/new/expense"))
    assert "Un proveedor de la UE factur" in page  # form.help_eu_reverse delivered to the script (JSON-escaped)
    r = client.post("/api/preview", json={"kind": "expense", "regime": "domestic", "base": "1000", "vat_rate": "21",
                                          "category_id": 1, "date": "2026-02-01"})
    assert r.get_json()["total"] == "1.210,00 €"
    client.post("/lang", data={"lang": "en"})
    r = client.post("/api/preview", json={"kind": "expense", "regime": "domestic", "base": "1000", "vat_rate": "21",
                                          "category_id": 1, "date": "2026-02-01"})
    assert r.get_json()["total"] == "€1,210.00"
