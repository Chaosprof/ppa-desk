import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ppa import simulate, valuation
from ppa.hours import hours


@pytest.fixture(scope="module")
def sims(curve, shape, synthetic_prices, window):
    idx = hours(window[0], window[1] + dt.timedelta(days=1))
    hour = idx.tz_convert("Europe/Berlin").hour.to_numpy()
    rng = np.random.default_rng(1)
    sun = np.clip(np.sin(np.pi * (hour - 6) / 14), 0, None) * rng.uniform(0.2, 1, len(idx))
    cf = pd.Series(0.8 * sun, index=idx)
    pool = simulate.pool(synthetic_prices, cf, window)
    return simulate.run(curve, shape, pool, [2027, 2028], np.full(20, 1000.0), dt.date(2026, 10, 30),
                        100, forward_vol=0.3, model_sd=0.05, paths=300, seed=2)


def test_analogue_days_are_drawn_with_equal_weight(synthetic_prices, window):
    pool_days = pd.date_range(*window, freq="D")  # touches four calendar years
    future = pd.date_range("2027-01-01", "2027-12-31", freq="D")
    analog = simulate._analogues(future, pool_days)
    for row in analog:
        counts = np.unique(row, return_counts=True)[1]
        assert counts.min() == counts.max()


def test_simulated_prices_are_unbiased_against_the_curve(sims):
    for s in sims:
        mean_base = s.spot.mean() / s.hours
        se = s.spot.std() / s.hours / np.sqrt(len(s.spot))
        assert abs(mean_base - s.base) < 4 * se + 0.5


def test_volume_matches_the_full_load_hours_target(sims):
    for s in sims:
        assert s.volume.mean() / 100 == pytest.approx(1000 * s.hours / 8760, rel=0.02)


def test_solar_captures_less_than_baseload(sims):
    for s in sims:
        assert s.profile_value / s.volume.mean() < s.base


def test_decomposition_adds_up(sims):
    df = np.array([0.97, 0.94])
    v = valuation.value(sims, df, np.array([-1.0, -2.0]), balancing=1.5, quantile=0.1)
    assert sum(v["steps"].values()) == pytest.approx(v["price"], abs=0.02)
    assert v["steps"]["risk"] <= 0 and v["steps"]["balancing"] == -1.5


def test_risk_premium_breaks_even_at_p90(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), balancing=0.0, quantile=0.1)
    assert abs(v["risk"]["p10_hedged"]) < 0.01  # €m


def test_hedge_reduces_risk(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), balancing=0.0, quantile=0.1)
    assert v["risk"]["sd_hedged"] < 0.5 * v["risk"]["sd_open"]
    assert all(h["mw"] > 0 for h in v["hedge"])


def test_negative_price_clause_is_worth_at_least_the_put_strip(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), balancing=0.0, quantile=0.1)
    assert v["steps"]["clause"] >= 0
    assert v["negative"]["put_mc"] >= 0
