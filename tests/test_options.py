import numpy as np
import pytest

from ppa import options


def test_bachelier_put_call_parity():
    f, k, sd = 12.0, 0.0, 30.0
    put = options.bachelier_put(f, k, sd)
    call = options.bachelier_put(-f, -k, sd)  # call on X is a put on -X
    assert call - put == pytest.approx(f - k)


def test_bachelier_put_matches_simulation():
    rng = np.random.default_rng(3)
    x = 8.0 + 25.0 * rng.standard_normal(2_000_000)
    assert options.bachelier_put(8.0, 0.0, 25.0) == pytest.approx(np.maximum(-x, 0).mean(), rel=5e-3)


def test_kirk_reduces_to_black_with_zero_strike():
    # Margrabe: exchange option with K = 0 is exact under Kirk.
    v = options.kirk(100.0, 90.0, 0.0, 0.3, 0.25, 0.6, 1.0)
    sd = np.sqrt(0.3**2 + 0.25**2 - 2 * 0.6 * 0.3 * 0.25)
    assert v == pytest.approx(options.black_call(100.0, 90.0, sd))


@pytest.fixture
def spark(cfg):
    return {**cfg["spark"], "vol": {"power": 0.60, "gas": 0.55, "carbon": 0.40},
            "corr": {"power_gas": 0.80, "power_carbon": 0.45, "gas_carbon": 0.35}}


def test_kirk_close_to_monte_carlo(spark):
    s, t = spark, 0.25
    mc, se = options.spark_mc(s, t, n=1_000_000)
    assert abs(options.spark_kirk(s, t) - mc) < max(4 * se, 0.01 * mc)


def test_spread_option_value_falls_with_correlation(spark):
    s, t = spark, 0.25
    values = [options.spark_kirk({**s, "corr": {**s["corr"], "power_gas": r}}, t) for r in (0.2, 0.5, 0.8)]
    assert values[0] > values[1] > values[2]


def test_swing_lsmc_is_bounded_by_intrinsic_and_foresight():
    rng = np.random.default_rng(5)
    spreads = 40 + 25 * rng.standard_normal((2000, 120))
    lsmc, foresight = options.swing_lsmc(spreads, rights=40)
    exercise_mean_top = np.sort(spreads.mean(0))[-40:].sum()  # fixed schedule on expected spreads
    assert exercise_mean_top <= lsmc * 1.01
    assert lsmc <= foresight


def test_swing_with_every_right_equals_sum_of_positive_parts():
    rng = np.random.default_rng(6)
    spreads = rng.standard_normal((500, 30))
    lsmc, foresight = options.swing_lsmc(spreads, rights=30)
    assert lsmc == pytest.approx(np.maximum(spreads, 0).sum(1).mean())
    assert foresight == pytest.approx(lsmc)


def test_correlations_that_cannot_hold_together_are_rejected(spark):
    assert options.consistent(spark["corr"])
    assert not options.consistent({"power_gas": 0.95, "power_carbon": 0.95, "gas_carbon": -0.5})
