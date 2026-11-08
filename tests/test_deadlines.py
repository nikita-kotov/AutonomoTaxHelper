from datetime import date

from taxhelper import deadlines, deadlines as dl


def test_weekend_rolls_to_monday():
    assert dl.roll_to_business_day(date(2027, 1, 30)) == date(2027, 2, 1)  # Saturday
    assert dl.roll_to_business_day(date(2026, 4, 19)) == date(2026, 4, 20)  # Sunday
    assert dl.roll_to_business_day(date(2026, 4, 20)) == date(2026, 4, 20)  # Monday


def test_quarter_due_dates_2026():
    due = {q: dl.quarter_filing("303", 2026, q).due for q in range(1, 5)}
    assert due[1] == date(2026, 4, 20)
    assert due[2] == date(2026, 7, 20)
    assert due[3] == date(2026, 10, 20)
    assert due[4] == date(2027, 2, 1)  # Jan 30 2027 is Saturday


def test_window_opens_day_after_quarter_end():
    assert dl.quarter_filing("130", 2026, 1).opens == date(2026, 4, 1)
    assert dl.quarter_filing("130", 2026, 4).opens == date(2027, 1, 1)


def test_default_set_for_autonomo():
    keys = [(t.model, t.period) for t in dl.default_filings(2026)]
    assert keys.count(("303", "1T")) == 1 and len(keys) == 10
    assert ("390", "ANUAL") in keys and ("100", "ANUAL") in keys


def test_display_status():
    opens, due = date(2026, 4, 1), date(2026, 4, 20)
    s = dl.display_status
    assert s("filed", opens, due, date(2026, 12, 1)) == "filed"
    assert s("not_required", opens, due, date(2026, 12, 1)) == "not_required"
    assert s("pending", opens, due, date(2026, 3, 20)) == "waiting"
    assert s("pending", opens, due, date(2026, 4, 2)) == "open"
    assert s("pending", opens, due, date(2026, 4, 10)) == "due_soon"
    assert s("pending", opens, due, date(2026, 4, 20)) == "due_soon"  # last day still allowed
    assert s("pending", opens, due, date(2026, 4, 21)) == "overdue"


def test_period_quarter_helper():
    f = deadlines.period_quarter
    assert [f(p) for p in ("1T", "4T", "5T", "0T", "ANUAL", "", None)] == [1, 4, None, None, None, None, None]
