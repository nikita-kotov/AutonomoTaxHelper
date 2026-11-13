"""The filings tracker: custom filings, deleted standard filings, restoring them, redirects."""

from datetime import date

import pytest

from taxhelper import db
from tests.support import calendar_from_last_year, set_settings, text
from tests.test_profile import form


@pytest.mark.parametrize("period", ["5T", "0T", "9T", "T", "1t"])
def test_custom_filings_with_odd_periods_do_not_break_pages(client, period):
    r = client.post("/filings/new", data={"fyear": "2026", "model": "303", "period": period, "due_date": "2026-12-01"},
                    follow_redirects=True)
    assert r.status_code == 200
    assert client.get("/filings").status_code == 200 and client.get("/").status_code == 200


def test_filing_year_is_validated(client):
    r = client.post("/filings/new", data={"fyear": "99999", "model": "303", "period": "1T", "due_date": "2026-12-01"},
                    follow_redirects=True)
    assert "Fill in the year, model and due date." in text(r)


def filing_keys():
    con = db.connect()
    try:
        return {(r["year"], r["model"], r["period"]) for r in con.execute("SELECT * FROM filings")}
    finally:
        con.close()


def test_deleted_default_filing_is_not_recreated(client):
    calendar_from_last_year()
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE year=? AND model='303' AND period='1T'", (year,)).fetchone()["id"]
    con.close()
    client.post(f"/filings/{fid}/delete")
    for url in ("/filings", "/", "/filings"):
        client.get(url)
    assert (year, "303", "1T") not in filing_keys()
    client.post("/settings", data=form())  # settings sync must not bring it back either
    assert (year, "303", "1T") not in filing_keys() and (year, "303", "2T") in filing_keys()
    # adding the same filing by hand works and forgets the deletion
    client.post("/filings/new", data={"fyear": str(year), "model": "303", "period": "1T", "due_date": f"{year}-04-20"})
    assert (year, "303", "1T") in filing_keys()
    con = db.connect()
    assert con.execute("SELECT COUNT(*) FROM filing_skips").fetchone()[0] == 0
    con.close()


def test_deleted_standard_filings_can_be_restored(client):
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    fid = con.execute("SELECT id FROM filings WHERE year=? AND model='303' AND period='4T'", (year,)).fetchone()[0]
    con.close()
    client.post(f"/filings/{fid}/delete")
    client.get("/filings")
    assert (year, "303", "4T") not in filing_keys()
    r = client.post("/filings/restore", follow_redirects=True)
    assert "Standard filings restored." in text(r) and (year, "303", "4T") in filing_keys()


def test_restoring_follows_the_tax_profile(client):
    client.get("/filings")
    year = date.today().year
    con = db.connect()
    ids = {m: con.execute("SELECT id FROM filings WHERE year=? AND model=? AND period='4T'", (year, m)).fetchone()[0]
           for m in ("303", "130")}
    con.close()
    for filing_id in ids.values():
        client.post(f"/filings/{filing_id}/delete")
    set_settings(tracked_models=("130", "100"))  # the 303 is no longer part of the profile
    client.post("/filings/restore")
    keys = filing_keys()
    assert (year, "130", "4T") in keys and (year, "303", "4T") not in keys


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example/x", "http://evil.example", "javascript:alert(1)", ""])
def test_filing_mark_ignores_foreign_redirect_targets(client, target):
    client.get("/filings")
    con = db.connect()
    fid = con.execute("SELECT id FROM filings LIMIT 1").fetchone()["id"]
    con.close()
    r = client.post(f"/filings/{fid}/mark", data={"action": "filed", "next": target})
    assert r.status_code == 302 and r.headers["Location"].endswith("/filings")


def test_filing_mark_keeps_local_redirect_targets(client):
    client.get("/filings")
    con = db.connect()
    fid = con.execute("SELECT id FROM filings LIMIT 1").fetchone()["id"]
    con.close()
    r = client.post(f"/filings/{fid}/mark", data={"action": "filed", "next": "/?year=2026"})
    assert r.headers["Location"].endswith("/?year=2026")


# ---------------------------------------------------------------- a new installation has nothing overdue

def new_installation_filings():
    from tests.test_profile import filings
    return filings()


def test_a_new_installation_is_not_told_that_past_periods_are_overdue(client):
    assert "Overdue filings" not in text(client.get("/"))
    today = date.today().isoformat()
    rows = new_installation_filings()
    assert rows and all(r["due_date"] >= today for r in rows.values())  # nothing that is already past due
    assert not any(y < date.today().year for (y, _m, _p) in rows)  # and nothing for earlier years


def test_without_an_activity_start_the_first_entry_starts_the_calendar(client):
    from tests.support import expense_form
    year = date.today().year - 1
    client.post("/entries/new/expense", data={**expense_form(), "date": f"{year}-05-10"})
    client.get("/filings")
    left = new_installation_filings()
    assert (year, "303", "1T") not in left  # that quarter ended before the first entry
    assert (year, "303", "2T") in left and (year, "303", "4T") in left and (date.today().year, "303", "1T") in left


def test_the_activity_start_wins_over_the_first_entry(client):
    from tests.support import expense_form
    year = date.today().year
    client.post("/entries/new/expense", data={**expense_form(), "date": f"{year - 1}-05-10"})
    set_settings(activity_start=date(year, 1, 1))
    client.get("/filings")
    assert not any(y < year for (y, _m, _p) in new_installation_filings())


def test_untouched_periods_created_before_the_start_was_known_disappear_but_filed_ones_stay(client):
    from tests.test_profile import filings
    calendar_from_last_year()
    client.get("/filings")  # last year's calendar, as older versions created it for everybody
    year = date.today().year - 1
    con = db.connect()
    con.execute("UPDATE filings SET status = 'filed', filed_date = due_date WHERE year = ? AND model = '303' AND period = '2T'", (year,))
    con.commit()
    con.close()
    set_settings(activity_start=None)
    client.post("/settings", data=form(activity_start=""))  # saving the profile re-applies the calendar
    left = filings()
    assert (year, "303", "2T") in left and left[(year, "303", "2T")]["status"] == "filed"
    assert (year, "303", "1T") not in left and (year, "130", "3T") not in left
