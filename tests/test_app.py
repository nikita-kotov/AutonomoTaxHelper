"""End-to-end tests through the Flask test client against a throwaway data folder."""
import io

import pytest


@pytest.fixture()
def client(fresh_app):
    """Test client with Russian saved as the interface language (most assertions check Russian texts)."""
    from support import set_settings
    set_settings(language="ru")
    return fresh_app.test_client()


def eur(cents):
    """Money as the Russian UI shows it (non-breaking-space thousands, comma decimals)."""
    from taxhelper import i18n
    return i18n.format_money(cents, "ru")


def category_id(client, code):
    from taxhelper import db
    con = db.connect()
    try:
        return con.execute("SELECT id FROM categories WHERE code = ?", (code,)).fetchone()["id"]
    finally:
        con.close()


def add_expense(client, **over):
    data = {"date": "2026-02-10", "counterparty": "Test", "description": "x", "country": "ES",
            "regime": "domestic", "base": "100,00", "vat_rate": "21", "vat_amount": "",
            "currency": "EUR", "category_id": str(category_id(client, "software"))}
    data.update(over)
    return client.post("/entries/new/expense", data=data, follow_redirects=True)


def test_pages_render_empty(client):
    for url in ["/", "/entries", "/reports/303", "/reports/130", "/reports/renta", "/filings",
                "/settings", "/categories", "/entries/new/expense", "/entries/new/income"]:
        r = client.get(url)
        assert r.status_code == 200, url


def test_add_expense_and_report(client):
    r = add_expense(client)
    assert r.status_code == 200 and "Операция добавлена" in r.get_data(as_text=True)
    page = client.get("/reports/303?year=2026&q=1").get_data(as_text=True)
    assert eur(2100) in page  # IVA soportado deducible
    r130 = client.get("/reports/130?year=2026&q=1").get_data(as_text=True)
    assert "Gastos" in r130


def test_home_office_flow(client):
    s = client.post("/settings?year=2026", data={
        "language": "ru", "vat_registered": "1", "home_total_m2": "100", "home_affected_pct": "20", "supplies_factor_pct": "30",
        "vat_home_mode": "share", "difficult_enabled": "1", "difficult_pct_pct": "5",
        "difficult_cap_eur": "2000", "prev_year_net_yield_eur": "", "vat_carry_in": ""},
        follow_redirects=True)
    assert "Настройки сохранены" in s.get_data(as_text=True)
    add_expense(client, category_id=str(category_id(client, "electricity")), base="100,00")
    rows = client.get("/entries?year=2026").get_data(as_text=True)
    assert "7,01 €" in rows and "4,20 €" in rows  # IRPF share*30% and IVA share


def test_full_year_scenario_renta_and_dashboard(client):
    client.post("/settings?year=2026", data={
        "language": "ru", "vat_registered": "1", "home_total_m2": "100", "home_affected_pct": "20", "supplies_factor_pct": "30",
        "vat_home_mode": "share", "difficult_enabled": "1", "difficult_pct_pct": "5",
        "difficult_cap_eur": "2000", "prev_year_net_yield_eur": "", "vat_carry_in": ""})
    # income 10.000 EUR + 21% IVA, 15% withholding
    inc = client.post("/entries/new/income", data={
        "date": "2026-02-05", "counterparty": "Cliente SL", "country": "ES", "regime": "domestic",
        "base": "10000", "vat_rate": "21", "vat_amount": "", "withholding_rate": "15", "currency": "EUR"},
        follow_redirects=True)
    assert "Операция добавлена" in inc.get_data(as_text=True)
    # AI subscription from an EU supplier without IVA (reverse charge): 20 EUR
    add_expense(client, regime="eu_reverse", base="20,00", counterparty="Anthropic Ireland")
    add_expense(client, category_id=str(category_id(client, "reta")), regime="no_vat", base="300,00")

    r303 = client.get("/reports/303?year=2026&q=1").get_data(as_text=True)
    assert eur(210_000) in r303 and "349" in r303  # devengado + warning about modelo 349
    r130 = client.get("/reports/130?year=2026&q=1").get_data(as_text=True)
    assert eur(1_000_000) in r130 and eur(150_000) in r130  # c01 and retenciones
    renta = client.get("/reports/renta?year=2026").get_data(as_text=True)
    # expenses 20 + 300 = 320; net before = 9.680; difficult 5% = 484; net yield = 9.196
    assert eur(968_000) in renta and eur(48_400) in renta and eur(919_600) in renta
    dash = client.get("/?year=2026").get_data(as_text=True)
    assert "Ближайшие сроки" in dash


SETTINGS_BASE = {"language": "ru", "vat_registered": "1", "home_total_m2": "0", "home_affected_pct": "0", "supplies_factor_pct": "30",
                 "difficult_pct_pct": "5", "difficult_cap_eur": "2000"}


def test_aeat_check_flow(client, monkeypatch):
    from taxhelper.aeat import client as aeat_client
    # without identity data the card explains what is missing
    page = client.get("/reports/130?year=2026&q=1").get_data(as_text=True)
    assert "проверьте «Настройки»" in page
    r = client.post("/settings", data={**SETTINGS_BASE, "holder_nif": "X1234567L", "holder_surname": "Garcia Lopez",
                                      "holder_firstname": "Ana", "payment_method": "nrc"},
                    follow_redirects=True)
    assert "Настройки сохранены" in r.get_data(as_text=True)
    client.post("/entries/new/income", data={
        "date": "2026-02-05", "counterparty": "Cliente", "country": "ES", "regime": "domestic",
        "base": "1000", "vat_rate": "21", "withholding_rate": "0", "currency": "EUR"})

    calls = []

    def fake_validate(model, year, period, content, **kw):
        calls.append((model, year, period, len(content)))
        return aeat_client.ValidationResult(True, warnings=["31190 - aviso"], pdf=b"%PDF-1.4 fake")

    monkeypatch.setattr(aeat_client, "validate", fake_validate)
    r = client.post("/aeat/check/130", data={"year": "2026", "q": "1"}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "AEAT приняла файл" in text and "31190 - aviso" in text and "Открыть PDF" in text
    assert calls == [("130", 2026, "1T", 946)]

    # changing the data makes the stored check stale
    add_expense(client, base="10,00")
    stale = client.get("/reports/130?year=2026&q=1").get_data(as_text=True)
    assert "данные с тех пор изменились" in stale

    f = client.get("/aeat/file/130?year=2026&q=1")
    assert f.status_code == 200 and len(f.data) == 946
    assert "X1234567L-2026-1T.130" in f.headers["Content-Disposition"]
    f303 = client.get("/aeat/file/303?year=2026&q=1")
    assert len(f303.data) == 328 + 1581 + 1017 + 823 + 18


def test_aeat_check_shows_errors(client, monkeypatch):
    from taxhelper.aeat import client as aeat_client
    client.post("/settings", data={**SETTINGS_BASE, "holder_nif": "X1234567L", "holder_surname": "Garcia Lopez",
                                  "holder_firstname": "Ana", "payment_method": "nrc"})
    monkeypatch.setattr(aeat_client, "validate", lambda *a, **k: aeat_client.ValidationResult(
        False, errors=["3005 - El campo N.I.F. no está identificado."]))
    r = client.post("/aeat/check/303", data={"year": "2026", "q": "1"}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "AEAT вернула ошибки" in text and "3005" in text


def test_settings_nif_and_iban_validation(client):
    r = client.post("/settings", data={**SETTINGS_BASE, "holder_nif": "123", "payment_method": "domiciliacion"})
    text = r.get_data(as_text=True)
    assert "9 символов" in text and "укажите IBAN" in text


def test_settings_validation(client):
    r = client.post("/settings", data={"language": "ru", "home_total_m2": "50", "home_affected_pct": "180",
                                       "supplies_factor_pct": "30", "difficult_pct_pct": "5"})
    assert "Процент должен быть от 0 до 100" in r.get_data(as_text=True)  # an affectation above 100 % is impossible


def test_preview_api(client):
    r = client.post("/api/preview", json={
        "kind": "expense", "regime": "eu_reverse", "base": "20", "vat_rate": "21",
        "category_id": category_id(client, "software"), "date": "2026-03-01"})
    j = r.get_json()
    assert j["ok"] and j["vat_cents"] == 420 and j["reverse"] and j["quarter"] == 1


def test_invalid_expense_rejected(client):
    r = client.post("/entries/new/expense", data={"date": "bad", "base": "x", "regime": "domestic"})
    text = r.get_data(as_text=True)
    assert "корректную дату" in text and "сумму без IVA" in text and "категорию" in text


def test_filings_tracker(client):
    page = client.get("/filings").get_data(as_text=True)
    assert "Renta (IRPF anual)" in page and "IVA (autoliquidación trimestral)" in page
    from taxhelper import db
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE model='303' AND period='1T' AND year=2026").fetchone()["id"]
    con.close()
    client.post(f"/filings/{fid}/mark", data={"action": "filed"})
    con = db.connect()
    row = con.execute("SELECT status, filed_date FROM filings WHERE id=?", (fid,)).fetchone()
    con.close()
    assert row["status"] == "filed" and row["filed_date"]
    r = client.post(f"/filings/{fid}/edit", data={
        "status": "filed", "due_date": "2026-04-20", "filed_date": "2026-04-15",
        "reference": "ABC123", "result": "1.234,56", "notes": ""}, follow_redirects=True)
    assert r.status_code == 200
    assert "ABC123" in client.get("/filings").get_data(as_text=True)


def test_custom_filing_and_duplicate(client):
    d = {"fyear": "2026", "model": "349", "period": "1T", "due_date": "2026-04-20"}
    assert "Отчётность добавлена" in client.post("/filings/new", data=d, follow_redirects=True).get_data(as_text=True)
    assert "уже есть" in client.post("/filings/new", data=d, follow_redirects=True).get_data(as_text=True)


def test_bulk_mark_filed(client):
    from taxhelper import db
    client.get("/filings")
    r = client.post("/filings/bulk-filed", data={"upto": "2026-07-31"}, follow_redirects=True)
    assert "Отмечено сданными" in r.get_data(as_text=True)
    con = db.connect()
    rows = con.execute("SELECT due_date, status FROM filings").fetchall()
    con.close()
    assert all(x["status"] == "filed" for x in rows if x["due_date"] <= "2026-07-31")
    assert all(x["status"] == "pending" for x in rows if x["due_date"] > "2026-07-31")


def test_overdue_shown_on_dashboard(client):
    page = client.get("/?year=2020").get_data(as_text=True)  # the dashboard creates that year's calendar itself
    assert "Просрочена отчётность" in page


def test_upload_rejects_executable_and_accepts_pdf(client):
    bad = client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21",
        "category_id": str(category_id(client, "software")),
        "document": (io.BytesIO(b"MZ"), "evil.exe")}, content_type="multipart/form-data")
    assert "не разрешён" in bad.get_data(as_text=True)
    ok = client.post("/entries/new/expense", data={
        "date": "2026-02-10", "base": "10", "regime": "domestic", "vat_rate": "21",
        "category_id": str(category_id(client, "software")),
        "document": (io.BytesIO(b"%PDF-1.4"), "invoice.pdf")},
        content_type="multipart/form-data", follow_redirects=True)
    assert "📎" in ok.get_data(as_text=True)


def test_clone_edit_delete(client):
    add_expense(client)
    from taxhelper import db
    con = db.connect()
    eid = con.execute("SELECT id FROM entries").fetchone()["id"]
    con.close()
    r = client.post(f"/entries/{eid}/clone", follow_redirects=True)
    assert "Копия создана" in r.get_data(as_text=True)
    client.post(f"/entries/{eid}/delete")
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
    con.close()


def test_host_and_origin_guard(client):
    assert client.get("/", headers={"Host": "evil.example.com"}).status_code == 400
    r = client.post("/settings", data={}, headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 403


def test_backup_and_csv(client):
    add_expense(client)
    z = client.get("/backup")
    assert z.status_code == 200 and z.data[:2] == b"PK"
    c = client.get("/export/entries.csv?year=2026")
    assert c.status_code == 200 and b"fecha" in c.data


def test_every_page_carries_the_author_banner_with_a_safe_external_link(client):
    from taxhelper.web.infra import AUTHOR, DONATE_URL
    for path in ("/", "/entries", "/settings"):
        page = client.get(path).get_data(as_text=True)
        assert AUTHOR in page and f'href="{DONATE_URL}"' in page
        assert 'target="_blank" rel="noopener noreferrer"' in page.split(DONATE_URL)[1][:80]
    assert "открытым исходным кодом" in client.get("/").get_data(as_text=True)


def test_language_is_switched_by_buttons_not_by_a_dropdown(client):
    page = client.get("/").get_data(as_text=True)
    head = page.split("</header>")[0]
    assert 'name="lang" value="es"' in head and 'name="lang" value="ru"' in head and 'aria-current=true' in head
    assert 'select name="lang"' not in head
    client.post("/lang", data={"lang": "ru", "next": "/"})
    assert "Панель" in client.get("/").get_data(as_text=True)
