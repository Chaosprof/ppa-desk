"""Delivery calendar: hours, day types, peak hours and contract periods for the German market."""

from __future__ import annotations

import datetime as dt
import re
from functools import cache

import holidays
import numpy as np
import pandas as pd

TZ = "Europe/Berlin"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

WEEKDAY, SATURDAY, SUNDAY = 0, 1, 2


def hours(start: dt.date, end: dt.date) -> pd.DatetimeIndex:
    """UTC hour starts for local delivery days in [start, end). DST days have 23 or 25 hours."""
    lo = pd.Timestamp(start).tz_localize(TZ).tz_convert("UTC")
    hi = pd.Timestamp(end).tz_localize(TZ).tz_convert("UTC")
    return pd.date_range(lo, hi, freq="h", inclusive="left")


@cache
def _holidays(year: int) -> frozenset[dt.date]:
    return frozenset(holidays.Germany(years=year).keys())


def features(idx: pd.DatetimeIndex) -> pd.DataFrame:
    """Local calendar features per hour: year, month, hour, day type, peak flag, local date."""
    local = idx.tz_convert(TZ)
    date = local.date
    years = np.unique(local.year)
    hol = set().union(*(_holidays(int(y)) for y in years))
    weekday = local.day_of_week
    daytype = np.where(weekday == 5, SATURDAY, WEEKDAY)
    daytype = np.where((weekday == 6) | np.isin(date, list(hol)), SUNDAY, daytype)
    return pd.DataFrame(
        {
            "year": local.year,
            "month": local.month,
            "hour": local.hour,
            "daytype": daytype,
            # Phelix DE peak: Monday–Friday 08:00–20:00 local, holidays included.
            "peak": (weekday < 5) & (local.hour >= 8) & (local.hour < 20),
            "date": date,
        },
        index=idx,
    )


def delivery(contract: str) -> tuple[dt.date, dt.date]:
    """Parse 'Dec-26', 'Q1-27' or 'Cal-27' into a local [start, end) date range."""
    m = re.fullmatch(r"(Cal|Q[1-4]|[A-Z][a-z]{2})-(\d{2})", contract)
    if not m:
        raise ValueError(f"unrecognised contract {contract!r}")
    kind, year = m.group(1), 2000 + int(m.group(2))
    if kind == "Cal":
        return dt.date(year, 1, 1), dt.date(year + 1, 1, 1)
    if kind.startswith("Q"):
        first = 3 * (int(kind[1]) - 1) + 1
        return dt.date(year, first, 1), _add_months(dt.date(year, first, 1), 3)
    first = dt.date(year, MONTHS.index(kind) + 1, 1)
    return first, _add_months(first, 1)


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    return dt.date(d.year + m // 12, m % 12 + 1, 1)
