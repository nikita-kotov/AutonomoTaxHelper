"""Data access: one repository per aggregate. All SQL for entries, categories, filings,
AEAT checks and cached exchange rates lives here; views and services never write SQL.

Repositories use the connection they are given. Writes of the category, contact, entry, submission, check and
rate-cache repositories commit by themselves (`ContactRepository.remember(commit=False)` batches several calls);
the filing repository leaves the commit to `FilingRepository.commit`, so one use-case is one transaction.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from .codes import DeductionMode, FilingStatus, Kind, Regime
from . import calc, deadlines, i18n
from .fx import Rate


def _new_id(cursor: sqlite3.Cursor) -> int:
    """The id of the row that an INSERT just created."""
    if cursor.lastrowid is None:
        raise RuntimeError("the INSERT created no row")
    return cursor.lastrowid

# ----------------------------------------------------------------- categories


class CategoryRepository:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    # display helpers: seeded categories (name == code) follow the interface language
    @staticmethod
    def label(code: Optional[str], name: str) -> str:
        """Display name: seeded categories are translated, renamed and custom ones are shown as typed."""
        if code and name == code and i18n.exists(f"cat.{code}"):
            return i18n.t(f"cat.{code}")
        return name

    @staticmethod
    def note(code: Optional[str], notes: str) -> str:
        """The user's note if any, otherwise the translated hint of a seeded category."""
        if notes:
            return notes
        if code and i18n.exists(f"cat.{code}.note"):
            return i18n.t(f"cat.{code}.note")
        return ""

    def all(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM categories ORDER BY sort, name").fetchall()

    def get(self, category_id: int) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()

    def usage(self) -> Dict[Optional[int], int]:
        """{category id: number of entries using it}."""
        return {r["category_id"]: r["n"] for r in
                self.con.execute("SELECT category_id, COUNT(*) AS n FROM entries GROUP BY category_id")}

    def add(self, name: str, irpf_mode: str, vat_mode: str, notes: str, cap_cents: Optional[int]) -> None:
        """Raises sqlite3.IntegrityError if the name is taken."""
        self.con.execute("INSERT INTO categories (name, irpf_mode, vat_mode, notes, sort, annual_cap_cents) "
                         "VALUES (?,?,?,?,500,?)", (name, irpf_mode, vat_mode, notes, cap_cents))
        self.con.commit()

    def update(self, category_id: int, name: str, irpf_mode: str, vat_mode: str, notes: str,
               cap_cents: Optional[int]) -> None:
        """Raises sqlite3.IntegrityError if the new name is taken."""
        self.con.execute("UPDATE categories SET name=?, irpf_mode=?, vat_mode=?, notes=?, annual_cap_cents=? WHERE id=?",
                         (name, irpf_mode, vat_mode, notes, cap_cents, category_id))
        self.con.commit()

    def delete(self, category_id: int) -> bool:
        """Delete an unused category; returns False when entries still use it."""
        if self.usage().get(category_id):
            return False
        self.con.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        self.con.commit()
        return True


# ----------------------------------------------------------------- contacts (clients and suppliers)

CONTACT_COLUMNS = ("kind", "name", "country", "vat_id", "address", "email", "regime", "category_id", "vat_rate",
                   "withholding_rate", "description", "notes")


class ContactRepository:
    """Clients (kind 'income') and suppliers (kind 'expense') whose data new entries start from."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def all(self, kind: Optional[str] = None) -> List[sqlite3.Row]:
        """Contacts by name, optionally only the clients or only the suppliers."""
        if kind in tuple(Kind):
            return self.con.execute("SELECT * FROM contacts WHERE kind = ? ORDER BY name", (kind,)).fetchall()
        return self.con.execute("SELECT * FROM contacts ORDER BY kind DESC, name").fetchall()

    def get(self, contact_id: int) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()

    def usage(self) -> Dict[int, int]:
        """{contact id: number of entries that carry its name}."""
        rows = self.con.execute(
            "SELECT c.id AS id, COUNT(e.id) AS n FROM contacts c "
            "LEFT JOIN entries e ON e.kind = c.kind AND e.counterparty = c.name COLLATE NOCASE GROUP BY c.id").fetchall()
        return {r["id"]: r["n"] for r in rows}

    def add(self, values: dict) -> int:
        """Raises sqlite3.IntegrityError if a contact of that kind already has the name."""
        cur = self.con.execute(
            f"INSERT INTO contacts ({','.join(CONTACT_COLUMNS)}) VALUES ({','.join('?' * len(CONTACT_COLUMNS))})",
            [values[c] for c in CONTACT_COLUMNS])
        self.con.commit()
        return _new_id(cur)

    def update(self, contact_id: int, values: dict) -> None:
        """Raises sqlite3.IntegrityError if the new name is taken by another contact of the same kind."""
        self.con.execute(f"UPDATE contacts SET {','.join(f'{c} = ?' for c in CONTACT_COLUMNS)} WHERE id = ?",
                         [values[c] for c in CONTACT_COLUMNS] + [contact_id])
        self.con.commit()

    def delete(self, contact_id: int) -> None:
        self.con.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        self.con.commit()

    def remember(self, entry: dict, commit: bool = True) -> bool:
        """Save the counterparty of an entry as a contact unless one with that name exists; True if added."""
        name = " ".join((entry.get("counterparty") or "").split())
        if not name:
            return False
        income = entry["kind"] == Kind.INCOME
        cur = self.con.execute(  # OR IGNORE: a contact of that name may exist already (or appear from a double submit)
            f"INSERT OR IGNORE INTO contacts ({','.join(CONTACT_COLUMNS)}) VALUES ({','.join('?' * len(CONTACT_COLUMNS))})",
            [entry["kind"], name, entry.get("country") or "ES", entry.get("vat_id") or "", "", "", entry["regime"],
             entry.get("category_id"), entry["vat_rate"] if entry["regime"] == Regime.DOMESTIC else "",
             entry["withholding_rate"] if income or entry["withholding_rate"] not in ("", "0") else "", "", ""])
        if commit:
            self.con.commit()
        return cur.rowcount > 0

    def create_from_ledger(self) -> int:
        """Add a contact for every counterparty that appears in the entries, from its latest entry."""
        latest = self.con.execute(
            "SELECT e.* FROM entries e WHERE e.counterparty <> '' AND e.id = ("
            "SELECT e2.id FROM entries e2 WHERE e2.kind = e.kind AND e2.counterparty = e.counterparty COLLATE NOCASE "
            "ORDER BY e2.date DESC, e2.id DESC LIMIT 1)").fetchall()
        added = sum(1 for row in latest if self.remember(dict(row), commit=False))
        self.con.commit()
        return added


# ----------------------------------------------------------------- submissions to AEAT

SUBMISSION_COLUMNS = ("env", "model", "year", "period", "file_sha256", "tipo", "result_cents", "status", "csv",
                      "expediente", "justificante", "fecha", "hora", "errors_json", "warnings_json", "pdf",
                      "presenter_nif", "submitted_at")


class SubmissionRepository:
    """What was sent to AEAT and what it answered."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def add(self, **values: object) -> sqlite3.Row:
        """Store one outcome and return the row."""
        cur = self.con.execute(
            f"INSERT INTO submissions ({','.join(SUBMISSION_COLUMNS)}) VALUES ({','.join('?' * len(SUBMISSION_COLUMNS))})",
            [values[c] for c in SUBMISSION_COLUMNS])
        self.con.commit()
        row = self.get(_new_id(cur))
        assert row is not None  # it was committed one line above
        return row

    def get(self, submission_id: int) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()

    def all(self, limit: int = 50) -> List[sqlite3.Row]:
        """The latest outcomes first."""
        return self.con.execute("SELECT * FROM submissions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


# ----------------------------------------------------------------- entries

ENTRY_COLUMNS = ("kind", "date", "number", "counterparty", "country", "vat_id", "description", "category_id",
                 "regime", "base_cents", "vat_rate", "vat_cents", "withholding_rate", "withholding_cents",
                 "currency", "orig_amount_cents", "fx_rate", "fx_rate_date", "fx_source", "notes")


class EntryRepository:
    _SELECT = ("SELECT e.*, c.name AS category_name, c.code AS category_code, c.irpf_mode, c.vat_mode, "
               "c.annual_cap_cents AS category_cap "
               "FROM entries e LEFT JOIN categories c ON c.id = e.category_id")

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        # A page builds several reports from the same year: the rows are read once and read again only after the
        # connection changed some data (its total_changes moves with every insert, update and delete).
        self._rows: Dict[int, tuple] = {}
        self._entries: Dict[int, tuple] = {}

    @staticmethod
    def to_entry(r: sqlite3.Row) -> calc.Entry:
        """The calculation model of a joined row. Raises ValueError, naming the entry, for a code the app does not know."""
        label = CategoryRepository.label(r["category_code"], r["category_name"]) if r["category_name"] else ""
        try:
            kind, regime = Kind(r["kind"]), Regime(r["regime"])
            irpf_mode, vat_mode = (DeductionMode(r[column] or DeductionMode.FULL) for column in ("irpf_mode", "vat_mode"))
        except ValueError as exc:
            raise ValueError(f"entry {r['id']} of {r['date']}: {exc}") from exc
        return calc.Entry(
            kind=kind, date=date.fromisoformat(r["date"]), regime=regime,
            base_cents=r["base_cents"], vat_rate=Decimal(r["vat_rate"] or "0"), vat_cents=r["vat_cents"],
            withholding_cents=r["withholding_cents"], irpf_mode=irpf_mode, vat_mode=vat_mode,
            category=label, description=r["description"] or r["counterparty"] or "",
            category_cap_cents=r["category_cap"], category_code=r["category_code"] or "",
            counterparty=r["counterparty"] or "", vat_id=r["vat_id"] or "")

    def year_rows(self, year: int) -> List[sqlite3.Row]:
        """Joined rows of a calendar year, oldest first."""
        changes = self.con.total_changes
        cached = self._rows.get(year)
        if cached is None or cached[0] != changes:
            rows = self.con.execute(self._SELECT + " WHERE e.date >= ? AND e.date <= ? ORDER BY e.date, e.id",
                                    (f"{year}-01-01", f"{year}-12-31")).fetchall()
            cached = self._rows[year] = (changes, rows)
        return list(cached[1])

    def year_entries(self, year: int) -> List[calc.Entry]:
        """The calculation entries of a calendar year, oldest first (category names in the current language)."""
        stamp = (self.con.total_changes, i18n.current_language())
        cached = self._entries.get(year)
        if cached is None or cached[0] != stamp:
            cached = self._entries[year] = (stamp, [self.to_entry(r) for r in self.year_rows(year)])
        return list(cached[1])

    def get(self, entry_id: int) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()

    def insert(self, values: dict, document: str = "") -> int:
        cols = ENTRY_COLUMNS + ("document",)
        cur = self.con.execute(f"INSERT INTO entries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               [values[c] for c in ENTRY_COLUMNS] + [document])
        self.con.commit()
        return _new_id(cur)

    def insert_many(self, rows: List[dict]) -> int:
        """Store several entries in one transaction (all or none); returns how many were stored."""
        cols = ENTRY_COLUMNS + ("document",)
        try:
            self.con.executemany(f"INSERT INTO entries ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                 [[values[c] for c in ENTRY_COLUMNS] + [""] for values in rows])
            self.con.commit()
        except sqlite3.Error:
            self.con.rollback()
            raise
        return len(rows)

    def update(self, entry_id: int, values: dict, document: str) -> None:
        cols = ENTRY_COLUMNS + ("document",)
        self.con.execute(f"UPDATE entries SET {','.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                         [values[c] for c in ENTRY_COLUMNS] + [document, entry_id])
        self.con.commit()

    def delete(self, entry_id: int) -> None:
        self.con.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        self.con.commit()

    def copy_dated(self, entry_id: int, on: date) -> int:
        """A copy of an entry without its invoice number or document, dated `on` (monthly subscriptions)."""
        row = self.get(entry_id)
        if row is None:
            raise LookupError(f"no entry {entry_id}")
        values = {c: row[c] for c in ENTRY_COLUMNS}
        values.update(date=on.isoformat(), number="")
        return self.insert(values)

    def years_in_use(self) -> List[int]:
        rows = self.con.execute("SELECT DISTINCT substr(date,1,4) AS y FROM entries ORDER BY y").fetchall()
        return sorted({int(r["y"]) for r in rows} | {date.today().year}, reverse=True)


# ----------------------------------------------------------------- filings

class FilingRepository:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def all(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM filings ORDER BY due_date, model").fetchall()

    def pending(self) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM filings WHERE status = 'pending' ORDER BY due_date, model").fetchall()

    def get(self, filing_id: int) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM filings WHERE id = ?", (filing_id,)).fetchone()

    def years(self) -> List[int]:
        return [r["year"] for r in self.con.execute("SELECT DISTINCT year FROM filings")]

    def for_year_model(self, year: int, model: str) -> List[sqlite3.Row]:
        return self.con.execute("SELECT * FROM filings WHERE year = ? AND model = ? ORDER BY period", (year, model)).fetchall()

    def filed_results(self, year: int, model: str) -> Dict[int, int]:
        """{quarter: result in cents} of the quarterly filings of a model that are filed and have a result."""
        return {int(f["period"][0]): f["result_cents"] for f in self.for_year_model(year, model)
                if f["status"] == FilingStatus.FILED and f["result_cents"] is not None and f["period"][:1].isdigit()}

    def default_rows(self, year: int) -> List[sqlite3.Row]:
        """Rows of the models the tracker creates by itself."""
        marks = ",".join("?" * len(deadlines.KNOWN_MODELS))
        return self.con.execute(f"SELECT * FROM filings WHERE year = ? AND model IN ({marks})",
                                (year, *deadlines.KNOWN_MODELS)).fetchall()

    def insert_template(self, t: deadlines.FilingTemplate) -> None:
        """Create the filing if it does not exist yet."""
        self.con.execute("INSERT OR IGNORE INTO filings (year, model, period, opens, due_date) VALUES (?,?,?,?,?)",
                         (t.year, t.model, t.period, t.opens.isoformat(), t.due.isoformat()))

    def reschedule(self, t: deadlines.FilingTemplate) -> None:
        """Apply a new deadline to the matching filing unless it is filed or its date was edited by hand."""
        self.con.execute("UPDATE filings SET due_date=?, opens=? WHERE year=? AND model=? AND period=? "
                         "AND status='pending' AND due_manual=0",
                         (t.due.isoformat(), t.opens.isoformat(), t.year, t.model, t.period))

    def add_custom(self, year: int, model: str, period: str, due: date) -> None:
        """A hand-made filing (date protected from recalculation). Raises sqlite3.IntegrityError if it exists."""
        self.con.execute("INSERT INTO filings (year, model, period, opens, due_date, due_manual) VALUES (?,?,?,?,?,1)",
                         (year, model, period, (due - timedelta(days=30)).isoformat(), due.isoformat()))

    def set_status(self, filing_id: int, status: str, filed_date: str) -> None:
        self.con.execute("UPDATE filings SET status=?, filed_date=? WHERE id=?", (status, filed_date, filing_id))

    def update_details(self, filing_id: int, *, due_date: str, status: str, filed_date: str, reference: str,
                       result_cents: Optional[int], notes: str, document: str, due_manual: int) -> None:
        self.con.execute("UPDATE filings SET due_date=?, status=?, filed_date=?, reference=?, result_cents=?, "
                         "notes=?, document=?, due_manual=? WHERE id=?",
                         (due_date, status, filed_date, reference, result_cents, notes, document, due_manual, filing_id))

    def mark_filed_until(self, upto: date) -> int:
        cur = self.con.execute("UPDATE filings SET status='filed' WHERE status='pending' AND due_date <= ?",
                               (upto.isoformat(),))
        return cur.rowcount

    def delete(self, filing_id: int) -> None:
        self.con.execute("DELETE FROM filings WHERE id = ?", (filing_id,))

    # deliberate deletions of automatically created filings
    def skipped(self, year: int) -> set:
        return {(r["model"], r["period"])
                for r in self.con.execute("SELECT model, period FROM filing_skips WHERE year = ?", (year,))}

    def clear_skips(self) -> None:
        self.con.execute("DELETE FROM filing_skips")

    def skip(self, year: int, model: str, period: str) -> None:
        self.con.execute("INSERT OR IGNORE INTO filing_skips (year, model, period) VALUES (?,?,?)", (year, model, period))

    def unskip(self, year: int, model: str, period: str) -> None:
        self.con.execute("DELETE FROM filing_skips WHERE year=? AND model=? AND period=?", (year, model, period))

    def commit(self) -> None:
        self.con.commit()


# ----------------------------------------------------------------- AEAT checks

class CheckRepository:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def latest(self, model: str, year: int, period: str) -> Optional[sqlite3.Row]:
        return self.con.execute("SELECT * FROM aeat_checks WHERE model=? AND year=? AND period=? "
                                "ORDER BY id DESC LIMIT 1", (model, year, period)).fetchone()

    def add(self, *, model: str, year: int, period: str, file_sha256: str, tipo: str, result_cents: int,
            ok: bool, errors_json: str, warnings_json: str, pdf: str, checked_at: str) -> sqlite3.Row:
        cur = self.con.execute(
            "INSERT INTO aeat_checks (model, year, period, file_sha256, tipo, result_cents, ok, errors, warnings, "
            "pdf, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (model, year, period, file_sha256, tipo, result_cents, 1 if ok else 0, errors_json, warnings_json, pdf,
             checked_at))
        self.con.commit()
        return self.con.execute("SELECT * FROM aeat_checks WHERE id=?", (cur.lastrowid,)).fetchone()


# ----------------------------------------------------------------- exchange-rate cache

class FxRateCache:
    """Persistent cache of exchange rates fetched online (implements fx.RateCache)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def get(self, currency: str, on: date) -> Optional[Rate]:
        row = self.con.execute("SELECT * FROM fx_rates WHERE currency = ? AND requested = ?",
                               (currency, on.isoformat())).fetchone()
        if not row:
            return None
        return Rate(currency, Decimal(row["per_eur"]), date.fromisoformat(row["rate_date"]), row["source"])

    def put(self, rate: Rate, on: date) -> None:
        self.con.execute("INSERT OR REPLACE INTO fx_rates (currency, requested, per_eur, rate_date, source) "
                         "VALUES (?,?,?,?,?)",
                         (rate.currency, on.isoformat(), format(rate.per_eur, "f"), rate.rate_date.isoformat(), rate.source))
        self.con.commit()
