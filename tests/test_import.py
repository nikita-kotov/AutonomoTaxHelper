"""CSV import: parsing, preview, duplicate detection and the confirmed write."""
import io
from decimal import Decimal

from taxhelper import db
from tests.support import set_settings, text

HEADER = "date;kind;number;counterparty;country;vat_id;description;category;regime;currency;orig_amount;base;vat_rate;vat_amount\n"
GOOD = HEADER + (
    "2026-01-23;expense;INV-1;Gestoria SL;ES;B12345678;Monthly fee;professional;domestic;EUR;;59.00;21;12.39\n"
    "2026-02-28;expense;A-77;Cloud Inc;US;;Subscription;software;non_eu_reverse;USD;20.00;;21;\n")


def preview(client, csv_text, **kw):
    return client.post("/import/preview", data={"csv": csv_text}, **kw)


def commit(client, csv_text, rows):
    return client.post("/import/commit", data={"csv": csv_text, "row": [str(r) for r in rows]}, follow_redirects=True)


def stored():
    con = db.connect()
    try:
        return [dict(r) for r in con.execute("SELECT * FROM entries ORDER BY date, id")]
    finally:
        con.close()


def test_template_is_downloadable_and_previews_cleanly(client):
    r = client.get("/import/template.csv")
    assert r.status_code == 200 and "text/csv" in r.content_type
    body = r.get_data(as_text=True).lstrip("﻿")
    assert body.startswith("date;kind;number")
    page = text(preview(client, body))
    assert "2 ready to import" in page and "0 with errors" in page


def test_preview_shows_amounts_and_converts_foreign_currency(client):
    page = text(preview(client, GOOD))
    assert "59.00" in page.replace("€", "") or "€59.00" in page
    assert "1.1" in page  # the USD row shows its rate
    assert stored() == []  # a preview writes nothing


def test_commit_stores_the_ticked_rows_with_the_forms_numbers(client):
    commit(client, GOOD, [1, 2])
    rows = stored()
    assert len(rows) == 2
    first, second = rows
    assert (first["base_cents"], first["vat_cents"], first["number"], first["country"]) == (5900, 1239, "INV-1", "ES")
    assert second["currency"] == "USD" and second["orig_amount_cents"] == 2000
    assert second["base_cents"] == 1818 and second["vat_cents"] == 382  # 20.00 / 1.10 = 18.18 EUR; 21 % self-assessed
    assert second["fx_source"] == "ecb" and second["regime"] == "non_eu_reverse"


def test_only_ticked_rows_are_stored(client):
    commit(client, GOOD, [2])
    assert [r["number"] for r in stored()] == ["A-77"]


def test_importing_twice_marks_duplicates_and_leaves_them_unticked(client):
    commit(client, GOOD, [1, 2])
    page = text(preview(client, GOOD))
    assert "0 ready to import" in page and "2 already in the ledger" in page
    assert "checked" not in page.split("<table>")[1].split("</table>")[0].replace("disabled", "")
    again = commit(client, GOOD, [])
    assert "Nothing to import" in text(again) and len(stored()) == 2


def test_a_row_repeated_inside_the_file_is_flagged(client):
    csv_text = GOOD + GOOD.split("\n")[1] + "\n"
    page = text(preview(client, csv_text))
    assert "The same row appears earlier in the file" in page


def test_errors_are_reported_per_row_and_block_only_those_rows(client):
    csv_text = HEADER + (
        "2026-01-23;expense;1;Ok SL;ES;;x;professional;domestic;EUR;;10.00;21;\n"
        "2026-13-45;expense;2;Bad Date;ES;;x;professional;domestic;EUR;;10.00;21;\n"
        "2026-01-24;expense;3;No Cat;ES;;x;does-not-exist;domestic;EUR;;10.00;21;\n"
        "2026-01-25;sale;4;Bad Kind;ES;;x;professional;domestic;EUR;;10.00;21;\n"
        "2026-01-26;expense;5;No Rate;ES;;x;software;domestic;XXX;5.00;;21;\n")
    page = text(preview(client, csv_text))
    assert "1 ready to import" in page and "4 with errors" in page
    assert "Enter a valid date" in page and "Unknown category" in page and "Unknown type" in page
    commit(client, csv_text, [1, 2, 3, 4, 5])  # ticking invalid rows does not store them
    assert [r["number"] for r in stored()] == ["1"]


def test_missing_columns_and_empty_files_are_explained(client):
    assert "Missing required columns: date" in text(preview(client, "number;base\n1;10\n"))
    assert "Missing required columns: base" in text(preview(client, "date;number\n2026-01-01;1\n"))
    assert "no data rows" in text(preview(client, HEADER))
    assert "no data rows" in text(preview(client, ""))


def test_other_date_formats_delimiters_and_legacy_encoding(client):
    csv_text = "fecha,tipo,numero,contraparte,categoria,regimen_iva,base,tipo_iva\n01/03/2026,gasto,7,Café Ñandú,software,domestic,\"10,00\",21\n"
    page = text(client.post("/import/preview", data={"file": (io.BytesIO(csv_text.encode("cp1252")), "x.csv")},
                            content_type="multipart/form-data"))
    assert "1 ready to import" in page and "Café Ñandú" in page
    commit(client, csv_text, [1])
    row = stored()[0]
    assert (row["date"], row["base_cents"], row["vat_cents"]) == ("2026-03-01", 1000, 210)


def test_blank_cells_take_the_defaults_from_settings(client):
    set_settings(default_vat_rate=Decimal("10"))
    csv_text = "date;category;base\n2026-04-01;software;100.00\n"
    commit(client, csv_text, [1])
    row = stored()[0]
    assert row["vat_rate"] == "10" and row["vat_cents"] == 1000 and row["regime"] == "domestic"


def test_income_rows_need_no_category(client):
    csv_text = "date;kind;counterparty;base;vat_rate;withholding_rate\n2026-05-01;income;Client SL;1000.00;21;15\n"
    commit(client, csv_text, [1])
    row = stored()[0]
    assert (row["kind"], row["category_id"], row["vat_cents"], row["withholding_cents"]) == ("income", None, 21000, 15000)


def test_manual_rate_and_amount_in_the_file_are_kept(client):
    csv_text = "date;category;regime;currency;orig_amount;fx_rate;vat_rate\n2026-04-01;software;non_eu_reverse;USD;22.00;1.2;21\n"
    commit(client, csv_text, [1])
    row = stored()[0]
    assert row["base_cents"] == 1833 and row["fx_source"] == "manual" and row["fx_rate"] == "1.2"


def test_an_exported_file_can_be_read_back(client):
    commit(client, GOOD, [1, 2])
    export = client.get("/export/entries.csv?year=2026").get_data(as_text=True).lstrip("﻿")
    page = text(preview(client, export))
    assert "0 with errors" in page and "2 already in the ledger" in page


def test_the_import_page_is_linked_and_renders_in_every_language(client):
    assert "/import" in text(client.get("/entries"))
    expected = {"ru": "Импорт операций из CSV", "es": "Importar operaciones desde CSV", "en": "Import entries from CSV"}
    for code, title in expected.items():
        client.post("/lang", data={"lang": code, "next": "/import"})
        assert title in text(client.get("/import"))
        assert "Статус" in text(preview(client, GOOD)) if code == "ru" else preview(client, GOOD).status_code == 200


def test_oversized_files_are_refused(client):
    big = HEADER + ("2026-01-01;expense;1;X;ES;;x;software;domestic;EUR;;1.00;21;\n" * 2001)
    assert "too large" in text(preview(client, big))
