import io

import numpy as np
import pandas as pd
import pytest

from ppa import fundamental as fm
from ppa import sources

CFG = {
    "lignite_fuel": 5.0, "emissions": {"lignite": 0.40, "coal": 0.34, "gas": 0.20, "oil": 0.27},
    "vom": {"lignite": 4.0, "coal": 4.0, "gas": 2.0, "oil": 3.0},
    "pumped_hours": 6.0, "pumped_efficiency": 0.75,
    "demand_response": {"mw": 5000.0, "price": 500.0, "spread": 150.0},
    "surplus_response": {"mw": 5000.0, "price": -150.0, "spread": 50.0},
}


def hours_frame(n: int, rng: np.random.Generator) -> pd.DataFrame:
    """Synthetic hours with a realistic fleet and fuel prices; demand varies around the thermal range."""
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    x = pd.DataFrame(index=idx)
    x["demand"] = 55000 + 10000 * np.sin(np.arange(n) / 24 * 2 * np.pi) + rng.normal(0, 3000, n)
    x["must_take"] = 8000.0
    variable = rng.uniform(5000, 40000, n)
    x["wind_fc"], x["solar_fc"] = variable * 0.6, variable * 0.4
    x["abroad"] = rng.normal(20000, 5000, n)
    for f, cap in {"lignite": 15000, "coal": 15000, "gas": 35000, "oil": 4000}.items():
        x[f"cap_{f}"] = float(cap)
    x[["fuel_lignite", "fuel_coal", "fuel_gas", "fuel_oil"]] = [5.0, 15.0, 35.0, 45.0]
    x["co2"] = 70.0
    x["month"] = idx.month
    x["day"] = np.arange(n) // 24
    return x


@pytest.fixture(scope="module")
def market():
    rng = np.random.default_rng(3)
    x = hours_frame(24 * 20, rng)
    eff = {f: np.linspace(0.33, 0.45, fm.BINS) if f != "gas" else np.linspace(0.35, 0.6, fm.BINS) for f in fm.FUELS}
    avail = {f: np.full(12, 0.85) for f in fm.FUELS}
    must_run = {f: np.full(12, 0.1) for f in fm.FUELS}
    trade = np.r_[0.0, 0.0, -0.1, -0.1, -0.05, 3000.0, 2000.0, 0.4]
    fl = fm.Fleet(eff, avail, must_run, trade, (-15000.0, 15000.0), 6000.0, {"wind": 1.0, "solar": 0.95})
    mk = fm.Market(x, fl, fm.X0, CFG)
    fm.solve(mk, x, fl, CFG, np.zeros(len(x)))  # commits plants for each day
    return mk, x, fl


def test_clearing_meets_demand(market):
    mk, x, _ = market
    p = mk.clear(np.zeros(len(x)))
    inside = (p > fm.FLOOR + 1) & (p < fm.CAP - 1)
    assert inside.all()
    assert np.abs(mk.supply(p) - x["demand"].to_numpy()).max() < 1.0  # MW


def test_supply_rises_with_price(market):
    mk, x, _ = market
    grid = np.linspace(-100, 400, 60)
    s = np.array([mk.supply(np.full(len(x), g)) for g in grid])
    assert (np.diff(s, axis=0) >= -1e-6).all()


def test_more_demand_never_lowers_the_price(market):
    mk, x, _ = market
    base = mk.clear(np.zeros(len(x)))
    tighter = mk.clear(np.full(len(x), -2000.0))  # storage charging adds 2 GW of demand
    assert (tighter >= base - 1e-6).all()


def test_wind_and_solar_curtail_only_below_zero_and_never_beyond_the_forecast(market):
    mk, x, fl = market
    for p in [5.0, 50.0, 500.0]:
        d = mk.dispatch(np.full(len(x), p))
        assert np.allclose(d["wind"], x["wind_fc"] * fl.level["wind"], rtol=1e-3)
        assert np.allclose(d["solar"], x["solar_fc"] * fl.level["solar"], rtol=1e-3)
    grid = np.linspace(-490, 20, 60)
    out = np.array([mk.dispatch(np.full(len(x), g))["wind"] for g in grid])
    assert (out >= 0).all() and (np.diff(out, axis=0) >= -1e-6).all()


def test_the_surplus_backstop_keeps_a_surplus_off_the_floor(market):
    """A surplus 4 GW beyond what curtailment and exports absorb hits the floor without the backstop, not with it."""
    mk, x, fl = market
    bare = fm.Market(x, fl, fm.X0, {**CFG, "surplus_response": {**CFG["surplus_response"], "mw": 0.0}})
    storage = x["demand"].to_numpy() - (bare.supply(np.full(len(x), fm.FLOOR)) - 4000.0)
    assert (bare.clear(storage) < fm.FLOOR + 1).all()
    assert (mk.clear(storage) > fm.FLOOR + 1).all()


def test_committed_output_follows_the_day_and_stays_within_the_fleet(market):
    mk, x, fl = market
    mk = fm.Market(x, fl, fm.X0, CFG)
    p = mk.clear(np.zeros(len(x)))
    mk.commit(p, damping=1.0)
    merit = mk.merit(p)
    for f in fm.FUELS:
        by_day = pd.Series(mk.committed[f]).groupby(x["day"].to_numpy())
        assert (by_day.nunique() == 1).all()
        assert (mk.committed[f] + (1 - mk.share) * merit[f] <= mk.flex[f] + 1e-6).all()
        assert by_day.first().to_numpy() == pytest.approx(
            mk.share * pd.Series(merit[f]).groupby(x["day"].to_numpy()).mean().to_numpy())
    deep = mk.dispatch(np.full(len(x), mk.shutdown - 60))
    floor = mk.merit(np.full(len(x), mk.shutdown - 60))
    for f in fm.FUELS:
        assert deep[f] == pytest.approx(mk.mr[f] + (1 - mk.share) * floor[f], abs=1.0)


def test_commitment_widens_the_daily_price_range(market):
    """Plants held to the day's dispatch cannot follow the hour, so the price has to move more."""
    _, x, fl = market
    ranges = []
    for share in [0.0, 0.5]:
        theta = fm.X0.copy()
        theta[11] = share
        p, _ = fm.solve(fm.Market(x, fl, theta, CFG), x, fl, CFG, np.zeros(len(x)))
        ranges.append(pd.Series(p).groupby(x["day"].to_numpy()).agg(lambda v: v.max() - v.min()).mean())
    assert ranges[1] > ranges[0]


def test_storage_arbitrage_respects_power_and_losses():
    price = np.tile(np.r_[np.full(8, 20.0), np.full(8, 60.0), np.full(8, 150.0)], 3)
    day = np.repeat(np.arange(3), 24)
    out = fm.arbitrage(price, day, np.full(len(price), 1000.0), hours=6, eff=0.75)
    assert out.min() >= -1000.0 and out.max() <= 750.0
    for d in range(3):
        charged, discharged = -out[day == d].clip(max=0).sum(), out[day == d].clip(min=0).sum()
        assert discharged == pytest.approx(0.75 * charged)
        assert (out[(day == d) & (price == 20.0)] <= 0).all() and (out[(day == d) & (price == 150.0)] >= 0).all()


def test_storage_idles_when_the_spread_does_not_cover_losses():
    price = np.r_[np.full(12, 100.0), np.full(12, 120.0)]
    assert not fm.arbitrage(price, np.zeros(24, int), np.full(24, 1000.0), hours=6, eff=0.75).any()


def test_fleet_efficiencies_are_capacity_weighted():
    plants = pd.DataFrame({
        "status": "operating", "energy_source": ["Lignite"] * 2 + ["Hard coal", "Natural gas", "Oil"],
        "capacity_net_bnetza": [900.0, 100.0, 500.0, 500.0, 100.0], "efficiency_estimate": [0.35, 0.45, 0.4, 0.55, 0.3],
    })
    eff = fm.efficiencies(plants)
    assert (eff["lignite"] == 0.35).sum() == round(0.9 * fm.BINS)  # 90% of capacity at 35%
    assert set(eff["gas"]) == {0.55}


def test_trade_fit_recovers_a_known_response():
    rng = np.random.default_rng(5)
    x = hours_frame(24 * 60, rng)
    x["price"] = rng.uniform(-20, 250, len(x))
    truth = np.r_[1000.0, 0.05, -0.2, -0.1, -0.08, 4000.0, 1500.0, 0.4]
    x["trade"] = fm.trade_at(fm.trade_design(x), fm.ccgt_cost(x, CFG), x["price"].to_numpy(), truth)
    fitted = fm.fit_trade(x, CFG)
    assert fitted[5:] == pytest.approx(truth[5:], rel=0.02)


def test_eua_report_is_parsed_whatever_its_layout():
    """EEX moved the header row and renamed the price column over the years."""
    old = pd.DataFrame([[None, "EUA Primary Market Auction", None],
                        ["Date", "Auction Time", "Auction Price EUR/tCO2"],
                        ["2016-12-16", "11:00", 4.59], ["2016-12-15", "11:00", 4.96]])
    new = pd.DataFrame([[None] * 4, [None, "References", None, "Prices"],
                        [None, "Date", "Time", "Auction Price €/tCO2"],
                        [None, "2026-11-03", "11:00", 90.16]])
    for frame, first in [(old, 4.59), (new, 90.16)]:
        buf = io.BytesIO()
        frame.to_excel(buf, header=False, index=False)
        s = sources._eua_sheet(buf.getvalue())
        assert s.iloc[0] == first
        assert isinstance(s.index, pd.DatetimeIndex)
