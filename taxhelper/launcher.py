"""Starting and stopping the app as one process.

`python app.py` (run.bat) binds the port first, then opens the browser, so the page never loads before the
server is ready. The app is stopped from its own "Exit" button (or Ctrl+C in the console): the server and the
console window close together instead of leaving a server running behind a closed tab.
"""
from __future__ import annotations

import socket
import threading
import webbrowser
from typing import Callable, Optional

from flask import Flask
from werkzeug.serving import make_server

HOST = "127.0.0.1"
PORT = 5057


def start_url(app: Flask, host: str = HOST, port: int = PORT) -> str:
    """The address the browser is opened at: the app's own start page (see `START_PATH`), else the root."""
    path = app.config.get("START_PATH")
    return f"http://{host}:{port}{path() if path else '/'}"


def already_running(host: str = HOST, port: int = PORT) -> bool:
    """True when something already accepts connections on host:port (normally another copy of the app).

    Parameters: the address to probe. Returns a bool.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def bind(app: Flask, host: str = HOST, port: int = PORT, log: Callable[[str], object] = print):
    """Bind the port and register the server in app.config["SERVER"] (which makes the Exit button appear).

    Returns the server, or None (after logging why) when the port is busy or not allowed.
    """
    try:
        server = make_server(host, port, app, threaded=True)  # bound and listening from here on
    except (OSError, SystemExit) as exc:  # werkzeug exits instead of raising when the port is taken
        log(f"Cannot listen on http://{host}:{port}/: the port is busy or not allowed ({exc}).")
        return None
    app.config["SERVER"] = server
    return server


def run(app: Flask, host: str = HOST, port: int = PORT, open_browser: Optional[Callable[[str], object]] = None,
        log: Callable[[str], object] = print) -> int:
    """Serve `app` until the Exit button or Ctrl+C; returns a process exit code.

    If the port is already taken by a running copy, only opens it in the browser and returns 0.
    `open_browser` (default: the system browser) and `log` can be replaced, which the tests do.
    """
    open_browser = open_browser or webbrowser.open
    url = start_url(app, host, port)
    if already_running(host, port):
        log(f"Already running at {url} - opening it.")
        open_browser(url)
        return 0
    server = bind(app, host, port, log)
    if server is None:
        return 1
    log(f"Autonomo Tax Helper is running at {url}\nUse the Exit button in the app (or Ctrl+C here) to stop it.")
    open_browser(url)  # connections queue until serve_forever starts, so an early request just waits
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.config.pop("SERVER", None)
    return 0


def request_stop(app: Flask) -> bool:
    """Ask the running server to stop (from a request handler: it stops once the response is sent).

    Returns False when the app is not run by `run` (tests, flask run), True otherwise.
    """
    server = app.config.get("SERVER")
    if server is None:
        return False
    threading.Timer(0.3, server.shutdown).start()  # shutdown() blocks until serve_forever returns
    return True
