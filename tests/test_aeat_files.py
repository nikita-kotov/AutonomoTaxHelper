"""Checks generated AEAT files against field positions published in the official layouts.

Positions are 1-based, as in DR130e15v12.xls and DR303e26v101.xlsx.
"""
import base64
import json
from datetime import date
from decimal import Decimal

import pytest

from taxhelper import calc
from taxhelper.aeat import client, fixed, model130, model303
from taxhelper.aeat.fixed import Taxpayer

TP = Taxpayer(nif="12345678Z", surname="García Ñúñez", first_name="José Ángel")


def field(text: str, pos: int, length: int) -> str:
    return text[pos - 1:pos - 1 + length]


def entries():
    E = calc.Entry
    return [
        E(kind="income", date=date(2026, 2, 5), regime="domestic", base_cents=1_000_000,
          vat_rate=Decimal(21), vat_cents=210_000, withholding_cents=150_000),
        E(kind="expense", date=date(2026, 2, 10), regime="domestic", base_cents=100_000,
          vat_rate=Decimal(21), vat_cents=21_000, category="Soft"),
        E(kind="expense", date=date(2026, 2, 11), regime="eu_reverse", base_cents=2_000,
          vat_rate=Decimal(21), vat_cents=420, category="Soft"),
    ]


# ---------------------------------------------------------------- fixed helpers

def test_an_strips_accents_and_pads():
    assert fixed.an("García Ñúñez", 14) == "GARCIA NUNEZ  ".replace("NUNEZ", "ÑUÑEZ")
    assert fixed.an("a-b/c", 5) == "ABC  "
    assert fixed.an("abcdefgh", 4) == "ABCD"


def test_numeric_formats():
    assert fixed.num(123456) == "00000000000123456"
    assert fixed.signed(-5) == "N0000000000000005"
    assert fixed.signed(5) == "00000000000000005"
    assert fixed.pct(2100) == "02100"
    with pytest.raises(ValueError):
        fixed.num(-1)
    with pytest.raises(ValueError):
        fixed.num(10 ** 18)


def test_taxpayer_validation():
    with pytest.raises(ValueError):
        Taxpayer("123", "A", "B").validate()
    with pytest.raises(ValueError):
        Taxpayer("12345678Z", "", "B").validate()


# ---------------------------------------------------------------- modelo 130

def test_130_file_layout_and_values():
    rep = calc.build_130_year(entries(), calc.TaxSettings(), 2026, 1)[0]
    text = model130.build(rep, TP, dev_nif="12345678Z")
    assert len(text) == 328 + 600 + 18
    assert text.startswith("<T13002026" + "1T" + "0000><AUX>")
    assert field(text, 101, 9) == "12345678Z"  # developer NIF inside AUX
    assert text.endswith("</T130020261T0000>")
    page = text[328:328 + 600]
    assert page.startswith("<T13001000>") and field(page, 589, 12) == "</T13001000>"
    assert field(page, 13, 1) == "I"  # positive result, no IBAN
    assert field(page, 14, 9) == "12345678Z"
    assert field(page, 23, 60).strip() == "GARCÍA ÑÚÑEZ".replace("Í", "I").replace("Ú", "U")
    assert field(page, 83, 20).strip() == "JOSE ANGEL"
    assert field(page, 103, 4) == "2026" and field(page, 107, 2) == "1T"
    # c01 income 10.000,00. Expenses: 1.000,00 domestic + 20,00 reverse-charge = 1.020,00;
    # difficult-justification 5% of 8.980,00 = 449,00 -> c02 = 1.469,00
    assert field(page, 109, 17) == "00000000001000000"
    assert field(page, 126, 17) == fixed.num(146_900)       # c02
    assert field(page, 143, 17) == fixed.num(853_100)       # c03
    assert field(page, 160, 17) == fixed.num(170_620)       # c04 = 20% of c03
    assert field(page, 194, 17) == fixed.num(150_000)       # c06 withholdings
    assert field(page, 211, 17) == fixed.num(20_620)        # c07 = 170.620 - 0 - 150.000
    assert field(page, 296, 17) == fixed.num(20_620)        # c12
    assert field(page, 415, 17) == fixed.num(20_620)        # c19


def test_130_types_and_domiciliation():
    rep = calc.build_130_year(entries(), calc.TaxSettings(), 2026, 1)[0]
    assert model130.declaration_type(rep) == "I"
    assert model130.declaration_type(rep, iban="ES9121000418450200051332") == "U"
    page = model130.page1(rep, TP, "U", "ES91 2100 0418 4502 0005 1332")
    assert field(page, 446, 34).strip() == "ES9121000418450200051332"
    with pytest.raises(ValueError):
        model130.page1(rep, TP, "U", "")
    zero = calc.build_130_year([], calc.TaxSettings(), 2026, 1)[0]
    assert model130.declaration_type(zero) == "N"


def test_130_negative_fields_use_n_prefix():
    # loss -> c03 negative
    E = calc.Entry
    es = [E(kind="income", date=date(2026, 2, 1), regime="domestic", base_cents=10_000),
          E(kind="expense", date=date(2026, 2, 2), regime="domestic", base_cents=50_000, category="x")]
    rep = calc.build_130_year(es, calc.TaxSettings(), 2026, 1)[0]
    page = model130.page1(rep, TP, model130.declaration_type(rep))
    assert field(page, 143, 17) == "N0000000000040000"
    assert field(page, 13, 1) == "N"


# ---------------------------------------------------------------- modelo 303

def test_303_file_layout_and_values():
    rep = calc.build_303_year(entries(), calc.TaxSettings(), 2026, 1)[0]
    text = model303.build(rep, TP)
    assert len(text) == 328 + 1581 + 1017 + 823 + 18
    assert text.endswith("</T303020261T0000>")
    p1 = text[328:328 + 1581]
    assert field(p1, 1, 11) == "<T30301000>" and field(p1, 1570, 12) == "</T30301000>"
    assert field(p1, 13, 1) == "I"
    assert field(p1, 14, 9) == "12345678Z"
    assert field(p1, 23, 80).strip() == "GARCIA ÑUÑEZ JOSE ANGEL"
    assert field(p1, 103, 4) == "2026" and field(p1, 107, 2) == "1T"
    assert field(p1, 109, 9) == "223222222"  # territorio comun, solo regimen general
    # 21%: base 10.000,00 cuota 2.100,00
    assert field(p1, 326, 17) == fixed.num(1_000_000)
    assert field(p1, 343, 5) == "02100"
    assert field(p1, 348, 17) == fixed.num(210_000)
    # reverse charge EU: [10][11] and [36][37]
    assert field(p1, 365, 17) == fixed.num(2_000) and field(p1, 382, 17) == fixed.num(420)
    assert field(p1, 849, 17) == fixed.num(2_000) and field(p1, 866, 17) == fixed.num(420)
    # [27] = 2.100,00 + 4,20 ; [28][29] domestic 1.000,00 / 210,00 ; [45] ; [46]
    assert field(p1, 696, 17) == fixed.num(210_420)
    assert field(p1, 713, 17) == fixed.num(100_000) and field(p1, 730, 17) == fixed.num(21_000)
    assert field(p1, 1002, 17) == fixed.num(21_420)
    assert field(p1, 1019, 17) == fixed.num(189_000)
    # constant rate columns
    assert field(p1, 226, 5) == "00400" and field(p1, 304, 5) == "01000" and field(p1, 484, 5) == "00175"


def test_303_page3_result_and_compensation():
    E = calc.Entry
    es = [E(kind="expense", date=date(2026, 2, 10), regime="domestic", base_cents=100_000,
            vat_rate=Decimal(21), vat_cents=21_000, category="Soft"),
          E(kind="income", date=date(2026, 5, 10), regime="domestic", base_cents=200_000,
            vat_rate=Decimal(21), vat_cents=42_000),
          E(kind="income", date=date(2026, 5, 11), regime="eu_b2b", base_cents=300_000),
          E(kind="income", date=date(2026, 5, 12), regime="non_eu", base_cents=400_000)]
    q1, q2 = calc.build_303_year(es, calc.TaxSettings(), 2026, 2)
    assert model303.declaration_type(q1) == "C"  # negative -> compensar
    assert q1.c71 == -21_000
    p3 = model303.page3(q2)
    assert len(p3) == 1017 and field(p3, 1, 11) == "<T30303000>"
    assert field(p3, 12, 17) == fixed.num(300_000)           # [59] EU services (see README: VERIFY)
    assert field(p3, 46, 17) == fixed.num(400_000)           # [120] non-EU
    assert field(p3, 199, 17) == fixed.num(42_000)           # [64]
    assert field(p3, 216, 5) == "10000"                      # [65] 100,00 %
    assert field(p3, 255, 17) == fixed.num(21_000)           # [110] pending from Q1
    assert field(p3, 272, 17) == fixed.num(21_000)           # [78] applied
    assert field(p3, 289, 17) == fixed.num(0)                # [87]
    assert field(p3, 340, 17) == fixed.num(21_000)           # [69]
    assert field(p3, 408, 17) == fixed.num(21_000)           # [71]
    assert field(p3, 1006, 12) == "</T30303000>"
    p3_neg = model303.page3(q1)
    assert field(p3_neg, 408, 17) == "N0000000000021000"


def test_303_domiciliation_page():
    rep = calc.build_303_year(entries(), calc.TaxSettings(), 2026, 1)[0]
    text = model303.build(rep, TP, iban="ES91 2100 0418 4502 0005 1332")
    did = text[328 + 1581 + 1017:-18]
    assert len(did) == 823 and field(did, 6, 5) == "DID00"
    assert model303.declaration_type(rep, "ES9121000418450200051332") == "U"
    assert field(did, 23, 34).strip() == "ES9121000418450200051332" and field(did, 194, 1) == "1"
    plain = model303.build(rep, TP)
    assert field(plain[328 + 1581 + 1017:-18], 194, 1) == "0"


def test_303_last_quarter_flags():
    reps = calc.build_303_year(entries(), calc.TaxSettings(), 2026, 4)
    q1_flags, q4_flags = model303._flags(reps[0]), model303._flags(reps[3])
    assert len(q1_flags) == len(q4_flags) == 22
    assert q1_flags[19] == "0" and q4_flags[19] == "2"  # position 128: exonerado 390 (Nota 4)


# ---------------------------------------------------------------- client

def test_build_payload_strips_line_breaks():
    p = client.build_payload("130", 2026, "1T", "ab\r\ncd\tef")
    assert p["F01"] == "abcdef" and p["MODELO"] == "130" and "SINVL" not in p
    assert "SINVL" in client.build_payload("130", 2026, "1T", "x", print_only=True)


def test_parse_success_with_warnings():
    pdf = base64.b64encode(b"%PDF-1.4 test").decode()
    body = json.dumps({"RESPUESTA": {"AVISOS": ["7113 - AVISO"], "PDF": [pdf]}})
    r = client.parse_response(body)
    assert r.ok and r.pdf == b"%PDF-1.4 test" and r.warnings == ["7113 - AVISO"]


def test_parse_errors_and_garbage():
    r = client.parse_response('{"respuesta":{"errores":["3005 - El campo N.I.F. no está identificado."]}}')
    assert not r.ok and "3005" in r.errors[0] and r.pdf is None
    assert not client.parse_response("<html>oops</html>").ok
    assert not client.parse_response('{"foo": 1}').ok


def test_validate_reports_network_failure():
    r = client.validate("130", 2026, "1T", "x", url="http://127.0.0.1:9/none", timeout=2)
    assert not r.ok and r.errors
