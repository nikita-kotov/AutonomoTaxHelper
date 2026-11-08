"""Every "are you sure?" dialog must reach the browser as one complete JavaScript call.

A JSON string put inside a double-quoted HTML attribute ends the attribute at its first quote, the handler then fails to
compile and the delete button submits without asking.
"""
import json
import re
from html.parser import HTMLParser

from tests.support import text


class HandlerCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.handlers = []

    def handle_starttag(self, tag, attrs):
        self.handlers += [(tag, name, value) for name, value in attrs if name.startswith("on")]


def confirm_handlers(page):
    collector = HandlerCollector()
    collector.feed(page)
    return [h for h in collector.handlers if "confirm" in (h[2] or "")]


def assert_complete_dialog(handlers):
    assert handlers, "the page has no confirmation dialog"
    for tag, name, value in handlers:
        match = re.fullmatch(r"return confirm\((\".*\")\)", value, re.S)
        assert match, f"<{tag} {name}> is not a complete confirm() call: {value!r}"
        assert json.loads(match.group(1))  # the message is a well-formed string


def test_entry_deletion_asks_first(client):
    client.post("/entries/new/income", data=dict(date="2026-02-05", counterparty="Client", country="ES", regime="domestic",
                                                  base="100", vat_rate="21", withholding_rate="0", currency="EUR"))
    assert_complete_dialog(confirm_handlers(text(client.get("/entries/1/edit"))))


def test_contact_deletion_asks_first(client):
    client.post("/contacts/new/income", data=dict(name="Acme", country="ES"))
    assert_complete_dialog(confirm_handlers(text(client.get("/contacts/1/edit"))))


def test_category_deletion_asks_first(client):
    assert_complete_dialog(confirm_handlers(text(client.get("/categories"))))


def test_filing_deletion_and_bulk_marking_ask_first(client):
    page = text(client.get("/filings"))
    assert_complete_dialog(confirm_handlers(page))
    filing_id = int(re.search(r"/filings/(\d+)/edit", page).group(1))
    assert_complete_dialog(confirm_handlers(text(client.get(f"/filings/{filing_id}/edit"))))


def test_exit_button_asks_first_in_every_language(fresh_app):
    fresh_app.config["SERVER"] = object()  # what taxhelper.launcher registers; it makes the Exit button appear
    client = fresh_app.test_client()
    for lang in ("en", "ru", "es"):
        client.post("/lang", data={"lang": lang, "next": "/"})
        assert_complete_dialog(confirm_handlers(text(client.get("/"))))
