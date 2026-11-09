"""Foreign-currency invoices: converting an invoice amount to euros.

The euro amount is what every tax calculation uses. For a foreign-currency invoice the app
stores the original amount, the exchange rate and where that rate came from, so the euro
base can be justified later.

Rates are quoted the way the ECB quotes them: units of the currency per 1 EUR. The default
source is the ECB reference rate of the invoice date, fetched through a RateProvider.
IVA law (art. 79 LIVA) names the Banco de España selling rate at the accrual date; the
Banco de España derives its rate from the ECB reference rate, so the figures normally agree.
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional, Protocol

from .calc import D, round_cents
from .i18n import UserError

SOURCE_ECB = "ecb"
SOURCE_MANUAL = "manual"
EUR = "EUR"


class FxError(UserError):
    """A rate could not be obtained; the message is meant for the user."""


@dataclass(frozen=True)
class Rate:
    currency: str
    per_eur: Decimal  # units of `currency` per 1 EUR
    rate_date: date  # the day the rate was published for (earlier than requested on weekends/holidays)
    source: str = SOURCE_ECB


def convert_to_eur_cents(amount_cents: int, per_eur: object) -> int:
    """Euro cents for an amount in another currency: amount / rate, rounded half-up."""
    rate = D(per_eur)
    if not rate.is_finite() or rate <= 0:
        raise ValueError("exchange rate must be positive")
    return round_cents(D(amount_cents) / rate)


def parse_rate(text: object) -> Decimal:
    """A positive finite exchange rate from user text ('1,1805' allowed). Raises ValueError otherwise."""
    try:
        rate = Decimal(str(text).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(text) from exc
    if not rate.is_finite() or rate <= 0:
        raise ValueError(text)
    return rate


class RateProvider(Protocol):
    def fetch(self, currency: str, on: date) -> Rate:
        """The rate for `currency` as of `on`. Raises FxError if it cannot be obtained."""


class FrankfurterProvider:
    """ECB reference rates through the open Frankfurter service (api.frankfurter.dev).

    The ECB's own download hosts send an incomplete certificate chain, which Python's TLS
    verification (rightly) rejects, so the app does not talk to them directly. Frankfurter
    republishes the ECB reference rates and answers weekends/holidays with the previous
    business day, reporting that day's date.
    """

    BASE_URL = "https://api.frankfurter.dev/v1"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def fetch(self, currency: str, on: date) -> Rate:
        url = f"{self.BASE_URL}/{on.isoformat()}?base={EUR}&symbols={currency}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"), parse_float=Decimal)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 422):
                raise FxError("fx.err_unknown", currency=currency, date=on.isoformat()) from exc
            raise FxError("fx.err_network") from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
            raise FxError("fx.err_network") from exc
        try:
            value = Decimal(str(data["rates"][currency]))
            rate_date = date.fromisoformat(data["date"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise FxError("fx.err_unknown", currency=currency, date=on.isoformat()) from exc
        if value <= 0:
            raise FxError("fx.err_unknown", currency=currency, date=on.isoformat())
        return Rate(currency, value, rate_date, SOURCE_ECB)


class RateCache(Protocol):
    def get(self, currency: str, on: date) -> Optional[Rate]: ...
    def put(self, rate: Rate, on: date) -> None: ...


class RateService:
    """Looks a rate up in the local cache first, then asks the provider (if automatic rates are on)."""

    def __init__(self, cache: RateCache, provider: Optional[RateProvider]):
        self.cache = cache
        self.provider = provider

    def lookup(self, currency: str, on: date) -> Rate:
        if currency == EUR:
            return Rate(EUR, Decimal(1), on, SOURCE_ECB)
        cached = self.cache.get(currency, on)
        if cached:
            return cached
        if self.provider is None:
            raise FxError("fx.err_manual")
        rate = self.provider.fetch(currency, on)
        self.cache.put(rate, on)
        return rate
