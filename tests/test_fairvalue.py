import numpy as np
import pandas as pd
import pytest

from ppa import fairvalue as fv

CFG = {"lignite_fuel": 5.0}


def installed(nuclear_until: str = "2023-04-01") -> pd.DataFrame:
    """Monthly capacity, GW: solar grows 1 GW a month, wind 0.5, nuclear 4 until it closes."""
    idx = pd.date_range("2015-01-01", "2026-12-01", freq="MS")
    k = np.arange(len(idx), dtype=float)
    return pd.DataFrame({
        "solar": 40 + k, "wind_onshore": 50 + 0.5 * k, "wind_offshore": np.full(len(idx), 8.0),
        "nuclear": np.where(idx < pd.Timestamp(nuclear_until), 4.0, 0.0),
        "lignite": 18.0, "coal": 15.0, "gas": 33.0, "oil": 4.0,
    }, index=idx)


def hours_2021() -> pd.DataFrame:
    idx = pd.date_range("2021-01-01", "2021-12-31 23:00", freq="h", tz="UTC")
    local = idx.tz_convert("Europe/Berlin")
    return pd.DataFrame({"wind_fc": 20000.0, "solar_fc": 10000.0, "must_take": 12000.0,
                         "month": local.month, "year": local.year}, index=idx)


def test_trailing_takes_the_twelve_months_before_the_day():
    idx = pd.date_range("2020-01-01", "2022-12-31 23:00", freq="h", tz="UTC")
    x = pd.DataFrame({"v": 1.0}, index=idx)
    t = fv.trailing(x, pd.Timestamp("2022-06-30"))
    assert t.index.max() < pd.Timestamp("2022-06-30", tz="Europe/Berlin")
    assert t.index.min() == pd.Timestamp("2021-06-30", tz="Europe/Berlin")


def test_scenario_uses_nothing_published_after_the_date():
    x, inst = hours_2021(), installed()
    as_of = pd.Timestamp("2022-06-30")
    months = pd.date_range("2015-01-01", "2026-12-01", freq="MS")
    fuel = pd.DataFrame({"gas": 30.0, "coal": 10.0, "oil": 40.0}, index=months)
    fuel.loc["2022-07-01":] *= 10
    eua = pd.Series(80.0, index=pd.date_range("2015-01-01", "2026-12-31", freq="D"))
    eua["2022-07-01":] = 500.0
    s = fv.scenario(x, pd.Series(0.0, index=x.index), inst, fuel, eua, as_of, CFG)
    assert (s["fuel_gas"] == 30.0).all() and (s["co2"] == 80.0).all()
    assert (s["cap_gas"] == 33000.0).all()


def test_scenario_keeps_wind_and_solar_and_removes_closed_nuclear():
    x, inst = hours_2021(), installed()
    nuclear = pd.Series(3500.0, index=x.index)
    months = pd.date_range("2015-01-01", "2026-12-01", freq="MS")
    fuel = pd.DataFrame({"gas": 30.0, "coal": 10.0, "oil": 40.0}, index=months)
    eua = pd.Series(80.0, index=pd.date_range("2015-01-01", "2026-12-31", freq="D"))
    s = fv.scenario(x, nuclear, inst, fuel, eua, pd.Timestamp("2023-06-30"), CFG)  # delivery 2024: nuclear gone
    assert s["must_take"].to_numpy() == pytest.approx(12000.0 - 3500.0)
    assert (s["solar_fc"] == x["solar_fc"]).all() and (s["wind_fc"] == x["wind_fc"]).all()
    kept = fv.scenario(x, nuclear, inst, fuel, eua, pd.Timestamp("2021-06-30"), CFG)  # delivery 2022: still open
    assert kept["must_take"].to_numpy() == pytest.approx(12000.0)


def test_next_change_stays_within_a_contract_and_skips_expiry():
    dates = pd.to_datetime(["2022-11-30", "2022-12-30", "2023-01-31"])
    history = pd.DataFrame({
        "date": [dates[0], dates[1], dates[0], dates[1], dates[2]],
        "contract": ["Cal-23", "Cal-23", "Cal-24", "Cal-24", "Cal-24"],
        "price": [300.0, 250.0, 200.0, 190.0, 150.0],
    })
    rows = pd.DataFrame({"contract": ["Cal-23", "Cal-23", "Cal-24"]}, index=dates)
    ch = fv.next_change(history, rows)
    assert ch.to_dict() == {dates[0]: -50.0}  # Dec 2022's Cal-23 expired before the next month end


def test_signal_stats_trade_the_sign_of_the_gap():
    signal = pd.Series([10.0, -5.0, 3.0, -1.0])
    change = pd.Series([4.0, -2.0, -6.0, 1.0])
    st = fv.signal_stats(signal, change)
    assert st["months"] == 4 and st["hit_rate"] == 0.5
    assert st["mean"] == pytest.approx((4 + 2 - 6 - 1) / 4)
    assert st["cumulative"].iloc[-1] == pytest.approx(-1.0)
