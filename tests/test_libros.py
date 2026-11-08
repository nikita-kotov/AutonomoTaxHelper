"""The AEAT books workbook (libros registro): format, line rules, the page and the validation client."""

import io
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from xml.etree import ElementTree as ET

import pytest

from taxhelper import db
from taxhelper.aeat import client as aeat_client, libros, libros_layout
from taxhelper.aeat.xlsx import Sheet, write_xlsx
from taxhelper.i18n import UserError
from tests.support import category_id, set_settings, text


NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
PROFILE = dict(holder_nif="X1234567L", holder_surname="Garcia Lopez", holder_firstname="Ana", iae_epigraph="2*763",
               home_affected_pct=Decimal("11"), vat_home_mode="none")


def read_workbook(content: bytes):
    """{sheet name: {row number: {column number: value}}} with dates as ISO text."""
    z = zipfile.ZipFile(io.BytesIO(content))
    shared = ["".join(t.text or "" for t in si.iter("{%s}t" % NS["m"]))
              for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", NS)]
    styles = ET.fromstring(z.read("xl/styles.xml"))
    date_style = {i for i, xf in enumerate(styles.find("m:cellXfs", NS)) if xf.get("numFmtId") == "164"}
    out = {}
    for index, name in enumerate(names, 1):
        rows = {}
        for row in ET.fromstring(z.read(f"xl/worksheets/sheet{index}.xml")).find("m:sheetData", NS):
            cells = {}
            for c in row.findall("m:c", NS):
                v = c.find("m:v", NS)
                if v is None:
                    continue
                letters = re.match(r"[A-Z]+", c.get("r")).group(0)
                col = 0
                for ch in letters:
                    col = col * 26 + ord(ch) - 64
                if c.get("t") == "s":
                    cells[col] = shared[int(v.text)]
                elif int(c.get("s") or 0) in date_style:
                    cells[col] = (date(1899, 12, 30) + timedelta(days=int(v.text))).isoformat()
                else:
                    cells[col] = Decimal(v.text) if "." in v.text else int(v.text)
            rows[int(row.get("r"))] = cells
        out[name] = rows
    return out


def post_entry(client, kind, **fields):
    data = dict(country="ES", currency="EUR", regime="domestic", vat_rate="21", description="", vat_id="", number="")
    data.update(fields)
    assert client.post(f"/entries/new/{kind}", data=data).status_code == 302


def build(upto=4):
    con = db.connect()
    try:
        from taxhelper.repositories import EntryRepository
        from taxhelper.settings import SettingsRepository
        return libros.build_books(SettingsRepository(con).load(), EntryRepository(con).year_rows(2026), 2026, upto)
    finally:
        con.close()


@pytest.fixture()
def ledger(client):
    set_settings(**PROFILE)
    post_entry(client, "income", date="2026-01-23", counterparty="Client One Ltd", country="SC", vat_id="1234567-8",
               number="FACT-2026-00001", regime="non_eu", base="5000.00", vat_rate="0", withholding_rate="0")
    post_entry(client, "expense", date="2026-01-23", counterparty="Xolo Business Spain, SL", vat_id="B67817262",
               number="INV2604173", category_id=str(category_id("professional")), base="59.00", vat_amount="12.39")
    post_entry(client, "expense", date="2026-02-28", counterparty="OpenAI OpCo, LLC", country="US", vat_id="9999999",
               number="23826346-0029", category_id=str(category_id("software")), regime="non_eu_reverse",
               currency="USD", orig_amount="20.00", fx_rate="1.1")
    post_entry(client, "expense", date="2026-03-25", counterparty="Telefonica", vat_id="A82018474", number="F1",
               category_id=str(category_id("internet")), base="16.45", vat_amount="3.45")
    post_entry(client, "expense", date="2026-02-27", counterparty="TGSS", vat_id="Q2827003A",
               category_id=str(category_id("reta")), regime="no_vat", base="88.56", vat_rate="0")
    post_entry(client, "expense", date="2026-04-23", counterparty="Aigues", vat_id="A66098435", number="W1",
               category_id=str(category_id("water")), base="61.34", vat_rate="10", vat_amount="3.26")
    post_entry(client, "expense", date="2026-04-01", counterparty="Landlord SL", vat_id="B58613068", number="2026/109",
               category_id=str(category_id("rent")), regime="no_vat", base="1712.88", vat_rate="0")
    return build()


def lines(book):
    """The data lines (row 4 on) of a sheet as lists ordered by column."""
    return [[row.get(i) for i in range(1, 43)] for r, row in sorted(book.items()) if r >= 4]


# ---------------------------------------------------------------- the xlsx writer

def test_the_writer_keeps_text_numbers_dates_and_escapes_markup():
    sheet = Sheet("DATA", [["<a&b> \"q\"\nline", Decimal("5"), date(2026, 3, 1), 2026, None, "ñ€"]], header_rows=1,
                  merges=("A1:B1",))
    book = read_workbook(write_xlsx([sheet]))["DATA"][1]
    assert book[1] == "<a&b> \"q\"\nline" and book[2] == Decimal("5.00") and book[3] == "2026-03-01"
    assert book[4] == 2026 and 5 not in book and book[6] == "ñ€"


def test_control_characters_are_dropped_from_cells():
    assert read_workbook(write_xlsx([Sheet("S", [["a\x00b\x0cc"]])]))["S"][1][1] == "abc"


def test_the_layout_has_the_official_column_counts():
    assert [len(r) for r in libros_layout.INCOME_HEADER] == [36] * 3
    assert [len(r) for r in libros_layout.EXPENSE_HEADER] == [42] * 3


# ---------------------------------------------------------------- the books

def test_workbook_has_the_two_unified_sheets_with_three_header_rows(ledger):
    wb = read_workbook(ledger.content)
    assert list(wb) == ["EXPEDIDAS_INGRESOS", "RECIBIDAS_GASTOS"]
    assert wb["RECIBIDAS_GASTOS"][1][6] == "Tipo de Factura" and wb["RECIBIDAS_GASTOS"][3][8] == "Decimal(12,2)"
    assert min(r for r in wb["EXPEDIDAS_INGRESOS"] if r >= 4) == 4
    assert ledger.filename == "2026X1234567LTGarcia Lopez, Ana.xlsx"


def test_income_from_a_client_outside_the_eu(ledger):
    [row] = lines(read_workbook(ledger.content)["EXPEDIDAS_INGRESOS"])
    assert row[:10] == [2026, "1T", "A", "05", "763", "F1", "I01", Decimal("5000.00"), "2026-01-23", "2026-01-23"]
    assert row[11] == "FACT-2026-00001"                          # number
    assert row[13:17] == ["04", "SC", "1234567-8", "Client One Ltd"]  # foreign id: type, country, number, name
    assert row[17:19] == ["01", "N2"]                            # general regime, not subject by localisation rules
    assert row[20:24] == [Decimal("5000.00"), Decimal("5000.00"), Decimal("0.00"), Decimal("0.00")]
    assert row[30] is None and row[31] is None                   # no withholding


def test_domestic_expense_with_deductible_iva(ledger):
    row = lines(read_workbook(ledger.content)["RECIBIDAS_GASTOS"])[0]
    assert row[:8] == [2026, "1T", "A", "05", "763", "F1", "G19", Decimal("59.00")]
    assert row[10] == "INV2604173" and row[12] == "2026-01-23" and row[13] == "1"   # invoice, received, receipt no.
    assert row[15:19] == [None, None, "B67817262", "Xolo Business Spain, SL"]       # a Spanish NIF has no type/country
    assert row[25:30] == [Decimal("71.39"), Decimal("59.00"), Decimal("21.00"), Decimal("12.39"), Decimal("12.39")]


def test_reverse_charge_from_outside_the_eu(ledger):
    rows = {r[10]: r for r in lines(read_workbook(ledger.content)["RECIBIDAS_GASTOS"])}
    row = rows["23826346-0029"]
    assert row[6:8] == ["G22", Decimal("18.18")] and row[21] == "S" and row[19] == "01"
    assert row[15:18] == ["04", "US", "9999999"]
    assert row[25:30] == [Decimal("18.18"), Decimal("18.18"), Decimal("21.00"), Decimal("3.82"), Decimal("3.82")]


def test_home_supplies_use_the_irpf_share_and_deduct_no_iva(ledger):
    row = {r[10]: r for r in lines(read_workbook(ledger.content)["RECIBIDAS_GASTOS"])}["F1"]
    assert row[6:8] == ["G17", Decimal("0.66")]                 # 19,90 x 11 % x 30 %
    assert row[28:30] == [Decimal("3.45"), Decimal("0.00")]     # IVA paid, none deductible


def test_expenses_without_iva_are_accounting_vouchers(ledger):
    rows = lines(read_workbook(ledger.content)["RECIBIDAS_GASTOS"])
    social, rent = (next(r for r in rows if r[6] == c) for c in ("G45", "G12"))
    assert social[5] == "F6" and social[7] == Decimal("88.56") and social[27] is None and social[29] is None
    assert rent[5] == "F6" and rent[7] == Decimal("188.42") and rent[10] == "2026/109"


def test_an_invoice_whose_iva_covers_part_of_it_becomes_two_lines(ledger):
    water = [r for r in lines(read_workbook(ledger.content)["RECIBIDAS_GASTOS"]) if r[10] == "W1"]
    assert [r[26] for r in water] == [Decimal("32.60"), Decimal("28.74")]       # bases add up to the invoice
    assert [r[27] for r in water] == [Decimal("10.00"), Decimal("0.00")]
    assert sum(r[7] for r in water) == Decimal("2.13")                           # 64,60 x 11 % x 30 %
    assert water[0][13] == water[1][13]                                           # one receipt number


def test_receipt_numbers_follow_the_order_of_the_entries(ledger):
    numbers = [r[13] for r in lines(read_workbook(ledger.content)["RECIBIDAS_GASTOS"])]
    assert numbers == ["1", "2", "3", "4", "5", "6", "6"]


def test_the_books_stop_at_the_chosen_quarter(ledger):
    q1 = build(upto=1)
    wb = read_workbook(q1.content)
    assert {r[0] for r in lines(wb["RECIBIDAS_GASTOS"])} == {2026} and q1.expense_lines == 4
    assert all(r[1] == "1T" for r in lines(wb["RECIBIDAS_GASTOS"]))
    assert build(upto=4).expense_lines == 7


def test_income_withholding_appears_once_per_invoice(client):
    set_settings(**PROFILE)
    post_entry(client, "income", date="2026-02-01", counterparty="Spanish SL", vat_id="B12345674", number="A-1",
               base="1000.00", vat_rate="21", withholding_rate="15")
    [row] = lines(read_workbook(build().content)["EXPEDIDAS_INGRESOS"])
    assert row[17:19] == ["01", "S1"] and row[20:24] == [Decimal("1210.00"), Decimal("1000.00"), Decimal("21.00"), Decimal("210.00")]
    assert row[30] == Decimal("15.00") and row[31] == Decimal("150.00")


def test_rent_retention_is_written_in_the_expense_book(client):
    set_settings(**PROFILE)
    post_entry(client, "expense", date="2026-02-01", counterparty="Landlord SL", vat_id="B12345674", number="R-1",
               category_id=str(category_id("office_rent")), base="1000.00", vat_rate="21", withholding_rate="19")
    post_entry(client, "expense", date="2026-02-02", counterparty="Shop", vat_id="A28812618", number="2",
               category_id=str(category_id("office")), base="50.00", vat_rate="0")
    rent, other = lines(read_workbook(build().content)["RECIBIDAS_GASTOS"])
    assert rent[36] == Decimal("19.00") and rent[37] == Decimal("190.00")
    assert other[36] is None and other[37] is None


def test_custom_categories_follow_their_irpf_mode(client):
    set_settings(**PROFILE)
    client.post("/categories", data={"name": "My supply", "irpf_mode": "home_supply", "vat_mode": "none"})
    client.post("/categories", data={"name": "Fines", "irpf_mode": "none", "vat_mode": "none"})
    cats = {c["name"]: c["id"] for c in db.connect().execute("SELECT id, name FROM categories")}
    post_entry(client, "expense", date="2026-02-01", counterparty="A", vat_id="A28812618", number="1",
               category_id=str(cats["My supply"]), base="100.00", vat_rate="0")
    post_entry(client, "expense", date="2026-02-02", counterparty="B", vat_id="A28812618", number="2",
               category_id=str(cats["Fines"]), base="50.00", vat_rate="0")
    rows = lines(read_workbook(build().content)["RECIBIDAS_GASTOS"])
    assert rows[0][6] == "G18" and rows[0][7] == Decimal("3.30")
    assert rows[1][6] is None and rows[1][7] is None     # not deductible: no concept, no amount


# ---------------------------------------------------------------- warnings and required settings

def test_missing_data_is_reported_not_invented(client):
    set_settings(**PROFILE)
    post_entry(client, "expense", date="2026-02-01", counterparty="No NIF SL", category_id=str(category_id("software")),
               base="10.00", vat_rate="21")
    messages = [str(w) for w in build().warnings]
    assert any("no tax ID" in m for m in messages) and any("no invoice number" in m for m in messages)


def test_a_placeholder_tax_id_goes_into_the_file_with_a_reminder(client):
    set_settings(**PROFILE)
    post_entry(client, "expense", date="2026-04-22", counterparty="Anthropic, PBC", country="US", vat_id="9999999",
               number="8ECHKHW8-0001", regime="non_eu_reverse", category_id=str(category_id("software")), base="180.00",
               vat_rate="21")
    built = build()
    messages = [str(w) for w in built.warnings]
    assert any("placeholder 9999999" in m and "Anthropic" in m for m in messages) and not any("no tax ID" in m for m in messages)
    assert "9999999" in str(read_workbook(built.content)["RECIBIDAS_GASTOS"])
    assert "placeholder 9999999" in text(client.get("/libros?year=2026"))  # the page shows it next to the other checks


def test_the_taxpayer_and_epigraph_are_required(client):
    with pytest.raises(UserError):
        build()
    set_settings(holder_nif="X1234567L", holder_surname="Garcia", holder_firstname="Ana")
    with pytest.raises(UserError) as exc:
        build()
    assert "IAE" in str(exc.value)
    set_settings(activity_code="B01")  # not in the IAE: no epigraph needed
    assert build().income_lines == 0


def test_the_epigraph_is_written_without_the_section_prefix(ledger):
    assert {r[4] for r in lines(read_workbook(ledger.content)["EXPEDIDAS_INGRESOS"])} == {"763"}


# ---------------------------------------------------------------- pages and the validation client

def test_the_page_download_and_menu(client, ledger):
    page = text(client.get("/libros?year=2026"))
    assert "2026X1234567LTGarcia Lopez, Ana.xlsx" in page and "/libros" in text(client.get("/entries"))
    r = client.get("/libros/file?year=2026&q=4")
    assert r.status_code == 200 and "spreadsheetml.sheet" in r.content_type
    assert "attachment" in r.headers["Content-Disposition"] and zipfile.is_zipfile(io.BytesIO(r.data))


def test_the_page_explains_missing_settings(client):
    page = text(client.get("/libros?year=2026"))
    assert "Fill in the NIF" in page


def test_the_check_shows_the_verdict(client, ledger, monkeypatch):
    seen = {}

    def fake(content, year, **kw):
        seen.update(size=len(content), year=year)
        return aeat_client.LibrosValidation(True, "Fichero validado correctamente", periods={"iva": ["1T"], "renta": ["1T", "2T"]})

    monkeypatch.setattr(aeat_client, "validate_libros", fake)
    page = text(client.post("/libros/check?year=2026", data={"q": "4"}))
    assert "accepts the file" in page and "IVA (303): 1T" in page and "IRPF (130, Renta): 1T, 2T" in page
    assert seen["year"] == 2026 and seen["size"] > 1000


def test_the_check_shows_errors(client, ledger, monkeypatch):
    monkeypatch.setattr(aeat_client, "validate_libros", lambda *a, **k: aeat_client.LibrosValidation(
        False, errors=["[Gasto] Fila(4) algo falla"], warnings=["aviso uno"]))
    page = text(client.post("/libros/check?year=2026", data={"q": "4"}))
    assert "found problems" in page and "Fila(4) algo falla" in page and "aviso uno" in page


def test_the_pages_render_in_every_language(client, ledger):
    for code in ("ru", "es", "en"):
        client.post("/lang", data={"lang": code, "next": "/libros"})
        assert client.get("/libros?year=2026").status_code == 200
        assert client.get("/settings").status_code == 200


def test_parse_libros_response():
    ok = aeat_client.parse_libros_response('{"resultado":{"valido":true,"mensaje":"Fichero validado correctamente"},'
                                           '"datosde":["iva","renta"],"periodosvalidos":{"iva":["1T","2T"],"renta":["1T"]}}')
    assert ok.ok and ok.message.startswith("Fichero") and ok.periods == {"iva": ["1T", "2T"], "renta": ["1T"]}
    bad = aeat_client.parse_libros_response('{"resultado":{"valido":false,"mensaje":"x"},"errores":["e1",{"a":1}],"avisos":["w"]}')
    assert not bad.ok and bad.errors[0] == "e1" and '"a"' in bad.errors[1] and bad.warnings == ["w"]
    assert not aeat_client.parse_libros_response("not json").ok
    assert not aeat_client.parse_libros_response('{"hola": 1}').ok


def test_the_accents_of_the_service_survive_either_encoding():
    assert aeat_client._decode("epígrafe".encode("utf-8")) == "epígrafe"
    assert aeat_client._decode("epígrafe".encode("cp1252")) == "epígrafe"


def test_network_failures_become_a_failed_verdict(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    res = aeat_client.validate_libros(b"x", 2026)
    assert not res.ok and res.errors


def test_activity_settings_are_saved_and_validated(client):
    form = {"activity_code": "A03", "iae_epigraph": "6533", "language": "", "payment_method": "nrc", "irpf_method": "simplificada",
            "irpf_rate_pct": "20", "default_vat_rate": "21", "default_withholding_rate": "0", "difficult_pct_pct": "5",
            "supplies_factor_pct": "30", "home_affected_pct": "0", "home_total_m2": "0", "vat_registered": "1"}
    client.post("/settings", data=form)
    from tests.support import load_settings
    assert (load_settings().activity_code, load_settings().iae_epigraph) == ("A03", "6533")
    client.post("/settings", data={**form, "activity_code": "ZZ"})
    assert load_settings().activity_code == "A05"


def custom_category(client, name, irpf, vat):
    client.post("/categories", data={"name": name, "irpf_mode": irpf, "vat_mode": vat})
    con = db.connect()
    try:
        return con.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]
    finally:
        con.close()


def test_expense_above_the_base_is_capped_and_the_total_matches_the_file(client):
    set_settings(**PROFILE)
    cid = custom_category(client, "Nonrecoverable", "full", "none")
    post_entry(client, "expense", date="2026-03-02", counterparty="Supplier SL", vat_id="B67817262", number="N-1",
               category_id=str(cid), base="100.00", vat_rate="21", vat_amount="21.00")
    built = build()
    [row] = lines(read_workbook(built.content)["RECIBIDAS_GASTOS"])
    assert row[7] == Decimal("100.00")                       # the AEAT never accepts more than the base
    assert built.expense_deductible_cents == 10_000          # the page total equals what the file carries
    warning = [str(w) for w in built.warnings if "limited" in str(w)]
    assert warning and "€21.00" in warning[0]                # and says what has to be added by hand


def test_credit_notes_are_rectifying_invoices_with_negative_amounts(client):
    set_settings(**PROFILE)
    post_entry(client, "income", date="2026-02-01", counterparty="Spanish SL", vat_id="B12345674", number="A-2",
               base="-200.00", vat_rate="21", vat_amount="-42.00", withholding_rate="0")
    post_entry(client, "expense", date="2026-02-02", counterparty="Aigues", vat_id="A66098435", number="W-9",
               category_id=str(category_id("water")), base="-61.34", vat_rate="10", vat_amount="-3.26")
    wb = read_workbook(build().content)
    [income] = lines(wb["EXPEDIDAS_INGRESOS"])
    assert income[5] == "R1" and income[21] == Decimal("-200.00") and income[23] == Decimal("-42.00")
    expense = lines(wb["RECIBIDAS_GASTOS"])
    assert [r[5] for r in expense] == ["R1", "R1"]                                   # both lines of the split credit note
    assert [r[26] for r in expense] == [Decimal("-32.60"), Decimal("-28.74")]        # split keeps the sign
    assert all(r[6] == "G15" for r in expense) and sum(r[7] for r in expense) < 0


def test_a_long_name_is_reported_once_even_when_the_invoice_has_two_lines(client):
    set_settings(**PROFILE)
    post_entry(client, "expense", date="2026-04-23", counterparty="N" * 45, vat_id="A66098435", number="W1",
               category_id=str(category_id("water")), base="61.34", vat_rate="10", vat_amount="3.26")
    built = build()
    assert built.expense_lines == 2 and sum("cut to 40" in str(w) for w in built.warnings) == 1


def test_an_unknown_book_column_fails_loudly():
    with pytest.raises(ValueError):
        libros._line(libros.INCOME_COLUMNS, nonsense=1)


def test_the_download_of_an_incomplete_profile_goes_back_to_the_page(client):
    r = client.get("/libros/file?year=2026")
    assert r.status_code == 302 and "/libros" in r.headers["Location"]


def test_the_aeat_verdict_needs_a_real_true():
    assert not aeat_client.parse_libros_response('{"resultado":{"valido":"false"}}').ok
    assert not aeat_client.parse_libros_response('{"resultado":{"valido":"N"}}').ok
    assert not aeat_client.parse_libros_response('{"resultado":{"valido":0}}').ok
    assert aeat_client.parse_libros_response('{"resultado":{"valido":true}}').ok
    assert aeat_client.parse_libros_response('{"resultado":{"valido":"true"}}').ok
