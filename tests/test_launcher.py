"""Starting and stopping the app as one process."""
import socket
import threading

import pytest

from taxhelper import launcher


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeServer:
    def __init__(self):
        self.stopped = threading.Event()

    def shutdown(self):
        self.stopped.set()


def test_exit_button_is_hidden_when_not_run_by_the_launcher(client):
    assert "/quit" not in client.get("/").get_data(as_text=True)
    assert client.post("/quit").status_code == 404


def test_exit_button_stops_the_server_and_says_so(fresh_app):
    server = FakeServer()
    fresh_app.config["SERVER"] = server
    client = fresh_app.test_client()
    assert 'action="/quit"' in client.get("/").get_data(as_text=True)
    page = client.post("/quit", headers={"Sec-Fetch-Site": "same-origin"}).get_data(as_text=True)
    assert "has stopped" in page
    assert server.stopped.wait(2)


def test_another_site_cannot_stop_the_app(fresh_app):
    server = FakeServer()
    fresh_app.config["SERVER"] = server
    assert fresh_app.test_client().post("/quit", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert not server.stopped.wait(0.6)


def test_run_opens_the_browser_only_once_the_port_is_bound_and_stops_on_request(fresh_app):
    port = free_port()
    opened, logs = [], []

    def open_browser(url):
        # the server is already bound here, so the page cannot load "too early"
        assert launcher.already_running("127.0.0.1", port)
        opened.append(url)
        threading.Timer(0.2, lambda: launcher.request_stop(fresh_app)).start()

    code = launcher.run(fresh_app, port=port, open_browser=open_browser, log=logs.append)
    assert code == 0 and opened == [f"http://127.0.0.1:{port}/settings"]  # a new installation starts at the settings
    assert "SERVER" not in fresh_app.config
    assert not launcher.already_running("127.0.0.1", port)


def test_second_start_only_opens_the_running_copy(fresh_app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    opened = []
    try:
        assert launcher.run(fresh_app, port=port, open_browser=opened.append, log=lambda m: None) == 0
    finally:
        listener.close()
    assert opened == [f"http://127.0.0.1:{port}/settings"] and "SERVER" not in fresh_app.config


def test_a_busy_port_without_an_app_is_reported_not_crashed(fresh_app, monkeypatch):
    monkeypatch.setattr(launcher, "already_running", lambda *a: False)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    logs = []
    try:
        code = launcher.run(fresh_app, port=listener.getsockname()[1], open_browser=pytest.fail, log=logs.append)
    finally:
        listener.close()
    assert code == 1 and logs


# ---- tray ----

class FakeIcon:
    """Stands in for pystray.Icon: run() calls the setup hook, then waits until stop()."""
    instances = []

    def __init__(self, name, image, title, menu):
        self.name, self.image, self.title, self.menu = name, image, title, menu
        self.visible = False
        self._stopped = threading.Event()
        FakeIcon.instances.append(self)

    def run(self, setup=None):
        if setup:
            setup(self)
        assert self._stopped.wait(10)

    def stop(self):
        self._stopped.set()


class FakePystray:
    Icon = FakeIcon

    class Menu:
        def __init__(self, *items):
            self.items = items

    class MenuItem:
        def __init__(self, text, action, default=False):
            self.text, self.action, self.default = text, action, default


def test_icon_image_is_drawn_in_code():
    from taxhelper.icon import make_icon
    image = make_icon(64)
    assert image.size == (64, 64) and image.getpixel((32, 4))[3] == 255 and image.getpixel((0, 0))[3] == 0


def test_tray_shows_the_icon_opens_the_page_and_exits_from_the_menu(fresh_app):
    from taxhelper import tray
    FakeIcon.instances.clear()
    port, opened = free_port(), []
    holder = {}

    def open_browser(url):
        opened.append(url)
        holder.setdefault("exit", threading.Timer(0.3, lambda: FakeIcon.instances[0].menu.items[2].action(FakeIcon.instances[0])))
        if len(opened) == 1:
            holder["exit"].start()

    assert tray.run(fresh_app, port=port, pystray_module=FakePystray, open_browser=open_browser) == 0
    icon = FakeIcon.instances[0]
    assert icon.visible and icon.title == "Autonomo Tax Helper"
    open_item, folder_item, exit_item = icon.menu.items
    assert open_item.default and open_item.text == "Open Autonomo Tax Helper" and exit_item.text == "Exit"
    assert opened == [f"http://127.0.0.1:{port}/settings"]
    assert "SERVER" not in fresh_app.config and not launcher.already_running("127.0.0.1", port)


def test_tray_icon_disappears_when_the_page_stops_the_server(fresh_app):
    from taxhelper import tray
    FakeIcon.instances.clear()
    port = free_port()
    stopper = lambda url: threading.Timer(0.3, lambda: launcher.request_stop(fresh_app)).start()  # noqa: E731
    assert tray.run(fresh_app, port=port, pystray_module=FakePystray, open_browser=stopper) == 0
    assert FakeIcon.instances[0]._stopped.is_set()


def test_tray_without_pystray_falls_back_to_the_console_launcher(fresh_app, monkeypatch):
    from taxhelper import tray
    monkeypatch.setattr(tray, "load_pystray", lambda: None)
    port = free_port()
    stopper = lambda url: threading.Timer(0.3, lambda: launcher.request_stop(fresh_app)).start()  # noqa: E731
    assert tray.run(fresh_app, port=port, open_browser=stopper) == 0


def test_tray_menu_is_translated(fresh_app):
    from taxhelper import i18n
    for lang, word in (("ru", "Выход"), ("es", "Salir")):
        i18n.set_language(lang)
        assert i18n.t("tray.exit") == word and i18n.t("tray.open").endswith("Autonomo Tax Helper")


def test_the_crash_window_speaks_the_saved_language(fresh_app):
    from pathlib import Path

    from taxhelper import tray
    from tests.support import set_settings
    for lang, start in (("ru", "Autonomo Tax Helper не запустился"), ("es", "Autonomo Tax Helper no ha podido"),
                        ("en", "Autonomo Tax Helper could not start")):
        set_settings(language=lang)
        message = tray.crash_message(ValueError, "boom", Path("C:/data/app.log"))
        assert message.startswith(start) and "ValueError: boom" in message and "app.log" in message


def test_without_a_saved_language_the_crash_window_follows_the_system(fresh_app, monkeypatch):
    from pathlib import Path

    from taxhelper import tray
    monkeypatch.setattr(tray, "_system_language", lambda: "es")
    assert tray.crash_message(ValueError, "boom", Path("app.log")).startswith("Autonomo Tax Helper no ha podido")
    monkeypatch.setattr(tray, "_language", lambda: 1 / 0)  # a damaged database: the language of the system still decides
    assert tray.crash_message(ValueError, "boom", Path("app.log")).startswith("Autonomo Tax Helper no ha podido")


def test_the_system_language_is_one_of_ours_or_english():
    from taxhelper import i18n, tray
    assert tray._system_language() in i18n.available()


def test_a_new_installation_opens_at_the_settings_and_a_set_up_one_at_the_dashboard(fresh_app):
    from support import set_settings
    base = "http://127.0.0.1:5057"
    assert launcher.start_url(fresh_app) == base + "/settings"
    assert "Welcome" in fresh_app.test_client().get("/settings").get_data(as_text=True)

    set_settings(holder_nif="00000000T")
    assert launcher.start_url(fresh_app) == base + "/"
    assert "Welcome" not in fresh_app.test_client().get("/settings").get_data(as_text=True)


def test_an_entry_alone_is_enough_to_start_at_the_dashboard(fresh_app):
    from support import expense_form
    fresh_app.test_client().post("/entries/new/expense", data=expense_form())
    assert launcher.start_url(fresh_app).endswith(":5057/")


def test_an_app_without_a_start_page_opens_at_the_root():
    from flask import Flask
    assert launcher.start_url(Flask("plain"), port=1234) == "http://127.0.0.1:1234/"
