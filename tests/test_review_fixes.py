"""Regression tests for the defects found by the code review (one test group per finding)."""
import http.client
import io
import os
import time
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from support import category_id, set_settings, text

from taxhelper import backup as bk
from taxhelper import calc, db, deadlines, i18n
from taxhelper.settings import SettingsRepository
from taxhelper.aeat import client as aeat_client
from taxhelper.aeat import model130, model303
from taxhelper.aeat.fixed import Taxpayer, signed
from taxhelper.calc import Entry, TaxSettings
from taxhelper.i18n import UserError

TP = Taxpayer(nif="12345678Z", surname="Ruiz", first_name="Ana")
BASE = {"language": "en", "vat_registered": "1", "home_total_m2": "0", "home_affected_pct": "0",
        "supplies_factor_pct": "30", "difficult_pct_pct": "5", "difficult_cap_eur": "2000",
        "difficult_enabled": "1", "irpf_method": "simplificada", "irpf_rate_pct": "20",
        "default_vat_rate": "21", "default_withholding_rate": "0", "payment_method": "nrc",
        "tracked_models": ["303", "130", "390", "100"]}


def entry(kind, base, vat=0, rate=21, d=date(2026, 2, 10), regime="domestic"):
    return Entry(kind=kind, date=d, regime=regime, base_cents=base, vat_cents=vat, vat_rate=Decimal(rate), category="x")


def set_identity():
    set_settings(holder_nif="X1234567L", holder_surname="GARCIA LOPEZ", holder_firstname="ANA")


# ---------------------------------------------------------------- 1. fractional VAT rates

@pytest.mark.parametrize("rate", ["10.5", "4.9", "5", "7.5", "21.5"])
def test_non_whole_rates_are_not_forced_into_a_standard_box(rate):
    r = calc.build_303([entry("income", 10_000, 1_050, rate)], TaxSettings(), 2026, 1)
    assert all(v == [0, 0] for v in r.by_rate.values()) and r.c27 == 0
    assert [w.key for w in r.warnings] == ["warn.vat_rate_unsupported"]


@pytest.mark.parametrize("rate,box", [("21", 21), ("21.00", 21), ("10", 10), ("4.0", 4)])
def test_whole_rates_still_land_in_their_box(rate, box):
    r = calc.build_303([entry("income", 10_000, 1_000, rate)], TaxSettings(), 2026, 1)
    assert r.by_rate[box] == [10_000, 1_000] and not r.warnings


# ---------------------------------------------------------------- 2. editing keeps an unusual stored rate

def test_editing_an_entry_with_an_unusual_rate_does_not_change_it(client):
    client.post("/entries/new/expense", data={
        "date": "2026-02-10", "counterparty": "X", "regime": "domestic", "base": "100", "vat_rate": "5",
        "vat_amount": "5.00", "category_id": str(category_id("software")), "currency": "EUR"})
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()["id"]
    con.close()
    page = text(client.get(f"/entries/{eid}/edit"))
    assert '<option value="5" selected>' in page
    client.post(f"/entries/{eid}/edit", data={
        "date": "2026-02-10", "counterparty": "X", "regime": "domestic", "base": "100", "vat_rate": "5",
        "vat_amount": "5.00", "category_id": str(category_id("software")), "currency": "EUR"})
    con = db.connect()
    assert con.execute("SELECT vat_rate FROM entries").fetchone()["vat_rate"] == "5"
    con.close()


# ---------------------------------------------------------------- 3. credit notes

def test_credit_notes_go_to_the_signed_modification_boxes():
    es = [entry("income", 100_000, 21_000), entry("income", -20_000, -4_200),
          entry("expense", 50_000, 10_500), entry("expense", -10_000, -2_100)]
    r = calc.build_303(es, TaxSettings(), 2026, 1)
    assert r.by_rate[21] == [100_000, 21_000]
    assert (r.c14, r.c15) == (-20_000, -4_200) and (r.c40, r.c41) == (-10_000, -2_100)
    assert (r.c28, r.c29) == (50_000, 10_500)
    assert r.c27 == 21_000 - 4_200 and r.c45 == 10_500 - 2_100 and r.c46 == 16_800 - 8_400
    page = model303.page1(r, TP, "I")
    assert page[432:449] == signed(-20_000) and page[449:466] == signed(-4_200)    # [14][15] at 433/450
    assert page[916:933] == signed(-10_000) and page[933:950] == signed(-2_100)    # [40][41] at 917/934
    assert page[695:712] == signed(r.c27) and page[1001:1018] == signed(r.c45)


def test_credit_note_with_partial_home_deduction_is_proportional():
    s = TaxSettings(home_affected_pct=Decimal(20))
    e = Entry(kind="expense", date=date(2026, 2, 1), regime="domestic", base_cents=-10_000, vat_cents=-2_100,
              vat_rate=Decimal(21), vat_mode="home_supply", category="x")
    r = calc.build_303([e], s, 2026, 1)
    assert r.c41 == -420 and r.c40 == -2_000


def test_unsignable_negative_amount_gives_a_user_message_not_a_crash():
    es = [entry("income", -5_000, 0, 0)]
    rep = calc.build_130_year(es, TaxSettings(), 2026, 1)[0]
    with pytest.raises(UserError) as exc:
        model130.build(rep, TP)
    assert exc.value.msg.key == "err.aeat_negative"
    rev = calc.build_303([entry("expense", -2_000, -420, regime="non_eu_reverse")], TaxSettings(), 2026, 1)
    with pytest.raises(UserError):
        model303.build(rev, TP)


def test_report_page_shows_the_translated_negative_amount_message(client):
    set_identity()
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "Refund", "regime": "exempt",
                                            "base": "-50", "currency": "EUR"})
    page = text(client.get("/reports/130?year=2026&q=1"))
    assert "A negative amount cannot be written into this field" in page


# ---------------------------------------------------------------- 4. odd custom periods

@pytest.mark.parametrize("period", ["5T", "0T", "9T", "T", "1t"])
def test_custom_filings_with_odd_periods_do_not_break_pages(client, period):
    r = client.post("/filings/new", data={"fyear": "2026", "model": "303", "period": period, "due_date": "2026-12-01"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert client.get("/filings").status_code == 200 and client.get("/").status_code == 200


def test_period_quarter_helper():
    f = deadlines.period_quarter
    assert [f(p) for p in ("1T", "4T", "5T", "0T", "ANUAL", "", None)] == [1, 4, None, None, None, None, None]


def test_filing_year_is_validated(client):
    r = client.post("/filings/new", data={"fyear": "99999", "model": "303", "period": "1T", "due_date": "2026-12-01"},
                    follow_redirects=True)
    assert "Fill in the year, model and due date." in text(r)


# ---------------------------------------------------------------- 5. non-Latin file names

def test_cyrillic_file_name_is_accepted(client):
    r = client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21", "currency": "EUR",
        "category_id": str(category_id("software")),
        "document": (io.BytesIO(b"%PDF-1.4"), "счёт.pdf")}, content_type="multipart/form-data", follow_redirects=True)
    assert "Entry added." in text(r)
    con = db.connect()
    name = con.execute("SELECT document FROM entries").fetchone()["document"]
    con.close()
    assert name.endswith("_document.pdf") and (db.data_dir() / "documents" / name).is_file()


@pytest.mark.parametrize("filename", ["evil.exe", "noextension", "счёт", ".pdf.exe"])
def test_bad_extensions_are_still_rejected(client, filename):
    r = client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21", "currency": "EUR",
        "category_id": str(category_id("software")), "document": (io.BytesIO(b"x"), filename)},
        content_type="multipart/form-data")
    assert "is not allowed" in text(r)


# ---------------------------------------------------------------- 6. NaN / Infinity

@pytest.mark.parametrize("bad", ["NaN", "nan", "Infinity", "-Infinity", "sNaN"])
def test_non_finite_numbers_are_rejected_not_500(client, bad):
    r = client.post("/settings", data={**BASE, "irpf_rate_pct": bad})
    assert r.status_code == 200 and "Invalid percentage" in text(r)
    r = client.post("/settings", data={**BASE, "home_total_m2": bad})
    assert r.status_code == 200 and "Areas must be numbers." in text(r)
    r = client.post("/settings", data={**BASE, "home_affected_pct": bad})
    assert r.status_code == 200 and "Invalid percentage" in text(r)
    r = client.post("/entries/new/expense", data={"date": "2026-02-10", "base": "10", "regime": "domestic",
                                                 "vat_rate": bad, "category_id": str(category_id("software"))})
    assert r.status_code == 200 and "Invalid percentage" in text(r)
    r = client.post("/api/preview", json={"kind": "expense", "regime": "domestic", "base": "10", "vat_rate": bad})
    assert r.status_code == 200 and r.get_json() == {"ok": False}


def test_non_finite_amounts_are_rejected():
    for bad in ("NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError):
            calc.parse_money(bad)


# ---------------------------------------------------------------- 7. deleted default filings stay deleted

def filing_keys():
    con = db.connect()
    try:
        return {(r["year"], r["model"], r["period"]) for r in con.execute("SELECT * FROM filings")}
    finally:
        con.close()


def test_deleted_default_filing_is_not_recreated(client):
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE year=? AND model='303' AND period='1T'", (year,)).fetchone()["id"]
    con.close()
    client.post(f"/filings/{fid}/delete")
    for url in ("/filings", "/", "/filings"):
        client.get(url)
    assert (year, "303", "1T") not in filing_keys()
    client.post("/settings", data=BASE)  # settings sync must not bring it back either
    assert (year, "303", "1T") not in filing_keys() and (year, "303", "2T") in filing_keys()
    # adding the same filing by hand works and forgets the deletion
    client.post("/filings/new", data={"fyear": str(year), "model": "303", "period": "1T", "due_date": f"{year}-04-20"})
    assert (year, "303", "1T") in filing_keys()
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM filing_skips").fetchone()[0] == 0
    con.close()


# ---------------------------------------------------------------- 8. backups

def test_foreign_files_in_the_backup_folder_do_not_stop_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "data"))
    con = db.connect()
    db.init_db(con)
    cloud = tmp_path / "cloud"
    cloud.mkdir()
    junk = ["taxhelper-backup-old.zip", "taxhelper-backup-copy (1).zip", "taxhelper-backup-20269999-9999.zip", "notes.txt"]
    for name in junk:
        (cloud / name).write_text("x")
    leftover = cloud / "taxhelper-backup-20260101-0000.zip.partial"
    leftover.write_text("half-written")
    two_hours_ago = time.time() - 7_200
    os.utime(leftover, (two_hours_ago, two_hours_ago))  # an interrupted run from long ago
    SettingsRepository(con).update(backup_dir=str(cloud))
    status, _ = bk.run_backup(con, datetime(2026, 11, 4, 12, 0))
    con.close()
    assert status == "ok"
    assert [p.name for p in bk.list_backups(cloud)] == ["taxhelper-backup-20261104-1200.zip"]
    assert not list(cloud.glob("*.partial"))
    assert all((cloud / name).exists() for name in junk)  # nothing foreign is touched


def test_failed_backup_leaves_no_partial_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "data"))
    con = db.connect()
    db.init_db(con)
    cloud = tmp_path / "cloud"
    SettingsRepository(con).update(backup_dir=str(cloud))

    def boom(_con, target):
        Path(target).write_text("half")
        raise OSError("disk full")

    monkeypatch.setattr(bk, "build_backup_zip", boom)
    status, message = bk.run_backup(con, datetime(2026, 11, 4, 12, 0))
    assert status == "error" and "disk full" in message
    assert not list(cloud.glob("*.partial")) and not bk.list_backups(cloud)
    assert "disk full" in SettingsRepository(con).load().last_backup_error
    con.close()


def test_backup_name_parsing():
    assert bk.parse_backup_name("taxhelper-backup-20261104-1200.zip") == datetime(2026, 11, 4, 12, 0)
    assert all(bk.parse_backup_name(n) is None for n in
               ("taxhelper-backup-old.zip", "taxhelper-backup-20261104-1200.zip.partial", "x.zip", "taxhelper-backup-20261301-0000.zip"))


# ---------------------------------------------------------------- 9. open redirect

@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example/x", "http://evil.example", "javascript:alert(1)", ""])
def test_filing_mark_ignores_foreign_redirect_targets(client, target):
    client.get("/filings")
    con = db.connect()
    fid = con.execute("SELECT id FROM filings LIMIT 1").fetchone()["id"]
    con.close()
    r = client.post(f"/filings/{fid}/mark", data={"action": "filed", "next": target})
    assert r.status_code == 302 and r.headers["Location"].endswith("/filings")


def test_filing_mark_keeps_local_redirect_targets(client):
    client.get("/filings")
    con = db.connect()
    fid = con.execute("SELECT id FROM filings LIMIT 1").fetchone()["id"]
    con.close()
    r = client.post(f"/filings/{fid}/mark", data={"action": "filed", "next": "/?year=2026"})
    assert r.headers["Location"].endswith("/?year=2026")


# ---------------------------------------------------------------- 10. AEAT client

def test_reply_without_errors_and_without_pdf_is_a_failure_with_a_reason():
    r = aeat_client.parse_response('{"respuesta": {"avisos": ["31190 - aviso"]}}')
    assert not r.ok and r.pdf is None and len(r.errors) == 1 and r.warnings == ["31190 - aviso"]


def test_connection_errors_of_http_client_become_failed_results(monkeypatch):
    def boom(*_a, **_k):
        raise http.client.RemoteDisconnected("closed without a reply")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    r = aeat_client.validate("130", 2026, "1T", "x")
    assert not r.ok and "closed without a reply" in r.errors[0]


# ---------------------------------------------------------------- cleanups

def test_language_list_is_cached_until_reload():
    assert i18n.available() is i18n.available()


def test_entry_search_is_case_insensitive_for_every_alphabet(client):
    client.post("/entries/new/income", data={"date": "2026-02-01", "counterparty": "Петров Иван", "regime": "exempt",
                                            "base": "100", "currency": "EUR"})
    client.post("/entries/new/income", data={"date": "2026-02-02", "counterparty": "Other", "regime": "exempt",
                                            "base": "5", "currency": "EUR"})
    page = text(client.get("/entries?year=2026&text=ПЕТРОВ"))
    assert "Петров Иван" in page and "Other" not in page


def test_entries_list_is_newest_first_and_caps_apply_across_filters(client):
    cid = category_id("health")
    con = db.connect()
    con.execute("UPDATE categories SET annual_cap_cents=50000 WHERE id=?", (cid,))
    con.commit()
    con.close()
    for month in (1, 2):
        client.post("/entries/new/expense", data={"date": f"2026-0{month}-10", "counterparty": f"Insurer{month}",
                                                 "regime": "no_vat", "base": "400", "category_id": str(cid)})
    page = text(client.get("/entries?year=2026&q=1"))
    assert "€400.00" in page  # January is fully deducted
    page = text(client.get("/entries?year=2026&kind=expense&text=insurer1"))
    assert "Insurer1" in page and "Insurer2" not in page
    page = text(client.get("/entries?year=2026&text=insurer2"))  # filtered view still shows the capped amount
    assert "€100.00" in page and "€400.00" in page  # base 400, deductible only the remaining 100
    both = text(client.get("/entries?year=2026"))
    assert both.index("Insurer2") < both.index("Insurer1")  # newest first
    assert "€100.00" in both  # February is capped to the remaining 100 EUR


def test_shared_header_and_footer_for_both_models():
    from taxhelper.aeat import fixed
    h = fixed.file_header("130", 2026, "1T", "12345678Z", "")
    assert len(h) == 328 and h.startswith("<T13002026" + "1T0000><AUX>") and h[100:109] == "12345678Z"
    assert fixed.file_footer("303", 2026, "4T") == "</T303020264T0000>"
