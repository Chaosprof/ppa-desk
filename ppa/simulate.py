"""Joint hourly price x generation Monte Carlo.

Prices:  P_h = L_y * (F_h + D_h + M_h * du_h) - (D_h + dn_h)
    F_h  HPFC (so E[P_h] = F_h and the curve stays arbitrage-free)
    M_h  monthly baseload level of the HPFC
    D_h  expected negative part of the price for the hour's cell (shape.down), kept out of the level shock
    du_h positive part of a recent historical price relative to its month level, minus its cell mean
    dn_h negative part of the same price in EUR/MWh, minus its cell mean (does not scale with level)
    L_y  calendar-level shock, one-factor lognormal martingale with the forward vol

Generation: site capacity factor from ERA5 on the *same* historical day as z_h, so the joint
behaviour of weather and price (sunny noon -> low price) is resampled rather than assumed.
Days are drawn in weekly blocks from the same season of the recent window, shifted so weekdays
line up. Annual volume is then rescaled to a full-load-hours draw from 20+ ERA5 years plus a
per-path model error, so volume risk reflects long-run weather variability.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .hours import TZ, features, hours
from .hpfc import Curve, Shape, split
from .options import bachelier_put


@dataclass(frozen=True)
class Pool:
    """Recent days to resample: price residuals and site capacity factors on a local-hour grid."""

    days: pd.DatetimeIndex  # local midnight of each pool day
    du: np.ndarray  # (D, 24)
    dn: np.ndarray  # (D, 24)
    cf: np.ndarray  # (D, 24)


def _grid(values: pd.Series, days: pd.DatetimeIndex) -> np.ndarray:
    """(day, local hour) grid; the 25th hour on DST days is averaged in, the missing 23rd filled."""
    local = values.index.tz_convert(TZ)
    frame = pd.DataFrame({"day": local.normalize().tz_localize(None), "hour": local.hour, "v": values.to_numpy()})
    grid = frame.groupby(["day", "hour"])["v"].mean().unstack().reindex(index=days, columns=range(24))
    return grid.ffill(axis=1).bfill(axis=1).to_numpy()


def residuals(prices: pd.Series, window: tuple[dt.date, dt.date]) -> pd.DataFrame:
    """Sign-split residuals du, dn: each part minus its month x day type x hour mean."""
    lo, hi = window
    f = split(prices[hours(lo, hi + dt.timedelta(days=1))].dropna())
    cell = [f["month"], f["daytype"], f["hour"]]
    return pd.DataFrame({"du": f["up"] - f.groupby(cell)["up"].transform("mean"),
                         "dn": f["down"] - f.groupby(cell)["down"].transform("mean")}, index=f.index)


def pool(prices: pd.Series, cf: pd.Series, window: tuple[dt.date, dt.date]) -> Pool:
    lo, hi = window
    days = pd.date_range(lo, hi, freq="D")
    r = residuals(prices, window)
    return Pool(days, _grid(r["du"], days), _grid(r["dn"], days), _grid(cf[r.index.min():r.index.max()], days))


def _analogues(future: pd.DatetimeIndex, pool_days: pd.DatetimeIndex) -> np.ndarray:
    """For each future day, the pool days at the same date in each pool year, weekday-aligned.

    Rows are padded to 12 columns by repetition; 12 is divisible by any count from 1 to 4, so a uniform
    column draw picks every candidate with equal probability.
    """
    years = sorted(set(pool_days.year))
    width = 12
    out = np.full((len(future), width), -1)
    first, last = pool_days[0], pool_days[-1]
    for k, d in enumerate(future):
        cands = []
        for y in years:
            c = pd.Timestamp(y, d.month, min(d.day, 28 if d.month == 2 else d.day))
            shift = (d.weekday() - c.weekday() + 3) % 7 - 3
            c = c + pd.Timedelta(days=shift)
            if first <= c <= last:
                cands.append((c - first).days)
        cands = cands or [int(np.clip((d.replace(year=last.year) - first).days, 0, len(pool_days) - 1))]
        out[k] = (cands * width)[:width]
    return out


@dataclass
class YearSim:
    """Per-path totals for one delivery year (arrays of length paths) plus hourly means."""

    year: int
    hours: int
    base: float  # HPFC baseload average
    tau: float  # years from valuation to mid-delivery
    level: np.ndarray  # calendar-level shock L_y, mean one
    slope_error: np.ndarray  # standard normal, the same in every year: error in the cannibalisation slope
    volume: np.ndarray  # MWh
    revenue: np.ndarray  # sum G*P, EUR
    spot: np.ndarray  # sum P over hours: EUR per MW of baseload
    volume_pos: np.ndarray  # MWh in hours with P >= 0
    revenue_pos: np.ndarray
    revenue_det: np.ndarray  # sum E[G]*P: shape risk only
    volume_det_pos: np.ndarray  # sum E[G] over hours with P >= 0
    revenue_det_pos: np.ndarray
    revenue_fwd: np.ndarray  # sum G*F: volume risk only
    volume_fwd_pos: np.ndarray  # sum G over hours with F >= 0
    revenue_fwd_pos: np.ndarray
    put: np.ndarray  # sum G * max(-P, 0)
    negative_hours: np.ndarray
    profile_value: float  # sum E[G]*F
    put_normal: float  # sum E[G] * Bachelier put(F, sd(P)) at strike 0
    monthly_volume: np.ndarray  # (12,) expected MWh by month
    monthly_base: np.ndarray  # (12,) HPFC baseload by month
    daily_spread: np.ndarray | None  # (paths, days) 2h battery cycle value, first year only


def run(curve: Curve, shape: Shape, pl: Pool, years: list[int], flh: np.ndarray, valuation: dt.date, capacity_mw: float,
        forward_vol: float, model_sd: float, paths: int, seed: int) -> list[YearSim]:
    rng = np.random.default_rng(seed)
    tau = np.array([(dt.date(y, 7, 1) - valuation).days / 365.25 for y in years])
    # One Brownian path per scenario sampled at mid-year: calendar levels move together.
    dW = rng.standard_normal((paths, len(years))) * np.sqrt(np.diff(np.concatenate([[0.0], tau])))
    W = np.cumsum(dW, axis=1)
    level = np.exp(forward_vol * W - 0.5 * forward_vol**2 * tau)
    level /= level.mean(axis=0)  # moment-match: the level shock adds no sample bias
    model_error = 1 + model_sd * rng.standard_normal(paths)
    target_flh = rng.choice(flh, size=(paths, len(years))) * model_error[:, None]
    # Its own stream, so it leaves every other draw as it was; made orthogonal to each year's level and
    # level x volume, so an uncertain slope moves the risk and not the expected price.
    slope_error = np.random.default_rng([seed, 1]).standard_normal(paths)
    basis = np.column_stack([np.ones(paths), level, level * target_flh])
    slope_error -= basis @ np.linalg.lstsq(basis, slope_error, rcond=None)[0]
    slope_error /= slope_error.std()

    out = []
    for j, year in enumerate(years):
        idx = hours(dt.date(year, 1, 1), dt.date(year + 1, 1, 1))
        f = features(idx)
        F = curve.hourly.reindex(idx).to_numpy()
        month = f["month"].to_numpy()
        monthly_base = pd.Series(F).groupby(month).mean().to_numpy()
        M = monthly_base[month - 1]
        D = shape.factors(f, shape.down)

        local = idx.tz_convert(TZ)
        future_days = pd.DatetimeIndex(np.unique(local.normalize().tz_localize(None)))
        day_of = np.searchsorted(future_days, local.normalize().tz_localize(None))
        hour = local.hour.to_numpy()
        analog = _analogues(future_days, pl.days)  # (days, pool years)
        n_blocks = len(future_days) // 7 + 1
        pick = rng.integers(0, analog.shape[1], size=(paths, n_blocks))
        hist = analog[np.arange(len(future_days))[None, :], pick[:, np.arange(len(future_days)) // 7]]
        hist_h = hist[:, day_of]  # (paths, hours)

        du = pl.du[hist_h, hour[None, :]]
        dn = pl.dn[hist_h, hour[None, :]]
        cf = pl.cf[hist_h, hour[None, :]]
        cf *= (target_flh[:, j] / cf.sum(axis=1) * len(idx) / 8760)[:, None]
        G = capacity_mw * cf
        P = level[:, j : j + 1] * (F + D + M * du) - (D + dn)

        g_mean = G.mean(axis=0)
        pos = P >= 0
        sd = P.std(axis=0)
        out.append(YearSim(
            year=year, hours=len(idx), base=float(F.mean()), tau=float(tau[j]),
            level=level[:, j], slope_error=slope_error,
            volume=G.sum(axis=1), revenue=(G * P).sum(axis=1), spot=P.sum(axis=1),
            volume_pos=(G * pos).sum(axis=1), revenue_pos=(G * P * pos).sum(axis=1),
            revenue_det=P @ g_mean, volume_det_pos=pos @ g_mean, revenue_det_pos=(P * pos) @ g_mean,
            revenue_fwd=G @ F, volume_fwd_pos=G @ (F >= 0), revenue_fwd_pos=G @ np.maximum(F, 0),
            put=(G * np.maximum(-P, 0)).sum(axis=1),
            negative_hours=(~pos).sum(axis=1),
            profile_value=float(g_mean @ F), put_normal=float(g_mean @ bachelier_put(F, 0.0, sd)),
            monthly_volume=pd.Series(g_mean).groupby(month).sum().to_numpy(), monthly_base=monthly_base,
            daily_spread=battery_spread(P, day_of, len(future_days)) if j == 0 else None,
        ))
    return out



def battery_spread(P: np.ndarray, day_of: np.ndarray, n_days: int, efficiency: float = 0.88) -> np.ndarray:
    """Daily value of one 2-hour cycle per MW: buy the two cheapest hours before a split, sell the two
    dearest after it, choosing the best split so charging always precedes discharging."""
    out = np.empty((P.shape[0], n_days))
    for d in range(n_days):
        day = P[:, day_of == d]
        best = np.full(P.shape[0], -np.inf)
        for cut in range(2, day.shape[1] - 1):
            buy = np.sort(day[:, :cut], axis=1)[:, :2].sum(axis=1)
            sell = np.sort(day[:, cut:], axis=1)[:, -2:].sum(axis=1)
            best = np.maximum(best, sell - buy / efficiency)
        out[:, d] = best
    return out
