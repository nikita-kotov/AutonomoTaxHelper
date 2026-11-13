"""Small helpers shared by the test modules."""
from datetime import date
from decimal import Decimal

from taxhelper import db
from taxhelper.calc import Entry
from taxhelper.settings import SettingsRepository


def set_settings(**changes):
    """Change typed settings directly in the test database."""
    con = db.connect()
    try:
        return SettingsRepository(con).update(**changes)
    finally:
        con.close()


def calendar_from_last_year():
    """An activity start at the beginning of last year: the filings calendar then holds last year and this one
    (a new installation has no past periods to file)."""
    set_settings(activity_start=date(date.today().year - 1, 1, 1))


def load_settings():
    con = db.connect()
    try:
        return SettingsRepository(con).load()
    finally:
        con.close()


def category_id(code):
    con = db.connect()
    try:
        return con.execute("SELECT id FROM categories WHERE code = ?", (code,)).fetchone()["id"]
    finally:
        con.close()


def text(response):
    return response.get_data(as_text=True)


def entry(kind, base, vat=0, rate=21, d=date(2026, 2, 10), regime="domestic"):
    """A calculation entry with an IVA rate, for the pure 303 / 130 tests."""
    return Entry(kind=kind, date=d, regime=regime, base_cents=base, vat_cents=vat, vat_rate=Decimal(rate), category="x")


def expense_form(**over):
    """The posted fields of a valid expense; keyword arguments replace single fields."""
    data = {"date": "2026-02-10", "counterparty": "Vendor", "regime": "domestic", "base": "100", "vat_rate": "21",
            "currency": "EUR", "category_id": str(category_id("software"))}
    data.update(over)
    return data
