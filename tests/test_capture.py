import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ppa import capture

WINDOW = (dt.date(2023, 10, 1), dt.date(2026, 9, 30))


@pytest.fixture(scope="module")
def monthly():
    """Three years of months: capture falls and the unpaid share rises with log penetration, plus noise."""
    idx = pd.period_range("2023-10", "2026-09", freq="M")
    rng = np.random.default_rng(0)
    season = 0.15 + 0.1 * np.clip(np.sin(np.pi * (idx.month.to_numpy() - 3) / 6), 0, None)
    share = season * np.exp(0.15 * np.arange(len(idx)) / 12 + rng.normal(0, 0.15, len(idx)))
    return pd.DataFrame({
        "solar_share": share,
        "solar_rate": 0.4 - 0.2 * np.log(share) + rng.normal(0, 0.02, len(idx)),
        "solar_rate_paid": 0.6 - 0.1 * np.log(share) + rng.normal(0, 0.02, len(idx)),
        "solar_unpaid": 0.5 + 0.15 * np.log(share) + rng.normal(0, 0.01, len(idx)),
    }, index=idx)


def test_fit_recovers_the_slope_within_its_standard_error(monthly):
    for target, slope in [("rate", -0.2), ("rate_paid", -0.1), ("unpaid", 0.15)]:
        m = capture.fit(monthly, "solar", target=target)
        assert 0 < m.se < 0.05
        assert abs(m.beta - slope) < 3 * m.se


def test_lookback_fits_only_the_last_months(monthly):
    """The slope's sensitivity to its window refits on the trailing months alone."""
    short = capture.fit(monthly, "solar", lookback=24)
    assert short.beta == pytest.approx(capture.fit(monthly.iloc[-24:], "solar").beta)
    assert short.se > capture.fit(monthly, "solar").se  # fewer months pin the slope down less


def test_paid_volume_shrinks_with_the_build_out(monthly):
    today = monthly["solar_share"].groupby(monthly.index.month).mean()
    future = pd.DataFrame({2027: today * 1.2, 2036: today * 2.0})
    unpaid = capture.fit(monthly, "solar", target="unpaid")
    paid = capture.paid_volume(unpaid, monthly, "solar", WINDOW, today, future)
    assert (paid[2036] < paid[2027]).all() and (paid[2027] < 1).all() and (paid > 0).all().all()
    flat = capture.paid_volume(capture.Model(unpaid.alpha, 0.0), monthly, "solar", WINDOW, today, future)
    assert flat.to_numpy() == pytest.approx(1.0)


def test_slope_error_is_one_standard_error_of_the_change(monthly):
    today = monthly["solar_share"].groupby(monthly.index.month).mean()
    future = pd.DataFrame({2036: today * 2.0})
    m = capture.fit(monthly, "solar")
    assert capture.slope_error(m, today, future)[2036].to_numpy() == pytest.approx(m.se * np.log(2.0))
    assert capture.cannibalisation(m, today, future)[2036].to_numpy() == pytest.approx(m.beta * np.log(2.0))
