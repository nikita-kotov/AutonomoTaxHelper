"""Dates typed by the user: four-digit ISO years in range, in the parser and in every form."""

import pytest

from taxhelper import db
from tests.support import text


@pytest.mark.parametrize("bad", ["12345-01-23", "0026-01-23", "2101-01-01", "1999-12-31", "20260123", "2026-1-3",
                                 "2026-02-30", "", None, " "])
def test_typed_dates_must_be_four_digit_iso_years_in_range(bad):
    from taxhelper.dates import parse_date
    with pytest.raises(ValueError):
        parse_date(bad)


def test_a_good_date_is_accepted_with_surrounding_spaces():
    from datetime import date
    from taxhelper.dates import parse_date
    assert parse_date(" 2026-01-23 ") == date(2026, 1, 23)


def test_the_activity_start_refuses_a_five_digit_year(client):
    from tests.support import load_settings
    from tests.test_profile import form
    page = text(client.post("/settings", data=form(activity_start="12345-01-23")))
    assert "Invalid activity start date" in page
    assert load_settings().activity_start is None
    client.post("/settings", data=form(activity_start="2026-01-23"))
    assert str(load_settings().activity_start) == "2026-01-23"
    client.post("/settings", data=form(activity_start="0026-01-23"))
    assert str(load_settings().activity_start) == "2026-01-23"  # the earlier good value stays


def test_holidays_and_filing_dates_refuse_impossible_years(client):
    from tests.support import load_settings
    from tests.test_profile import form
    client.post("/settings", data=form(extra_holidays="12345-01-01"))
    assert load_settings().extra_holidays == ()
    client.post("/filings/new", data={"fyear": "2026", "model": "349", "period": "1T", "due_date": "12345-04-20"})
    con = db.connect()
    try:
        assert con.execute("SELECT COUNT(*) FROM filings WHERE model = '349'").fetchone()[0] == 0
    finally:
        con.close()
    page = text(client.post("/filings/bulk-filed", data={"upto": "20260101"}, follow_redirects=True))
    assert "date" in page.lower()


def test_every_date_field_limits_the_browser_to_four_digit_years(client):
    for path in ("/settings", "/entries/new/expense", "/filings"):
        page = text(client.get(path))
        fields = [chunk.split(">")[0] for chunk in page.split('type="date"')[1:]]
        assert fields and all('min="2000-01-01"' in f and 'max="2100-12-31"' in f for f in fields), path
