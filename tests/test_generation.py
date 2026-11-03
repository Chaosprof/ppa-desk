import numpy as np
import pandas as pd
import pytest

from ppa import generation


def test_sun_is_due_south_and_high_at_solar_noon_in_june():
    t = pd.DatetimeIndex(["2027-06-21 11:20"], tz="UTC")  # solar noon at 10°E is ~11:20 UTC
    zenith, azimuth = generation.solar_position(t, 50.0, 10.0)
    assert np.degrees(zenith[0]) == pytest.approx(50.0 - 23.44, abs=0.5)
    assert np.degrees(azimuth[0]) == pytest.approx(180, abs=3)


def test_pv_output_is_zero_at_night_and_clipped(cfg):
    t = pd.date_range("2027-06-21", periods=24, freq="h", tz="UTC") + pd.Timedelta(hours=1)
    w = pd.DataFrame({"shortwave_radiation": 1000.0, "direct_normal_irradiance": 900.0, "diffuse_radiation": 100.0,
                      "temperature_2m": 20.0, "wind_speed_10m": 3.0, "wind_speed_100m": 6.0}, index=t)
    cf = generation.solar_cf(w, 50.0, 10.0, cfg["technology"]["solar"])
    assert cf.iloc[0] == 0 and cf.iloc[23] == 0  # midnight UTC hours
    assert cf.max() <= 1.0 and cf.iloc[11] > 0.8


def test_wind_curve_is_monotone_until_cut_out(cfg):
    v = np.linspace(0, 18, 200)  # hub speed stays below cut-out
    w = pd.DataFrame({"wind_speed_10m": v * 0.75, "wind_speed_100m": v, "temperature_2m": 15.0})
    cf = generation.wind_cf(w, cfg["technology"]["wind"]).to_numpy()
    assert np.all(np.diff(cf) >= -1e-9)
    assert cf[0] < 1e-4 and cf.max() <= 1 - cfg["technology"]["wind"]["losses"] + 1e-9


def test_p90_is_below_p50_and_ten_years_is_tighter():
    idx = pd.date_range("2005-01-01", "2024-12-31 23:00", freq="h", tz="UTC")
    rng = np.random.default_rng(0)
    annual = pd.Series(rng.normal(1.0, 0.05, 20), index=range(2005, 2025))
    cf = pd.Series(0.12 * annual.reindex(idx.year).to_numpy(), index=idx)
    y = generation.yield_stats(cf, 0.05)
    assert y["p90_1y"] < y["p90_10y"] < y["p50"]
