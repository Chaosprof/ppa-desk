import datetime as dt

import pytest

from ppa.hours import SATURDAY, SUNDAY, WEEKDAY, delivery, features, hours


def test_dst_days_have_23_and_25_hours():
    assert len(hours(dt.date(2027, 3, 28), dt.date(2027, 3, 29))) == 23
    assert len(hours(dt.date(2027, 10, 31), dt.date(2027, 11, 1))) == 25
    assert len(hours(dt.date(2027, 1, 1), dt.date(2028, 1, 1))) == 8760
    assert len(hours(dt.date(2028, 1, 1), dt.date(2029, 1, 1))) == 8784


@pytest.mark.parametrize("contract, start, end", [
    ("Dec-26", dt.date(2026, 12, 1), dt.date(2027, 1, 1)),
    ("Q1-27", dt.date(2027, 1, 1), dt.date(2027, 4, 1)),
    ("Q4-27", dt.date(2027, 10, 1), dt.date(2028, 1, 1)),
    ("Cal-30", dt.date(2030, 1, 1), dt.date(2031, 1, 1)),
])
def test_delivery_periods(contract, start, end):
    assert delivery(contract) == (start, end)


def test_unknown_contract_is_rejected():
    with pytest.raises(ValueError):
        delivery("Summer-27")


def test_holidays_count_as_sundays():
    f = features(hours(dt.date(2027, 12, 24), dt.date(2027, 12, 27)))
    by_day = f.groupby("date")["daytype"].first()
    assert by_day[dt.date(2027, 12, 24)] == WEEKDAY  # Christmas Eve is not a federal holiday
    assert by_day[dt.date(2027, 12, 25)] == SUNDAY  # Christmas Day, a Saturday
    assert by_day[dt.date(2027, 12, 26)] == SUNDAY
    f = features(hours(dt.date(2027, 1, 2), dt.date(2027, 1, 3)))
    assert (f["daytype"] == SATURDAY).all()
