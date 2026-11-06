"""Small helpers shared by the test modules."""
from taxhelper import db
from taxhelper.settings import SettingsRepository


def set_settings(**changes):
    """Change typed settings directly in the test database."""
    con = db.connect()
    try:
        return SettingsRepository(con).update(**changes)
    finally:
        con.close()


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
