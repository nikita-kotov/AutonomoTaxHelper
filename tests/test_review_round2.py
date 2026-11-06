"""Regression tests for the second code review."""
import io
import threading
from datetime import date, datetime

import pytest
from support import category_id, set_settings, text

from taxhelper import backup as bk
from taxhelper import calc, db, i18n
from taxhelper.settings import SettingsRepository
from taxhelper.web import create_app
from taxhelper.web.helpers import safe_next


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("en")


# ---------------------------------------------------------------- amounts with a lone separator

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


def test_a_thousands_amount_typed_in_the_form_is_not_shrunk_a_thousandfold(client):
    set_settings(language="es")
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "C", "regime": "exempt",
                                            "base": "1.500", "currency": "EUR"})
    con = db.connect()
    assert con.execute("SELECT base_cents FROM entries").fetchone()[0] == 150_000
    con.close()


# ---------------------------------------------------------------- redirects

@pytest.mark.parametrize("target", ["/\\evil.example", "\\\\evil.example", "/\\/evil.example", "//evil.example",
                                    "https://evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=1", "", None,
                                    "/\tevil.example", "evil.example"])
def test_unsafe_redirect_targets_fall_back(target):
    assert safe_next(target, "/home") == "/home"


@pytest.mark.parametrize("target", ["/", "/entries?year=2026&q=2", "/filings#top", "/reports/303?year=2026"])
def test_local_redirect_targets_are_kept(target):
    assert safe_next(target, "/home") == target


def test_the_language_switch_refuses_a_backslash_redirect(client):
    r = client.post("/lang", data={"lang": "en", "next": "/\\evil.example"})
    assert r.headers["Location"].endswith("/") and "evil" not in r.headers["Location"]


# ---------------------------------------------------------------- cross-site posts and the secret key

def test_posts_marked_cross_site_by_the_browser_are_refused(client):
    assert client.post("/lang", data={"lang": "es"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/lang", data={"lang": "es"}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 302
    assert client.post("/lang", data={"lang": "es"}).status_code == 302  # clients that send no such header still work


def test_the_secret_key_is_random_per_data_folder_and_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "a"))
    first = create_app().secret_key
    assert first == create_app().secret_key and len(first) == 64 and first != "local-only-app"
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "b"))
    assert create_app().secret_key != first


# ---------------------------------------------------------------- attached documents

def attach(client, filename, content):
    client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21", "currency": "EUR",
        "category_id": str(category_id("software")), "document": (io.BytesIO(content), filename)},
        content_type="multipart/form-data")
    con = db.connect()
    try:
        row = con.execute("SELECT document FROM entries ORDER BY id DESC").fetchone()
        return row[0] if row else None  # None: the upload was refused and nothing was saved
    finally:
        con.close()


def test_only_pdfs_and_images_open_in_the_browser(client):
    pdf = client.get("/documents/" + attach(client, "a.pdf", b"%PDF-1.4"))
    assert pdf.status_code == 200 and "attachment" not in pdf.headers.get("Content-Disposition", "")
    png = client.get("/documents/" + attach(client, "a.PNG", b"\x89PNG"))
    assert "attachment" not in png.headers.get("Content-Disposition", "")
    note = client.get("/documents/" + attach(client, "notes.txt", b"<script>alert(1)</script>"))
    assert "attachment" in note.headers["Content-Disposition"]
    for response in (pdf, png, note):
        assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_markup_files_cannot_be_attached(client):
    for name in ("a.xml", "a.html", "a.svg", "a.xhtml"):
        assert attach(client, name, b"<x/>") is None  # rejected: nothing was stored


# ---------------------------------------------------------------- stale forms and stored documents

def expense_form(**over):
    data = {"date": "2026-02-10", "counterparty": "Vendor", "regime": "domestic", "base": "100", "vat_rate": "21",
            "currency": "EUR", "category_id": str(category_id("software"))}
    data.update(over)
    return data


def test_saving_with_a_deleted_category_keeps_the_old_invoice(client):
    doc = attach(client, "invoice.pdf", b"%PDF-1.4")
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()[0]
    con.close()
    path = db.documents_dir() / doc
    assert path.is_file()
    r = client.post(f"/entries/{eid}/edit", data={**expense_form(category_id="9999"),
                                                  "document": (io.BytesIO(b"%PDF-new"), "new.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and "Choose an expense category." in text(r)
    assert path.is_file()  # the original invoice survived
    con = db.connect()
    assert con.execute("SELECT document FROM entries WHERE id=?", (eid,)).fetchone()[0] == doc
    con.close()
    assert sorted(p.name for p in db.documents_dir().iterdir()) == [doc]  # and no orphan upload was left behind


def test_a_new_entry_with_an_unknown_category_is_an_error_not_a_crash(client):
    r = client.post("/entries/new/expense", data=expense_form(category_id="9999"))
    assert r.status_code == 200 and "Choose an expense category." in text(r)


def test_a_replaced_document_is_removed_only_after_the_save(client):
    old = attach(client, "old.pdf", b"%PDF-old")
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()[0]
    con.close()
    client.post(f"/entries/{eid}/edit", data={**expense_form(), "document": (io.BytesIO(b"%PDF-new"), "new.pdf")},
                content_type="multipart/form-data")
    con = db.connect()
    new = con.execute("SELECT document FROM entries WHERE id=?", (eid,)).fetchone()[0]
    con.close()
    assert new != old and (db.documents_dir() / new).is_file() and not (db.documents_dir() / old).exists()


# ---------------------------------------------------------------- the edit page keeps VAT values

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


# ---------------------------------------------------------------- the preview endpoint tolerates anything

@pytest.mark.parametrize("payload", [[], "x", 5, None, {}, {"kind": "expense", "regime": "nope", "base": "1"},
                                     {"kind": "x", "base": "1"}, {"kind": "expense", "regime": "domestic", "base": [1]},
                                     {"kind": "expense", "regime": "domestic", "base": "10", "vat_rate": {"a": 1}}])
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


# ---------------------------------------------------------------- backups under stress

@pytest.fixture()
def backup_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "data"))
    con = db.connect()
    db.init_db(con)
    yield con, tmp_path
    con.close()


def test_a_failing_target_is_retried_only_every_few_minutes(backup_env):
    con, tmp = backup_env
    blocker = tmp / "file.txt"
    blocker.write_text("x")
    SettingsRepository(con).update(backup_dir=str(blocker / "sub"))  # cannot be created
    t0 = datetime(2026, 11, 4, 12, 0)
    assert bk.run_backup(con, t0)[0] == "error"
    assert bk.run_backup(con, t0.replace(minute=1))[0] == "skipped"     # next save a minute later: no new attempt
    assert bk.run_backup(con, t0.replace(minute=14))[0] == "skipped"
    assert bk.run_backup(con, t0.replace(minute=16))[0] == "error"      # after 15 minutes it tries again
    assert bk.run_backup(con, t0.replace(minute=17), force=True)[0] == "error"  # the button always tries


def test_a_successful_backup_is_not_repeated_within_the_interval(backup_env):
    con, tmp = backup_env
    SettingsRepository(con).update(backup_dir=str(tmp / "cloud"))
    t0 = datetime(2026, 11, 4, 12, 0)
    assert bk.run_backup(con, t0)[0] == "ok"
    assert bk.run_backup(con, t0.replace(minute=30))[0] == "skipped"
    assert bk.run_backup(con, t0.replace(hour=19))[0] == "ok"


def test_a_second_backup_waits_while_one_is_running(backup_env):
    con, tmp = backup_env
    SettingsRepository(con).update(backup_dir=str(tmp / "cloud"))
    assert bk._RUNNING.acquire(blocking=False)
    try:
        assert bk.run_backup(con, datetime(2026, 11, 4, 12, 0))[0] == "skipped"
        assert not (tmp / "cloud").exists()
    finally:
        bk._RUNNING.release()


def test_a_partial_file_of_a_running_backup_is_left_alone(backup_env):
    con, tmp = backup_env
    cloud = tmp / "cloud"
    cloud.mkdir()
    fresh = cloud / "taxhelper-backup-20261104-1100.zip.partial"
    fresh.write_text("being written by another request")
    SettingsRepository(con).update(backup_dir=str(cloud))
    assert bk.run_backup(con, datetime(2026, 11, 4, 12, 0))[0] == "ok"
    assert fresh.exists()


def test_concurrent_backups_do_not_corrupt_each_other(backup_env):
    con, tmp = backup_env
    cloud = tmp / "cloud"
    SettingsRepository(con).update(backup_dir=str(cloud))
    results = []

    def run():
        c = db.connect()
        try:
            results.append(bk.run_backup(c, datetime(2026, 11, 4, 12, 0), force=True)[0])
        finally:
            c.close()

    threads = [threading.Thread(target=run) for _ in range(4)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert "ok" in results and set(results) <= {"ok", "skipped"}
    assert len(bk.list_backups(cloud)) == 1 and not list(cloud.glob("*.partial"))


# ---------------------------------------------------------------- restoring deleted standard filings

def filing_keys():
    con = db.connect()
    try:
        return {(r["year"], r["model"], r["period"]) for r in con.execute("SELECT * FROM filings")}
    finally:
        con.close()


def test_deleted_standard_filings_can_be_restored(client):
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE year=? AND model='303' AND period='4T'", (year,)).fetchone()[0]
    con.close()
    client.post(f"/filings/{fid}/delete")
    client.get("/filings")
    assert (year, "303", "4T") not in filing_keys()
    r = client.post("/filings/restore", follow_redirects=True)
    assert "Standard filings restored." in text(r) and (year, "303", "4T") in filing_keys()


def test_restoring_follows_the_tax_profile(client):
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    ids = {m: con.execute("SELECT id FROM filings WHERE year=? AND model=? AND period='4T'", (year, m)).fetchone()[0]
           for m in ("303", "130")}
    con.close()
    for filing_id in ids.values():
        client.post(f"/filings/{filing_id}/delete")
    set_settings(tracked_models=("130", "100"))  # the 303 is no longer part of the profile
    client.post("/filings/restore")
    keys = filing_keys()
    assert (year, "130", "4T") in keys and (year, "303", "4T") not in keys


# ---------------------------------------------------------------- settings cache

def test_the_settings_cache_is_dropped_on_every_write(backup_env):
    con, _ = backup_env
    repo = SettingsRepository(con)
    assert repo.load().language == ""
    repo.set_raw("language", "es")
    assert repo.load().language == "es"
    repo.update(language="ru")
    assert repo.load().language == "ru"
    repo.set_carry_in(2026, "10")
    assert repo.carry_in(2026) == 1_000
    assert repo.load() is repo.load()  # reads once between writes
