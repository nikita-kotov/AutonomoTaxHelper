"""Shared fixtures: every test gets its own throw-away data folder and a freshly built app."""
from datetime import timedelta
from decimal import Decimal

import pytest

from taxhelper import i18n
from taxhelper.fx import FxError, Rate
from taxhelper.web import create_app


class FakeRates:
    """Offline exchange-rate provider with known values: USD 1.10 and GBP 0.85 per EUR.

    Weekends answer with the previous Friday's date (like the real service); anything else is unknown.
    """
    RATES = {"USD": Decimal("1.1000"), "GBP": Decimal("0.8500")}

    def __init__(self):
        self.requests = []

    def fetch(self, currency, on):
        self.requests.append((currency, on))
        if currency not in self.RATES:
            raise FxError("fx.err_unknown", currency=currency, date=on.isoformat())
        published = on - timedelta(days=max(on.weekday() - 4, 0))  # Sat/Sun -> Friday
        return Rate(currency, self.RATES[currency], published)


@pytest.fixture(autouse=True)
def never_touch_real_data(tmp_path, monkeypatch):
    """Every test works in its own empty data folder, so a test that forgets a fixture cannot read or change the
    developer's real database (the default data folder)."""
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path / "isolated-data"))


@pytest.fixture(autouse=True)
def english():
    """Every test starts and ends in English, whatever language an earlier test selected."""
    i18n.set_language("en")
    yield
    i18n.set_language("en")


@pytest.fixture()
def rates():
    return FakeRates()


@pytest.fixture()
def fresh_app(tmp_path, monkeypatch, rates):
    """A Flask app on an empty data folder, with offline exchange rates."""
    monkeypatch.setenv("TAXHELPER_DATA", str(tmp_path))
    app = create_app(fx_provider=rates)
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(fresh_app):
    """Test client; the language follows the (absent) browser header, i.e. English."""
    return fresh_app.test_client()
