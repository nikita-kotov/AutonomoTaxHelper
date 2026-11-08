"""Redirect targets, cross-site posts and the secret key."""

import pytest

from taxhelper.web import create_app
from taxhelper.web.helpers import safe_next


@pytest.mark.parametrize("target", ["/\\evil.example", "\\\\evil.example", "/\\/evil.example", "//evil.example",
                                    "https://evil.example", "javascript:alert(1)", "/ok\nSet-Cookie: x=1", "", None,
                                    "/\tevil.example", "evil.example"])
def test_unsafe_redirect_targets_fall_back(target):
    assert safe_next(target, "/home") == "/home"


@pytest.mark.parametrize("target", ["/", "/entries?year=2026&q=2", "/filings#top", "/reports/303?year=2026"])
def test_local_redirect_targets_are_kept(target):
    assert safe_next(target, "/home") == target


def test_the_language_switch_refuses_a_backslash_redirect(client):
    r = client.post("/lang", data={"lang": "en", "next": "/\\evil.example"})
    assert r.headers["Location"].endswith("/") and "evil" not in r.headers["Location"]


def test_posts_marked_cross_site_by_the_browser_are_refused(client):
    assert client.post("/lang", data={"lang": "es"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/lang", data={"lang": "es"}, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 302
    assert client.post("/lang", data={"lang": "es"}).status_code == 302  # clients that send no such header still work


def test_the_secret_key_is_random_per_data_folder_and_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "a"))
    first = create_app().secret_key
    assert first == create_app().secret_key and len(first) == 64 and first != "local-only-app"
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "b"))
    assert create_app().secret_key != first
