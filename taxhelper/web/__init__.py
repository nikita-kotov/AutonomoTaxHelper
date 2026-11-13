"""Web layer: application factory, request plumbing and one blueprint per area."""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Optional

from flask import Flask

from .. import backup as bk
from .. import db, i18n
from ..aeat.transport import Transport, WindowsStoreTransport
from ..fx import FrankfurterProvider, RateProvider
from . import infra
from .views import (aeat, categories, contacts, dashboard, entries, filings, importing, libros, reports,
                    settings, submit, system)

ROOT = Path(__file__).resolve().parents[2]


def _secret_key() -> str:
    """A random key created once per data folder and kept there, outside the source code."""
    path = db.data_dir() / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def create_app(fx_provider: Optional[RateProvider] = None, aeat_transport: Optional[Transport] = None) -> Flask:
    """Build the app. The data folder comes from TAXHELPER_DATA (see taxhelper.db).

    `fx_provider` supplies exchange rates for foreign-currency invoices (default: ECB reference
    rates via Frankfurter) and `aeat_transport` makes the certificate-authenticated calls to the AEAT
    (default: the Windows certificate store); tests pass fakes.
    """
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    app.secret_key = _secret_key()  # signs the flash-message cookie
    app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024
    app.config["FX_PROVIDER"] = fx_provider or FrankfurterProvider()
    app.config["AEAT_TRANSPORT"] = aeat_transport or WindowsStoreTransport()
    app.config["START_PATH"] = infra.start_path  # where the launcher and the tray open the browser
    app.jinja_env.globals["t"] = i18n.t

    infra.init_app(app)
    for module in (dashboard, entries, contacts, importing, reports, libros, filings, settings, categories, aeat,
                   submit, system):
        app.register_blueprint(module.bp)

    con = db.connect()
    try:
        db.init_db(con)
        bk.run_backup(con)  # no-op unless a backup folder is set and the last backup is old
    finally:
        con.close()
    return app
