"""Filing calendar: default deadlines and status for the filings tracker.

Defaults follow the general AEAT calendar: 1T-3T by the 20th of April/July/October,
4T by January 30; with direct debit (domiciliacion) the 15th and January 27 (2026
calendar - check the yearly AEAT calendar, the dates may change). A deadline on a
weekend or on one of the user's configured holidays moves to the next business day.
Every date can still be edited by hand in the tracker.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import FrozenSet, Iterable, List, Optional

from .calc import quarter_end
from .codes import FilingStatus
from .dates import parse_date

# Models the tracker can create automatically: model -> kind.
# 115 (retentions on rent) is opt-in: it is not in DEFAULT_MODELS, the user adds it in the settings.
QUARTERLY_MODELS = ("303", "130", "115")
ANNUAL_MODELS = ("390", "100")
DEFAULT_MODELS = ("303", "130", "390", "100")
KNOWN_MODELS = QUARTERLY_MODELS + ANNUAL_MODELS

DUE_SOON_DAYS = 14


def roll_to_business_day(d: date, holidays: Iterable[date] = ()) -> date:
    """Move Saturday/Sunday and configured holidays to the next business day."""
    hol = set(holidays)
    while d.weekday() >= 5 or d in hol:
        d += timedelta(days=1)
    return d


@dataclass(frozen=True)
class FilingTemplate:
    model: str
    period: str  # '1T'..'4T' or 'ANUAL'
    year: int  # fiscal year the filing refers to
    opens: date
    due: date


def quarter_filing(model: str, year: int, q: int, domiciliacion: bool = False,
                   holidays: Iterable[date] = ()) -> FilingTemplate:
    """Template for a quarterly model (303 / 130 / 115) of the given fiscal year.

    The 4T deadline is January 30 (27 with direct debit) for 303 and 130, and January 20 (15 with direct debit,
    check the yearly AEAT calendar) for the retention models such as 115.
    """
    opens = quarter_end(year, q) + timedelta(days=1)
    if q < 4:
        day = 15 if domiciliacion else 20
        due = date(year, quarter_end(year, q).month + 1, day)
    elif model == "115":
        due = date(year + 1, 1, 15 if domiciliacion else 20)
    else:
        due = date(year + 1, 1, 27 if domiciliacion else 30)
    return FilingTemplate(model, f"{q}T", year, opens, roll_to_business_day(due, holidays))


def annual_filing(model: str, year: int, holidays: Iterable[date] = ()) -> FilingTemplate:
    if model == "390":
        return FilingTemplate("390", "ANUAL", year, date(year + 1, 1, 1),
                              roll_to_business_day(date(year + 1, 1, 30), holidays))
    if model == "100":
        # Campaign normally runs early April - June 30 of the following year.
        # Exact dates are published each year: edit if they differ.
        return FilingTemplate("100", "ANUAL", year, date(year + 1, 4, 1),
                              roll_to_business_day(date(year + 1, 6, 30), holidays))
    raise ValueError(f"no annual template for model {model}")


def default_filings(year: int, models: Iterable[str] = DEFAULT_MODELS, domiciliacion: bool = False,
                    holidays: Iterable[date] = (), start: Optional[date] = None) -> List[FilingTemplate]:
    """Standard filings for a fiscal year, limited to the models the user tracks.

    With an activity start date, quarters that ended before it and years before its year are skipped.
    """
    wanted = set(models)
    hol: FrozenSet[date] = frozenset(holidays)
    out: List[FilingTemplate] = []
    for model in QUARTERLY_MODELS:
        if model in wanted:
            out += [quarter_filing(model, year, q, domiciliacion, hol) for q in range(1, 5)
                    if start is None or quarter_end(year, q) >= start]
    for model in ANNUAL_MODELS:
        if model in wanted and (start is None or year >= start.year):
            out.append(annual_filing(model, year, hol))
    return out


def parse_holidays(text: str) -> List[date]:
    """Parse 'YYYY-MM-DD' dates separated by commas, spaces or newlines. Raises ValueError on bad input."""
    out = []
    for token in (text or "").replace(",", " ").split():
        out.append(parse_date(token))
    return sorted(set(out))


def period_quarter(period: Optional[str]) -> Optional[int]:
    """1..4 for a quarterly period such as '2T'; None for 'ANUAL' and any custom text ('5T', '0T', …)."""
    return int(period[0]) if period and re.fullmatch(r"[1-4]T", period) else None


def display_status(status: str, opens: date, due: date, today: date) -> str:
    """Derive the UI status.

    Stored status is 'pending' | 'filed' | 'not_required'. Derived for pending:
    'waiting' (period not finished), 'open', 'due_soon', 'overdue'.
    """
    if status in (FilingStatus.FILED, FilingStatus.NOT_REQUIRED):
        return status
    if today > due:
        return "overdue"
    if today < opens:
        return "waiting"
    if (due - today).days <= DUE_SOON_DAYS:
        return "due_soon"
    return "open"
