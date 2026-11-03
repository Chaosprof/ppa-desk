"""Site generation from ERA5 weather: a fixed-tilt PV model and a hub-height wind power curve.

Output is the AC capacity factor per hour start (UTC), so a 100 MW plant produces 100 * cf MWh.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SOLAR_CONSTANT = 1361.0  # W/m²
ALBEDO = 0.2


def solar_position(t: pd.DatetimeIndex, lat: float, lon: float) -> tuple[np.ndarray, np.ndarray]:
    """Zenith and azimuth (radians, azimuth clockwise from north) — NOAA simplified algorithm."""
    doy = t.day_of_year.to_numpy()
    hour = (t.hour + t.minute / 60).to_numpy()
    g = 2 * np.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eot = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                    - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    hour_angle = np.radians((hour * 60 + eot + 4 * lon) / 4 - 180)
    phi = np.radians(lat)
    cos_z = np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.cos(hour_angle)
    zenith = np.arccos(np.clip(cos_z, -1, 1))
    az = np.arctan2(np.sin(hour_angle), np.cos(hour_angle) * np.sin(phi) - np.tan(decl) * np.cos(phi))
    return zenith, (az + np.pi) % (2 * np.pi)


def solar_cf(w: pd.DataFrame, lat: float, lon: float, p: dict) -> pd.Series:
    """Hay–Davies transposition, Faiman cell temperature, linear temperature derate, clipping."""
    start = w.index - pd.Timedelta(hours=1)  # ERA5 radiation is the mean over the preceding hour
    zenith, azimuth = solar_position(w.index - pd.Timedelta(minutes=30), lat, lon)
    k = p.get("irradiance_scale", 1.0)
    ghi = k * w["shortwave_radiation"].to_numpy()
    dni = k * w["direct_normal_irradiance"].to_numpy()
    dhi = k * w["diffuse_radiation"].to_numpy()

    tilt, surface_az = np.radians(p["tilt"]), np.radians(p["azimuth"])
    cos_aoi = np.cos(zenith) * np.cos(tilt) + np.sin(zenith) * np.sin(tilt) * np.cos(azimuth - surface_az)
    cos_aoi = np.clip(cos_aoi, 0, None)
    cos_z = np.maximum(np.cos(zenith), 0.087)
    i0 = SOLAR_CONSTANT * (1 + 0.033 * np.cos(2 * np.pi * w.index.day_of_year.to_numpy() / 365))
    anisotropy = np.clip(dni / i0, 0, 1)
    sky = dhi * (anisotropy * cos_aoi / cos_z + (1 - anisotropy) * (1 + np.cos(tilt)) / 2)
    poa = dni * cos_aoi + sky + ghi * ALBEDO * (1 - np.cos(tilt)) / 2
    poa = np.where(np.cos(zenith) > 0, poa, 0.0)

    t_cell = w["temperature_2m"].to_numpy() + poa / (25.0 + 6.84 * w["wind_speed_10m"].to_numpy())
    dc = poa / 1000 * (1 + p["temp_coeff"] * (t_cell - 25)) * (1 - p["losses"])
    ac = np.clip(dc * p["dc_ac_ratio"], 0, 1)
    return pd.Series(ac, index=start, name="cf")


def wind_cf(w: pd.DataFrame, p: dict) -> pd.Series:
    """Hub-height speed by hourly shear, air-density correction and a park-smoothed power curve."""
    v10 = np.maximum(w["wind_speed_10m"].to_numpy(), 0.1)
    v100 = np.maximum(w["wind_speed_100m"].to_numpy(), 0.1)
    alpha = np.clip(np.log(v100 / v10) / np.log(10), 0.05, 0.45)
    v = v100 * (p["hub_height"] / 100) ** alpha
    rho = 1.225 * 288.15 / (w["temperature_2m"].to_numpy() + 273.15)
    v_eq = v * (rho / 1.225) ** (1 / 3)
    cf = _smoothed_curve(np.array(p["power_curve"]))(v_eq) * (1 - p["losses"])
    return pd.Series(cf, index=w.index, name="cf")


def _smoothed_curve(curve: np.ndarray, sigma: float = 0.8):
    """Convolve the turbine curve with N(0, sigma) to represent a park rather than one turbine."""
    grid = np.linspace(0, 40, 4001)
    single = np.interp(grid, curve[:, 0], curve[:, 1])
    kernel_x = np.arange(-4 * sigma, 4 * sigma + 0.01, grid[1] - grid[0])
    kernel = np.exp(-0.5 * (kernel_x / sigma) ** 2)
    smooth = np.convolve(single, kernel / kernel.sum(), mode="same")
    return lambda v: np.interp(v, grid, smooth)


def capacity_factor(tech: str, w: pd.DataFrame, site: dict, params: dict) -> pd.Series:
    cf = solar_cf(w, site["lat"], site["lon"], params) if tech == "solar" else wind_cf(w, params)
    return cf.dropna()


def annual_hours(cf: pd.Series) -> pd.Series:
    """Full-load hours per complete calendar year (UTC years are close enough for yield stats)."""
    counts = cf.groupby(cf.index.year).count()
    full = counts[counts >= 8700].index
    return cf.groupby(cf.index.year).sum().loc[full]


def yield_stats(cf: pd.Series, model_uncertainty: float) -> dict:
    """P50 and P90 full-load hours for 1 and 10 years: weather variability plus model error."""
    flh = annual_hours(cf)
    p50, weather = float(flh.mean()), float(flh.std(ddof=1) / flh.mean())
    out = {"years": [int(y) for y in flh.index], "flh": [round(float(x), 1) for x in flh],
           "p50": round(p50, 1), "weather_sd": round(weather, 4)}
    for n in (1, 10):
        sd = np.hypot(weather / np.sqrt(n), model_uncertainty)
        out[f"p90_{n}y"] = round(p50 * (1 - 1.2816 * sd), 1)
    return out
