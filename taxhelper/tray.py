"""Run the app with an icon in the system tray (no console window).

The server runs in a background thread; the tray menu opens the interface in the browser, opens the data
folder, or stops everything. The Exit button inside the page stops the server too, and then the icon goes away.
Problems are written to app.log in the data folder, because there is no console to show them.
"""
from __future__ import annotations

import importlib
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import webbrowser
from typing import Callable, Optional

from flask import Flask

from . import db, i18n, launcher
from .settings import SettingsRepository

TITLE = "Autonomo Tax Helper"


def configure_logging() -> None:
    """Send log records and uncaught exceptions to a small rotating app.log in the data folder."""
    handler = logging.handlers.RotatingFileHandler(db.data_dir() / "app.log", maxBytes=512_000, backupCount=2,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def hook(kind, value, trace):
        logging.getLogger("taxhelper").error("Uncaught exception", exc_info=(kind, value, trace))
        alert(f"{TITLE} could not start or has crashed.\n\n{kind.__name__}: {value}\n\n"
              f"Details: {db.data_dir() / 'app.log'}")
    sys.excepthook = hook


def alert(message: str) -> None:
    """Tell the user something went wrong when there is no console to print it to (Windows message box)."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)


def open_folder(path) -> None:
    """Show a folder in the system file manager."""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - a folder of our own
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def _language() -> str:
    """The saved interface language (the tray has no browser request to take a preference from)."""
    con = db.connect()
    try:
        saved = SettingsRepository(con).load().language
    finally:
        con.close()
    return saved if saved in i18n.available() else i18n.FALLBACK


def load_pystray():
    """The pystray module, or None when it (or Pillow, or a desktop to show the icon on) is missing."""
    try:
        import pystray
        importlib.import_module(".icon", __package__)  # needs Pillow
        return pystray
    except Exception:  # ImportError, or pystray's own error on a system without a tray
        logging.getLogger("taxhelper").exception("tray icon unavailable")
        return None


def run(app: Flask, host: str = launcher.HOST, port: int = launcher.PORT, pystray_module=None,
        open_browser: Optional[Callable[[str], object]] = None) -> int:
    """Serve `app` in a background thread with a tray icon until Exit; returns a process exit code.

    Without a usable tray it falls back to `launcher.run`. A copy that is already running is only opened in
    the browser. `pystray_module` and `open_browser` can be replaced, which the tests do.
    """
    open_browser = open_browser or webbrowser.open
    log = logging.getLogger("taxhelper").info
    pystray = pystray_module or load_pystray()
    if pystray is None:
        return launcher.run(app, host, port, open_browser, log)
    url = f"http://{host}:{port}/"
    if launcher.already_running(host, port):
        open_browser(url)
        return 0
    server = launcher.bind(app, host, port, log)
    if server is None:
        return 1

    from .icon import make_icon
    i18n.set_language(_language())
    serving = threading.Thread(target=server.serve_forever, name="server", daemon=True)
    serving.start()
    icon = pystray.Icon(
        "AutonomoTaxHelper", make_icon(64), TITLE,
        pystray.Menu(
            pystray.MenuItem(i18n.t("tray.open"), lambda *_: open_browser(url), default=True),
            pystray.MenuItem(i18n.t("tray.folder"), lambda *_: open_folder(db.data_dir())),
            pystray.MenuItem(i18n.t("tray.exit"), lambda icon, *_: icon.stop())))

    def watch_server():
        serving.join()  # the page's Exit button stops the server: then the icon has nothing to show
        icon.stop()
    threading.Thread(target=watch_server, name="watch", daemon=True).start()

    def ready(icon):
        icon.visible = True
        open_browser(url)
    try:
        icon.run(setup=ready)
    finally:
        server.shutdown()
        serving.join(5)
        server.server_close()
        app.config.pop("SERVER", None)
    return 0
