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
                        100, vol=lambda tau: np.full_like(tau, 0.3), model_sd=0.05, paths=300, seed=2)


def test_flat_vol_gives_the_lognormal_level(curve):
    years, valuation = [2027, 2030, 2036], dt.date(2026, 10, 30)
    flat = lambda t: np.full_like(t, 0.2)  # noqa: E731
    level, tau = simulate.level_shocks(np.random.default_rng(0), years, valuation, flat, 20000)
    assert level.mean(axis=0) == pytest.approx(1.0)
    assert np.log(level).std(axis=0) == pytest.approx(0.2 * np.sqrt(tau), rel=0.03)
    assert np.corrcoef(np.log(level).T)[0, 2] == pytest.approx(np.sqrt(tau[0] / tau[2]), abs=0.03)  # one Brownian path


def test_far_years_move_less_while_they_are_far():
    """Vol 30% in the last year before delivery and 10% before that: a far year collects mostly the low vol."""
    years, valuation = [2027, 2036], dt.date(2026, 10, 30)
    vol = lambda t: np.where(t < 1, 0.3, 0.1)  # noqa: E731
    level, tau = simulate.level_shocks(np.random.default_rng(0), years, valuation, vol, 20000)
    far = np.sqrt(0.1**2 * (tau[1] - 1.5) + 0.3**2 * 1.5)  # the final year and the half-year in delivery at 30%
    assert np.log(level[:, 1]).std() == pytest.approx(far, rel=0.03)
    assert np.log(level[:, 1]).std() < 0.3 * np.sqrt(tau[1]) / 1.5


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
    v = valuation.value(sims, df, np.array([-1.0, -2.0]), np.array([0.5, 1.0]), balancing=1.5, quantile=0.1)
    assert sum(v["steps"].values()) == pytest.approx(v["price"], abs=0.02)
    assert v["steps"]["risk"] <= 0 and v["steps"]["balancing"] == -1.5


def test_risk_premium_breaks_even_at_p90(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), np.zeros(2), balancing=0.0, quantile=0.1)
    assert abs(v["risk"]["p10_hedged"]) < 0.01  # €m


def test_hedge_reduces_risk(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), np.zeros(2), balancing=0.0, quantile=0.1)
    assert v["risk"]["sd_hedged"] < 0.5 * v["risk"]["sd_open"]
    assert all(h["mw"] > 0 for h in v["hedge"])


def test_each_risk_alone_stays_within_the_hedged_risk(sims):
    """Shape under its own hedge, volume at the forward curve and the two capture errors are each part of what
    the deal's hedge leaves; the rest is hourly price and volume moving together."""
    v = valuation.value(sims, np.array([0.97, 0.94]), np.array([-1.0, -2.0]), np.array([0.5, 1.0]),
                        balancing=0.0, quantile=0.1, year_sd=np.array([1.0, 1.0]), rho=0.3)
    r = v["risk"]
    parts = [r["sd_shape"], r["sd_slope"], r["sd_year"], r["sd_volume"]]
    assert all(0 < p <= 1.05 * r["sd_hedged"] for p in parts)
    assert np.sqrt(np.sum(np.square(parts))) <= 1.05 * r["sd_hedged"]


def test_negative_price_clause_pays_more_per_mwh_on_less_volume(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), np.zeros(2), balancing=0.0, quantile=0.1)
    assert v["negative"]["price"] >= v["price"]
    assert 0 <= v["negative"]["volume_share"] < 1
    assert v["negative"]["put_mc"] >= 0


def test_fewer_paid_hours_shrink_the_paid_volume_and_not_the_all_hours_deal(sims):
    df, adj = np.array([0.97, 0.94]), np.array([-5.0, -10.0])
    today = valuation.value(sims, df, adj, np.zeros(2), balancing=0.0, quantile=0.1)
    later = valuation.value(sims, df, adj, np.zeros(2), balancing=0.0, quantile=0.1,
                            paid_adj=np.array([-2.0, -4.0]), paid=np.array([0.95, 0.9]))
    assert later["price"] == today["price"] and later["hedge"] == today["hedge"]
    assert later["negative"]["volume_share"] > today["negative"]["volume_share"]
    assert later["negative"]["volume_share_last"] > later["negative"]["volume_share"]
    assert later["negative"]["put_mc"] >= today["negative"]["put_mc"]
    assert later["negative"]["price"] > today["negative"]["price"]  # less of the capture drop is in the paid price


def test_hedge_shrinks_as_cannibalisation_takes_a_share_of_the_price(sims):
    df = np.array([0.97, 0.94])
    none = valuation.value(sims, df, np.zeros(2), np.zeros(2), balancing=0.0, quantile=0.1)
    some = valuation.value(sims, df, np.array([-10.0, -20.0]), np.zeros(2), balancing=0.0, quantile=0.1)
    for a, b, s, adj in zip(none["hedge"], some["hedge"], sims, [-10.0, -20.0], strict=True):
        capture_price = s.revenue.mean() / s.volume.mean()
        assert b["mw"] == pytest.approx(a["mw"] * (1 + adj / capture_price), rel=0.05)


def test_slope_error_raises_the_premium_and_not_the_expected_price(sims):
    df, adj = np.array([0.97, 0.94]), np.array([-5.0, -10.0])
    sure = valuation.value(sims, df, adj, np.zeros(2), balancing=0.0, quantile=0.1)
    unsure = valuation.value(sims, df, adj, np.array([4.0, 8.0]), balancing=0.0, quantile=0.1)
    assert unsure["steps"]["cannibalisation"] == pytest.approx(sure["steps"]["cannibalisation"], abs=0.006)
    assert unsure["steps"]["risk"] < sure["steps"]["risk"] - 0.5
    assert [h["mw"] for h in unsure["hedge"]] == pytest.approx([h["mw"] for h in sure["hedge"]], rel=0.02)


def test_slope_error_is_shared_by_every_year_and_orthogonal_to_the_level(sims):
    z = sims[0].slope_error
    assert z.mean() == pytest.approx(0, abs=1e-9) and z.std() == pytest.approx(1)
    for s in sims:
        assert s.slope_error is z
        assert (z * s.level).mean() == pytest.approx(0, abs=1e-9)
        assert (z * s.level * s.volume).mean() / s.volume.mean() == pytest.approx(0, abs=1e-9)


def test_year_to_year_capture_error_raises_the_premium_and_not_the_expected_price(sims):
    df, adj = np.array([0.97, 0.94]), np.array([-5.0, -10.0])
    sure = valuation.value(sims, df, adj, np.zeros(2), balancing=0.0, quantile=0.1)
    unsure = valuation.value(sims, df, adj, np.zeros(2), balancing=0.0, quantile=0.1, year_sd=np.array([6.0, 6.0]))
    assert unsure["steps"]["cannibalisation"] == pytest.approx(sure["steps"]["cannibalisation"], abs=0.006)
    assert unsure["steps"]["risk"] < sure["steps"]["risk"] - 0.5
    assert unsure["risk"]["sd_year"] > 0 == sure["risk"]["sd_year"]
    assert [h["mw"] for h in unsure["hedge"]] == pytest.approx([h["mw"] for h in sure["hedge"]], rel=0.02)


def test_capture_noise_is_new_each_year_and_carries_over_by_rho(sims):
    for s in sims:
        z = s.capture_noise
        assert z.mean() == pytest.approx(0, abs=1e-9) and z.std() == pytest.approx(1)
        for other in sims:
            assert (z * other.level * other.volume).mean() / other.volume.mean() == pytest.approx(0, abs=1e-9)
    assert abs(np.corrcoef(sims[0].capture_noise, sims[1].capture_noise)[0, 1]) < 0.15
    e = valuation.year_effects(sims, 0.6)
    assert e.std(axis=0) == pytest.approx(1, abs=0.1)
    assert np.corrcoef(e.T)[0, 1] == pytest.approx(0.6, abs=0.12)
    assert valuation.year_effects(sims, 0.0)[:, 1] == pytest.approx(sims[1].capture_noise)


def test_premium_rises_with_confidence_and_the_headline_is_on_the_ladder(sims):
    v = valuation.value(sims, np.array([0.97, 0.94]), np.zeros(2), np.array([2.0, 4.0]), balancing=0.0, quantile=0.1)
    p75, p90, p95 = v["premiums"]
    assert [p["confidence"] for p in v["premiums"]] == [75, 90, 95]
    assert 0 <= p75["premium"] < p90["premium"] < p95["premium"]
    assert p90["premium"] == -v["steps"]["risk"] and p90["price"] == v["price"]
    assert p75["price"] == pytest.approx(v["price"] + p90["premium"] - p75["premium"], abs=0.006)


TERMS = {"liquid_years": 1, "bid_offer": {"liquid": 0.25, "illiquid": 1.5},
         "margin": {"confidence": 0.99, "holding_days": 2, "funding_spread": 0.015}}
FLAT = lambda t: np.full_like(t, 0.3)  # noqa: E731


def test_hedge_cost_is_the_spread_plus_margin_funding(sims):
    cost = valuation.hedge_cost(sims, dt.date(2026, 10, 30), FLAT, TERMS)
    free = valuation.hedge_cost(sims, dt.date(2026, 10, 30), FLAT, {**TERMS, "margin": {**TERMS["margin"],
                                                                                         "funding_spread": 0.0}})
    assert free == pytest.approx([0.25, 1.5])  # Cal-27 is liquid, Cal-28 is not
    cover = 2.326 * np.sqrt(2 / 252)  # the 99% move over two days, per unit of vol
    for c, f, s in zip(cost, free, sims, strict=True):  # posted to mid-delivery, in monthly steps
        assert c - f == pytest.approx(0.015 * cover * 0.3 * s.tau * s.base, rel=1e-3)


def test_terms_are_steps_of_the_price_and_leave_the_model_deal_alone(sims):
    df, adj, sd = np.array([0.97, 0.94]), np.array([-1.0, -2.0]), np.array([0.5, 1.0])
    cost = np.array([0.25, 1.5])
    bare = valuation.value(sims, df, adj, sd, balancing=1.5, quantile=0.1)
    full = valuation.value(sims, df, adj, sd, balancing=1.5, quantile=0.1, cost=cost,
                           credit={"hazard": 0.02, "recovery": 0.4}, origin=1.0)
    assert sum(full["steps"].values()) == pytest.approx(full["price"], abs=1e-9)
    assert bare["steps"]["hedging"] == bare["steps"]["credit"] == bare["steps"]["origin"] == 0
    assert full["steps"]["hedging"] < 0 and full["steps"]["credit"] < 0 and full["steps"]["origin"] == 1.0
    for k in ["base", "profile", "correlation", "cannibalisation", "risk", "balancing"]:
        assert full["steps"][k] == bare["steps"][k]
    assert full["hedge"] == bare["hedge"] and abs(full["risk"]["p10_hedged"]) < 0.01
    volume = sum(d * s.volume.mean() for d, s in zip(df, sims, strict=True))
    paid = sum(d * h["mw"] * s.hours * c for d, h, s, c in zip(df, full["hedge"], sims, cost, strict=True))
    assert -full["steps"]["hedging"] == pytest.approx(paid / volume, abs=0.01)
    assert full["negative"]["price"] != bare["negative"]["price"]


def test_credit_loss_grows_with_default_and_shrinks_with_recovery(sims):
    df = np.array([0.97, 0.94])
    level = np.column_stack([s.level for s in sims])
    deal = valuation._deal(sims, df, np.zeros_like(level), 0.0, clause=False)
    k = deal.expected_price() - 5.0  # bought below value: the contract is worth something to lose
    loss = valuation.credit_loss(deal, k, level, hazard=0.02, recovery=0.4)
    assert loss > 0 and valuation.credit_loss(deal, k, level, hazard=0.0, recovery=0.4) == 0
    assert valuation.credit_loss(deal, k, level, hazard=0.04, recovery=0.4) > loss
    assert valuation.credit_loss(deal, k, level, hazard=0.02, recovery=0.7) == pytest.approx(loss / 2)
    first = 0.02 * 0.6 * deal.pnl(k, None).mean()  # before the first year the whole expected value is at stake
    assert first < loss < 3 * first
    assert valuation.credit_loss(deal, deal.expected_price() + 200.0, level, hazard=0.02, recovery=0.4) == 0


def test_degradation_takes_the_same_share_of_output_each_year(curve, shape, synthetic_prices, window):
    idx = hours(window[0], window[1] + dt.timedelta(days=1))
    cf = pd.Series(0.2, index=idx)
    pool = simulate.pool(synthetic_prices, cf, window)
    run = lambda d: simulate.run(curve, shape, pool, [2027, 2028, 2029], np.full(5, 1000.0), dt.date(2026, 10, 30),  # noqa: E731
                                 100, vol=FLAT, model_sd=0.0, paths=50, seed=2, degradation=d)
    new, aged = run(0.0), run(0.01)
    for j, (a, b) in enumerate(zip(new, aged, strict=True)):
        assert b.volume == pytest.approx(a.volume * 0.99**j)
        assert (b.level == a.level).all() and b.spot == pytest.approx(a.spot)  # prices are as they were
