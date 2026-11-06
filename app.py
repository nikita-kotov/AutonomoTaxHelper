"""Autonomo Tax Helper - local web app for a Spanish autonomo.

Run with run.bat (tray icon, no console) or `python app.py` (console; Exit button or Ctrl+C stops it).
Listens on 127.0.0.1 only.
The application itself lives in the `taxhelper` package; this file only starts it.
"""
import sys

from taxhelper import launcher, tray
from taxhelper.web import create_app

if "--tray" in sys.argv:
    tray.configure_logging()  # before anything else, so a start-up failure lands in app.log and a message box
app = create_app()

if __name__ == "__main__":
    if "--tray" in sys.argv:  # run.bat: icon in the system tray, no console window
        sys.exit(tray.run(app))
    sys.exit(launcher.run(app))
