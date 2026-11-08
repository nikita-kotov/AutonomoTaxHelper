"""SQLite plumbing: where the data lives, the schema, migrations and first-run seed data.

Queries live in `repositories.py` and `settings.py`; this module only prepares the database.
Where the data folder is: see `resolve_data_dir` (TAXHELPER_DATA, else the per-user app-data folder).
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import calc

SCHEMA_VERSION = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    irpf_mode TEXT NOT NULL DEFAULT 'full',
    vat_mode TEXT NOT NULL DEFAULT 'full',
    notes TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 100,
    code TEXT,
    annual_cap_cents INTEGER
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('income','expense')),
    date TEXT NOT NULL,
    number TEXT NOT NULL DEFAULT '',
    counterparty TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT 'ES',
    vat_id TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id),
    regime TEXT NOT NULL DEFAULT 'domestic',
    base_cents INTEGER NOT NULL,
    vat_rate TEXT NOT NULL DEFAULT '0',
    vat_cents INTEGER NOT NULL DEFAULT 0,
    withholding_rate TEXT NOT NULL DEFAULT '0',
    withholding_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'EUR',
    orig_amount_cents INTEGER,
    fx_rate TEXT NOT NULL DEFAULT '',
    fx_rate_date TEXT NOT NULL DEFAULT '',
    fx_source TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);

-- Clients (kind 'income') and suppliers (kind 'expense') with the values that new entries start from.
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('income','expense')),
    name TEXT NOT NULL COLLATE NOCASE,
    country TEXT NOT NULL DEFAULT 'ES',
    vat_id TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    regime TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    vat_rate TEXT NOT NULL DEFAULT '',
    withholding_rate TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE (kind, name)
);

-- Exchange rates already fetched, so a rate is looked up online only once.
CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT NOT NULL,
    requested TEXT NOT NULL,
    per_eur TEXT NOT NULL,
    rate_date TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (currency, requested)
);

CREATE TABLE IF NOT EXISTS aeat_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    period TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT '',
    result_cents INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    warnings TEXT NOT NULL DEFAULT '[]',
    pdf TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aeat_checks ON aeat_checks(model, year, period);

CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    model TEXT NOT NULL,
    period TEXT NOT NULL,
    opens TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','filed','not_required')),
    filed_date TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    result_cents INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    document TEXT NOT NULL DEFAULT '',
    due_manual INTEGER NOT NULL DEFAULT 0,
    UNIQUE (year, model, period)
);

-- Declarations sent to AEAT (the test environment for now) and what AEAT answered.
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    env TEXT NOT NULL,
    model TEXT NOT NULL,
    year INTEGER NOT NULL,
    period TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    tipo TEXT NOT NULL,
    result_cents INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted','rejected','error')),
    csv TEXT NOT NULL DEFAULT '',
    expediente TEXT NOT NULL DEFAULT '',
    justificante TEXT NOT NULL DEFAULT '',
    fecha TEXT NOT NULL DEFAULT '',
    hora TEXT NOT NULL DEFAULT '',
    errors_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    pdf TEXT NOT NULL DEFAULT '',
    presenter_nif TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL
);

-- Auto-created filings the user deleted on purpose; they must not be created again.
CREATE TABLE IF NOT EXISTS filing_skips (
    year INTEGER NOT NULL,
    model TEXT NOT NULL,
    period TEXT NOT NULL,
    PRIMARY KEY (year, model, period)
);
"""

# code, irpf_mode, vat_mode. Names and hints come from locales/*.json: cat.<code> and cat.<code>.note.
DEFAULT_CATEGORIES = [
    ("software", "full", "full"),
    ("professional", "full", "full"),
    ("reta", "full", "none"),
    ("health", "full", "none"),
    ("mobile", "full", "full"),
    ("electricity", "home_supply", "home_supply"),
    ("gas", "home_supply", "home_supply"),
    ("water", "home_supply", "home_supply"),
    ("internet", "home_supply", "home_supply"),
    ("rent", "home_property", "none"),
    ("office_rent", "full", "full"),
    ("ibi", "home_property", "none"),
    ("equipment", "full", "full"),
    ("office", "full", "full"),
    ("marketing", "full", "full"),
    ("training", "full", "full"),
    ("transport", "full", "full"),
    ("bank", "full", "none"),
    ("other", "full", "full"),
    ("nondeductible", "none", "none"),
]

# Russian names written by the first version of the app -> category code (migration only).
LEGACY_RU_NAMES = {
    "Софт и подписки (ИИ, SaaS, хостинг)": "software",
    "Профессиональные услуги (gestoría, юрист)": "professional",
    "Cuota de autónomos (RETA)": "reta",
    "Медстраховка (seguro de salud)": "health",
    "Мобильная связь (рабочая линия)": "mobile",
    "Электричество (дом)": "electricity",
    "Газ (дом)": "gas",
    "Вода (дом)": "water",
    "Интернет / стационарный телефон (дом)": "internet",
    "Аренда жилья (часть под офис)": "rent",
    "IBI / comunidad / seguro de hogar": "ibi",
    "Оборудование и мелкая техника": "equipment",
    "Офис и канцелярия": "office",
    "Маркетинг и реклама": "marketing",
    "Обучение и литература": "training",
    "Транспорт и командировки": "transport",
    "Банковские комиссии": "bank",
    "Прочие расходы": "other",
    "Не вычитается": "nondeductible",
}


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """The per-user application-data folder (outside the project, so it can never be committed)."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "TaxHelper"


def resolve_data_dir() -> Path:
    """Where the data lives:

    1. the TAXHELPER_DATA environment variable, if set;
    2. a ``data`` folder inside the project, only if it already holds a database (installs made by
       earlier versions keep working until their data is moved);
    3. otherwise the per-user application-data folder.
    """
    env = os.environ.get("TAXHELPER_DATA")
    if env:
        return Path(env)
    legacy = PROJECT_ROOT / "data"
    if (legacy / "taxhelper.db").exists():
        return legacy
    return user_data_dir()


def _keep_out_of_git(folder: Path) -> None:
    """A data folder inside the project gets its own catch-all .gitignore, so the database and the
    invoices cannot be committed even if the project's .gitignore is lost or the folder is copied."""
    try:
        folder.resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return  # outside the project: nothing to protect against
    marker = folder / ".gitignore"
    if not marker.exists():
        marker.write_text("# Autonomo Tax Helper data (financial records): never commit\n*\n", encoding="utf-8")


def data_dir() -> Path:
    """The folder holding the database and the attached documents (created on first use)."""
    folder = resolve_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "documents").mkdir(exist_ok=True)
    _keep_out_of_git(folder)
    return folder


def documents_dir() -> Path:
    return data_dir() / "documents"


def db_path() -> Path:
    return data_dir() / "taxhelper.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ----------------------------------------------------------------- migrations

def _columns(con: sqlite3.Connection, table: str) -> set:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _add_column(con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    if column not in _columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_old_orig_amount(con: sqlite3.Connection) -> None:
    """Early versions kept a free-text «20.00 USD» note; move its number to orig_amount_cents and drop it."""
    if "orig_amount" not in _columns(con, "entries"):
        return
    for row in con.execute("SELECT id, orig_amount FROM entries WHERE orig_amount != '' AND currency != 'EUR'").fetchall():
        match = re.match(r"\s*(-?[\d.,]+)", row["orig_amount"])
        if match:
            try:
                con.execute("UPDATE entries SET orig_amount_cents = ? WHERE id = ?",
                            (calc.parse_money(match.group(1)), row["id"]))
            except ValueError:
                pass
    try:
        con.execute("ALTER TABLE entries DROP COLUMN orig_amount")
    except sqlite3.OperationalError:  # SQLite older than 3.35 cannot drop columns; the column is just ignored
        pass


def _repair_exponent_rates(con: sqlite3.Connection) -> None:
    """Early versions stored 10 % as '1E+1' (Decimal.normalize); rewrite such rates as plain numbers."""
    for column in ("vat_rate", "withholding_rate"):
        for row in con.execute(f"SELECT id, {column} AS v FROM entries WHERE {column} LIKE '%E%'").fetchall():
            try:
                plain = format(Decimal(row["v"]).normalize(), "f")
            except InvalidOperation:
                continue
            con.execute(f"UPDATE entries SET {column} = ? WHERE id = ?", (plain, row["id"]))


def _give_legacy_categories_codes(con: sqlite3.Connection) -> None:
    """Categories created by the first (Russian-only) version get codes, so their names translate."""
    for ru_name, code in LEGACY_RU_NAMES.items():
        row = con.execute("SELECT id FROM categories WHERE name = ? AND code IS NULL", (ru_name,)).fetchone()
        if row and not con.execute("SELECT 1 FROM categories WHERE name = ?", (code,)).fetchone():
            con.execute("UPDATE categories SET name = ?, code = ?, notes = '' WHERE id = ?", (code, code, row["id"]))


def migrate(con: sqlite3.Connection) -> None:
    """Bring an older database up to the current schema (idempotent)."""
    _add_column(con, "categories", "code", "TEXT")
    _add_column(con, "categories", "annual_cap_cents", "INTEGER")
    _add_column(con, "filings", "due_manual", "INTEGER NOT NULL DEFAULT 0")
    _add_column(con, "entries", "orig_amount_cents", "INTEGER")
    _add_column(con, "entries", "fx_rate", "TEXT NOT NULL DEFAULT ''")
    _add_column(con, "entries", "fx_rate_date", "TEXT NOT NULL DEFAULT ''")
    _add_column(con, "entries", "fx_source", "TEXT NOT NULL DEFAULT ''")
    _migrate_old_orig_amount(con)
    _repair_exponent_rates(con)
    _give_legacy_categories_codes(con)


def _add_category_once(con: sqlite3.Connection, code: str, irpf_mode: str, vat_mode: str, after: str) -> None:
    """Add a default category that older databases lack, unless one with that code or name is there already.

    It runs once, when the schema version moves past the one that introduced the category, so a category the user
    deletes later does not come back. The new row is sorted right after the category `after`.
    """
    if con.execute("SELECT 1 FROM categories WHERE code = ? OR name = ?", (code, code)).fetchone():
        return
    row = con.execute("SELECT sort FROM categories WHERE code = ?", (after,)).fetchone()
    sort = (row[0] + 1) if row else 1000
    con.execute("INSERT INTO categories (name, code, irpf_mode, vat_mode, notes, sort) VALUES (?,?,?,?,'',?)",
                (code, code, irpf_mode, vat_mode, sort))


def init_db(con: sqlite3.Connection) -> None:
    """Create tables, migrate old data and seed the default categories (idempotent)."""
    con.executescript(SCHEMA)
    migrate(con)
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version < 6 and con.execute("SELECT COUNT(*) FROM categories").fetchone()[0]:
        _add_category_once(con, "office_rent", "full", "full", after="rent")  # version 6: rent with a retention (115)
    if version < SCHEMA_VERSION:
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    if con.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO categories (name, code, irpf_mode, vat_mode, notes, sort) VALUES (?,?,?,?,'',?)",
            [(code, code, irpf, vat, idx * 10) for idx, (code, irpf, vat) in enumerate(DEFAULT_CATEGORIES)])
    con.commit()
