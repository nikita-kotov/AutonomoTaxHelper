"""Dates typed by the user: four-digit ISO years inside a sensible range.

`date.fromisoformat` also accepts spellings such as 20260123 and a year like 0026; the books and the
year views only make sense for real years, so every typed date goes through `parse_date`.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

MIN_YEAR, MAX_YEAR = 2000, 2100
MIN_ISO, MAX_ISO = f"{MIN_YEAR}-01-01", f"{MAX_YEAR}-12-31"  # the bounds of the browser's date fields
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_date(text: Optional[str]) -> date:
    """A date written YYYY-MM-DD. Raises ValueError for any other spelling or a year outside 2000-2100."""
    text = (text or "").strip()
    if not _ISO.fullmatch(text):
        raise ValueError(text)
    value = date.fromisoformat(text)
    if not MIN_YEAR <= value.year <= MAX_YEAR:
        raise ValueError(text)
    return value
