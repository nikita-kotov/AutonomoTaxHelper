"""Builds the modelo 130 flat file (layout "Ejercicios 2019 y siguientes", DR130e15v12).

File = header record DR13000 + page DR13001 (600 chars) + closing tag.
"""
from __future__ import annotations

from typing import Optional

from ..calc import Irpf130
from ..i18n import UserError
from .fixed import Record, Taxpayer, an, blanks, const, file_footer, file_header, num, signed

PAGE_LENGTH = 600


def declaration_type(rep: Irpf130, iban: str = "") -> str:
    """Tipo de declaracion: I ingreso, U domiciliacion, N negativa/cero, B a deducir."""
    if rep.c19 > 0:
        return "U" if iban.strip() else "I"
    if rep.c19 == 0:
        return "N"
    return "B" if rep.quarter < 4 else "N"


def _period(rep: Irpf130) -> str:
    return f"{rep.quarter}T"


def page1(rep: Irpf130, tp: Taxpayer, tipo: str, iban: str = "") -> str:
    """DR13001: the single declaration page (600 characters)."""
    if tipo not in ("I", "U", "G", "N", "B"):
        raise ValueError(f"unsupported tipo de declaracion {tipo!r}")
    if tipo == "U" and not iban.strip():
        raise UserError("err.iban_required")
    r = Record(PAGE_LENGTH, "DR13001")
    r.add("<T130").add("01").add("000>")           # 1-11 model + page
    r.add(" ")                                      # 12 complementary-page flag
    r.add(tipo)                                     # 13
    r.add(an(tp.nif, 9)).add(an(tp.surname, 60)).add(an(tp.first_name, 20))
    r.add(f"{rep.year:04d}").add(_period(rep))
    # Apartado I: casillas 01-07
    r.add(num(rep.c01)).add(num(rep.c02)).add(signed(rep.c03)).add(num(rep.c04))
    r.add(num(rep.c05)).add(num(rep.c06)).add(signed(rep.c07))
    # Apartado II (agricultural): casillas 08-11 not used
    r.add(num(0)).add(num(0)).add(num(0)).add(signed(0))
    # Apartado III: casillas 12-19
    r.add(num(rep.c12)).add(num(rep.c13)).add(signed(rep.c14)).add(num(rep.c15))
    r.add(num(rep.c16)).add(signed(rep.c17)).add(num(0)).add(signed(rep.c19))
    r.add(" ")                                      # complementaria
    r.add(blanks(13))                               # previous justificante
    r.add(an(iban.replace(" ", ""), 34) if tipo == "U" else blanks(34))
    r.add(blanks(96)).add(blanks(13))
    r.add(const("</T13001000>", 12))
    return r.text()


def build(rep: Irpf130, tp: Taxpayer, tipo: Optional[str] = None, iban: str = "",
          dev_nif: str = "", dev_version: str = "") -> str:
    """Full file content for the declaration (no line breaks)."""
    tp.validate()
    tipo = tipo or declaration_type(rep, iban)
    return (file_header("130", rep.year, _period(rep), dev_nif or tp.nif, dev_version)
            + page1(rep, tp, tipo, iban) + file_footer("130", rep.year, _period(rep)))
