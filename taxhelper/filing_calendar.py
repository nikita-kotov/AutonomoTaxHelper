"""Use-cases of the filings tracker: build the standard calendar from the user's profile, keep it
in step when settings change, and describe each filing's status."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

from .codes import FilingStatus
from . import deadlines
from .repositories import EntryRepository, FilingRepository
from .settings import Settings, SettingsRepository

Key = Tuple[str, str]  # (model, period)


class FilingCalendar:
    def __init__(self, filings: FilingRepository, settings: SettingsRepository, entries: EntryRepository):
        self.filings = filings
        self.settings = settings
        self.entries = entries

    # ------------------------------------------------------------- what the profile asks for

    @staticmethod
    def tracked_models(s: Settings) -> List[str]:
        models = list(s.tracked_models)
        if not s.vat_registered:  # no IVA -> no 303 / 390
            models = [m for m in models if m not in ("303", "390")]
        return models

    def _start(self, s: Settings) -> date:
        """The day from which filings are expected: the start of the activity; without it the first entry; without
        any entry today, so that a new installation is not told that periods before its first day are overdue."""
        return s.activity_start or self.entries.first_date() or date.today()

    def _wanted(self, year: int, s: Settings) -> Dict[Key, deadlines.FilingTemplate]:
        """The standard filings the tracker should hold for a year: profile applied, deliberate deletions skipped."""
        skipped = self.filings.skipped(year)
        templates = deadlines.default_filings(year, self.tracked_models(s), s.uses_direct_debit,
                                              s.extra_holidays, self._start(s))
        return {(t.model, t.period): t for t in templates if (t.model, t.period) not in skipped}

    # ------------------------------------------------------------- commands

    def ensure(self, years: Iterable[int]) -> None:
        """Create the missing standard filings of the given fiscal years."""
        s = self.settings.load()
        for year in set(years):
            for t in self._wanted(year, s).values():
                self.filings.insert_template(t)
        self.filings.commit()

    def sync(self) -> None:
        """Re-apply the profile (tracked models, direct debit, holidays, start date) to existing filings.

        Pending filings whose date was not edited by hand get the new deadline. Untouched pending
        filings that the profile no longer asks for are removed. Filed and hand-edited ones are kept.
        """
        s = self.settings.load()
        for year in self.filings.years():
            wanted = self._wanted(year, s)
            for t in wanted.values():
                self.filings.insert_template(t)
                self.filings.reschedule(t)
            for row in self.filings.default_rows(year):
                if (row["model"], row["period"]) not in wanted and self._is_pristine(row):
                    self.filings.delete(row["id"])
        self.filings.commit()

    @staticmethod
    def _is_pristine(row: sqlite3.Row) -> bool:
        """A pending filing nobody has touched: safe to drop when the profile no longer needs it."""
        return (row["status"] == FilingStatus.PENDING and not row["due_manual"] and not row["filed_date"]
                and not row["reference"] and not row["notes"] and not row["document"]
                and row["result_cents"] is None)

    def delete(self, row: sqlite3.Row) -> None:
        """Delete a filing and remember it, so the standard calendar does not create it again."""
        self.filings.skip(row["year"], row["model"], row["period"])
        self.filings.delete(row["id"])
        self.filings.commit()

    def restore_deleted(self, today: Optional[date] = None) -> None:
        """Bring back standard filings that were deleted by hand (the profile decides which exist)."""
        today = today or date.today()
        self.filings.clear_skips()
        self.ensure(set(self.filings.years()) | {today.year - 1, today.year})

    def add_custom(self, year: int, model: str, period: str, due: date) -> None:
        """Add a hand-made filing. Raises sqlite3.IntegrityError if it already exists."""
        self.filings.add_custom(year, model, period, due)
        self.filings.unskip(year, model, period)  # adding it by hand cancels an earlier deliberate deletion
        self.filings.commit()

    def mark(self, filing_id: int, action: str) -> bool:
        """Set a filing to 'filed', 'not_required' or back to 'pending'. False for an unknown action."""
        row = self.filings.get(filing_id)
        if row is None or action not in tuple(FilingStatus):
            return False
        filed_date = ""
        if action == FilingStatus.FILED:
            filed_date = row["filed_date"] or date.today().isoformat()
        elif action == FilingStatus.NOT_REQUIRED:
            filed_date = row["filed_date"]
        self.filings.set_status(filing_id, action, filed_date)
        self.filings.commit()
        return True

    def mark_filed_until(self, upto: date) -> int:
        count = self.filings.mark_filed_until(upto)
        self.filings.commit()
        return count

    # ------------------------------------------------------------- queries

    def rows(self, pending_only: bool = False, today: Optional[date] = None) -> List[dict]:
        """Filings as dicts with the derived UI fields: quarter, display status, days left."""
        today = today or date.today()
        out = []
        for r in (self.filings.pending() if pending_only else self.filings.all()):
            due = date.fromisoformat(r["due_date"])
            d = dict(r)
            d["quarter"] = deadlines.period_quarter(r["period"])
            d["display"] = deadlines.display_status(r["status"], date.fromisoformat(r["opens"]), due, today)
            d["days_left"] = (due - today).days
            out.append(d)
        return out
