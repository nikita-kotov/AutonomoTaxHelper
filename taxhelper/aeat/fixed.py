"""Helpers for AEAT fixed-width record layouts ("diseño de registro").

Rules from the layout notes: alphanumeric (An) fields hold only letters, digits and
blanks, left-aligned and blank-padded; numeric (Num) fields are digits only,
right-aligned and zero-padded; signed (N) fields put an 'N' in the first position
when negative. Money has 15 integer + 2 decimal digits and no decimal point.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..i18n import UserError

KEEP = {"Ñ", "Ç"}  # allowed by the AEAT character set and meaningful in names


@dataclass(frozen=True)
class Taxpayer:
    """Person filing the declaration."""
    nif: str
    surname: str
    first_name: str

    @property
    def full_name(self) -> str:
        """'SURNAME FIRSTNAME' as used by the 303 (single name field)."""
        return f"{self.surname} {self.first_name}".strip()

    def validate(self) -> None:
        if len(self.nif.strip()) != 9:
            raise UserError("err.nif_length")
        if not self.surname.strip() or not self.first_name.strip():
            raise UserError("err.names_required")


def an(value: str, length: int) -> str:
    """Alphanumeric field: upper-case, accents stripped, only [A-Z0-9 ÑÇ], padded/truncated."""
    out = []
    for ch in (value or "").upper():
        if ch in KEEP:
            out.append(ch)
            continue
        for part in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(part):
                continue
            if ("A" <= part <= "Z") or ("0" <= part <= "9") or part == " ":
                out.append(part)
    return "".join(out)[:length].ljust(length)


def blanks(length: int) -> str:
    return " " * length


def const(text: str, length: int) -> str:
    """A constant that must have exactly the given length."""
    if len(text) != length:
        raise ValueError(f"constant {text!r} is not {length} long")
    return text


def num(cents: int, length: int = 17) -> str:
    """Unsigned numeric field in cents. A negative value raises a user-facing error: credit
    notes must be routed to the layout's signed boxes before they get here."""
    if cents < 0:
        raise UserError("err.aeat_negative")
    digits = str(int(cents))
    if len(digits) > length:
        raise UserError("err.aeat_too_big")
    return digits.rjust(length, "0")


def signed(cents: int, length: int = 17) -> str:
    """Signed numeric field: negatives get 'N' in the first position."""
    if cents >= 0:
        return num(cents, length)
    digits = str(-int(cents))
    if len(digits) > length - 1:
        raise UserError("err.aeat_too_big")
    return "N" + digits.rjust(length - 1, "0")


def pct(percent_x100: int, length: int = 5) -> str:
    """Percentage with 3 integer + 2 decimals: 21% -> 2100 -> '02100'."""
    return str(percent_x100).rjust(length, "0")


class Record:
    """Builds one page record and checks its total length against the layout."""

    def __init__(self, expected_length: int, name: str):
        self.expected = expected_length
        self.name = name
        self.parts = []

    def add(self, text: str) -> "Record":
        self.parts.append(text)
        return self

    def text(self) -> str:
        s = "".join(self.parts)
        if len(s) != self.expected:
            raise ValueError(f"record {self.name}: length {len(s)} != expected {self.expected}")
        return s


def file_header(model: str, year: int, period: str, dev_nif: str = "", dev_version: str = "") -> str:
    """Opening record (328 chars) shared by models 130 and 303: the model tag plus the AUX block
    carrying the software developer's NIF and version."""
    r = Record(328, f"header {model}")
    r.add("<T").add(model).add("0").add(f"{year:04d}").add(period).add("0000>")
    r.add("<AUX>").add(blanks(70)).add(an(dev_version, 4)).add(blanks(4)).add(an(dev_nif, 9))
    r.add(blanks(213)).add("</AUX>")
    return r.text()


def file_footer(model: str, year: int, period: str) -> str:
    """Closing tag (18 chars) of a declaration file."""
    return f"</T{model}0{year:04d}{period}0000>"
