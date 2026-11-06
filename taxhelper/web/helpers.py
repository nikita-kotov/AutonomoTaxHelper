"""Small request helpers shared by the views."""
from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from flask import request


def current_year() -> int:
    """The year selected with ?year=, defaulting to the current one."""
    y = request.args.get("year", type=int)
    return y if y and 2000 <= y <= 2100 else date.today().year


def safe_next(target: str, default: str) -> str:
    """A redirect target from a form: only same-site absolute paths ('/x?y=1') are allowed.

    Browsers read a backslash like a slash, so '/\\host' would be protocol-relative: any backslash,
    control character, scheme or host part falls back to the default.
    """
    if not target or "\\" in target or any(ord(ch) < 32 for ch in target):
        return default
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return default
    return target


def quarter_arg(source, default=1):
    """Quarter 1..4 from request args/form (anything else becomes the default)."""
    q = source.get("q", type=int)
    return q if q in (1, 2, 3, 4) else default
