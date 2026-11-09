"""Interface localisation without external dependencies.

Translations live in ``locales/<code>.json`` as flat ``"dotted.key": "text"`` maps.
A locale file also carries a ``_meta`` object: display name, date format, number
separators and the money pattern. English is the fallback for missing keys.

To add a language: copy ``locales/en.json`` to ``locales/<code>.json``, translate the
values and ``_meta``; it shows up in the language switcher automatically.

Texts may contain ``{name}`` placeholders, filled from keyword arguments of ``t()``.
User-facing messages produced by non-UI code are passed around as ``Msg`` objects
(or raised as ``UserError``) and translated where they are shown.
"""
from __future__ import annotations

import contextvars
import json
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Union

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
FALLBACK = "en"

_current = contextvars.ContextVar("taxhelper_language", default=FALLBACK)
_cache: Dict[str, dict] = {}
_available: Optional[Dict[str, str]] = None

_DEFAULT_META = {"name": "?", "date_format": "%d/%m/%Y", "decimal": ".", "thousands": ",", "money": "€{amount}"}


def load(code: str) -> dict:
    """Return the translation map for a language code ({} if the file is missing)."""
    if code not in _cache:
        path = LOCALES_DIR / f"{code}.json"
        try:
            _cache[code] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            _cache[code] = {}
    return _cache[code]


def reload() -> None:
    """Drop cached translations and the language list (used by tests; the app reads locale files once)."""
    global _available
    _cache.clear()
    _available = None


def available() -> Dict[str, str]:
    """Language code -> native display name for every locale file found (cached until reload())."""
    global _available
    if _available is None:
        _available = {}
        if LOCALES_DIR.is_dir():
            for p in sorted(LOCALES_DIR.glob("*.json")):
                _available[p.stem] = load(p.stem).get("_meta", {}).get("name", p.stem)
    return _available


def set_language(code: Optional[str]) -> str:
    """Select the language for the current context; unknown codes fall back to English."""
    code = code if code in available() else FALLBACK
    _current.set(code)
    return code


def current_language() -> str:
    return _current.get()


def meta(code: Optional[str] = None) -> dict:
    return {**_DEFAULT_META, **load(code or current_language()).get("_meta", {})}


def exists(key: str) -> bool:
    """True if the key has a translation in the current language or the fallback."""
    return key in load(current_language()) or key in load(FALLBACK)


class Msg:
    """A translatable message with parameters, translated lazily when displayed."""
    __slots__ = ("key", "params")

    def __init__(self, key: str, **params: object) -> None:
        self.key = key
        self.params = params

    def __str__(self) -> str:
        return t(self.key, **self.params)

    def __repr__(self) -> str:
        return f"Msg({self.key!r}, {self.params!r})"


class UserError(ValueError):
    """An error whose message is meant for the user and is translated when shown."""

    def __init__(self, key: str, **params: object) -> None:
        self.msg = Msg(key, **params)
        super().__init__(key)

    def __str__(self) -> str:
        return str(self.msg)


def t(key: Union[str, Msg], **params: object) -> str:
    """Translate a key (or a Msg) into the current language, falling back to English, then to the key."""
    if isinstance(key, Msg):
        name, params = key.key, key.params
    elif isinstance(key, str):
        name = key
    else:
        return str(key)
    text = None
    for code in (current_language(), FALLBACK):
        text = load(code).get(name)
        if isinstance(text, str):
            break
        text = None
    if text is None:
        return name
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError, ValueError):
        return text


def format_money(cents: int, code: Optional[str] = None, whole: bool = False) -> str:
    """Format integer cents using the locale's separators and currency pattern; `whole` rounds to whole euros."""
    m = meta(code)
    cents = int(cents)
    euros, whole_cents = divmod(abs(cents), 100)
    rem: Optional[int] = whole_cents
    if whole:
        euros, rem = euros + (whole_cents >= 50), None
    grouped = f"{euros:,}".replace(",", m["thousands"])
    text = m["money"].format(amount=grouped if rem is None else f"{grouped}{m['decimal']}{rem:02d}")
    return "-" + text if cents < 0 and (euros or rem) else text


def format_date(d: Union[date, str, None], code: Optional[str] = None) -> str:
    """Format a date (or ISO string) with the locale's date format."""
    if not d:
        return ""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d.strftime(meta(code)["date_format"])

