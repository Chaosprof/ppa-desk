"""Hourly price forward curve (HPFC).

    F_h = (level_h + a_d(h) + b_d(h) * peak_h) * up_h - down_h

The shape (up, down) is learned from recent day-ahead prices per month x day type x hour, and the
level from the calendar quote and monthly seasonality. Every monthly or yearly table is blended
linearly between period midpoints, so the prior has no steps at month or year boundaries. The daily
level corrections a (and b, if peak quotes exist) scale only the positive part of the price. They
minimise squared second differences — a discrete smoothing spline in the spirit of Benth, Koekebakker and
Ollmar (2007) — subject to every quoted future being repriced exactly by the hourly average.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from scipy.optimize import nnls

from .hours import TZ, delivery, features, hours


@dataclass(frozen=True)
class Shape:
    """Seasonal shape with a split sign: positive prices scale with the level, negative ones do not.

    Negative prices are set by renewable support and must-run, not by fuel, so a gas-driven move
    in the forward level should not make them deeper. Per cell (month x day type x hour):
        up   mean of max(p, 0) / month level      (dimensionless)
        down mean of max(-p, 0)                    (EUR/MWh)
    and expected price = level * up - down.
    """

    month: np.ndarray  # (12,) monthly level relative to its 12-month block
    up: np.ndarray  # (12, 3, 24)
    down: np.ndarray  # (12, 3, 24)

    def __call__(self, f: pd.DataFrame, level: np.ndarray) -> np.ndarray:
        """Expected hourly price for monthly base levels `level` (one per hour)."""
        return level * self.factors(f, self.up) - self.factors(f, self.down)

    def level(self, f: pd.DataFrame) -> np.ndarray:
        """Monthly level factor at each hour, blended across month boundaries with every monthly mean kept."""
        return blend(nodes(self.month, periodic=True), f["season"].to_numpy(), True)

    @staticmethod
    def factors(f: pd.DataFrame, table: np.ndarray) -> np.ndarray:
        """An up or down table (month x day type x hour) at each hour, blended across month boundaries."""
        v = nodes(table, periodic=True, positive=True)
        return blend(v, f["season"].to_numpy(), True, f["daytype"].to_numpy(), f["hour"].to_numpy())


def nodes(table: np.ndarray, periodic: bool, positive: bool = False) -> np.ndarray:
    """Node values at period midpoints whose linear blend averages to `table` over every period.

    Over period k, the blend averages (v[k-1] + 6 v[k] + v[k+1]) / 8. Periodic tables wrap around;
    otherwise the blend is flat beyond the first and last midpoints. Works along axis 0.

    Exact nodes overshoot below zero next to a sharp edge (no negative prices in March, many in
    April). With `positive`, nodes are non-negative, keep the total exactly and every period mean as
    closely as possible; a little of April's mass then lands in late March.
    """
    n = len(table)
    c = 6 * np.eye(n) + np.roll(np.eye(n), 1, 1) + np.roll(np.eye(n), -1, 1)
    if not periodic:
        c[0, -1] = c[-1, 0] = 0
        c[0, 0] += 1
        c[-1, -1] += 1
    flat = table.reshape(n, -1)
    if not positive:
        return np.linalg.solve(c / 8, flat).reshape(table.shape)
    a = np.vstack([c / 8, 1e3 * np.ones(n)])  # heavily weighted row: the total is kept
    v = [nnls(a, np.append(col, 1e3 * col.sum()))[0] for col in flat.T]
    return np.column_stack(v).reshape(table.shape)


def blend(v: np.ndarray, t: np.ndarray, periodic: bool, *index: np.ndarray) -> np.ndarray:
    """Linear interpolation of nodes v (axis 0) placed at t = 0, 1, 2, ..."""
    lo = np.floor(t).astype(int)
    w = t - lo
    n = len(v)
    i0, i1 = (lo % n, (lo + 1) % n) if periodic else (np.clip(lo, 0, n - 1), np.clip(lo + 1, 0, n - 1))
    return (1 - w) * v[(i0, *index)] + w * v[(i1, *index)]


def split(prices: pd.Series) -> pd.DataFrame:
    """Per hour: features, month level, positive part relative to the level, negative part."""
    f = features(prices.index).assign(price=prices.to_numpy())
    f["level"] = f.groupby(["year", "month"])["price"].transform("mean")
    f["up"] = f["price"].clip(lower=0) / f["level"]
    f["down"] = (-f["price"]).clip(lower=0)
    return f


def learn_shape(prices: pd.Series, window: tuple[dt.date, dt.date]) -> Shape:
    """Shape factors from whole 12-month blocks of the window."""
    lo, hi = window
    f = split(prices[hours(lo, hi + dt.timedelta(days=1))].dropna())
    f["block"] = ((f["year"] - lo.year) * 12 + f["month"] - lo.month) // 12

    month_mean = f.groupby(["block", "month"])["price"].mean()
    block_mean = f.groupby("block")["price"].mean()
    month = (month_mean / block_mean.reindex(month_mean.index, level="block")).groupby("month").mean()

    def grid(col):
        g = f.groupby(["month", "daytype", "hour"])[col].mean().unstack().reindex(columns=range(24))
        return g.T.ffill().T.to_numpy().reshape(12, 3, 24)  # DST gaps: hour 2 can be missing

    return Shape(month.reindex(range(1, 13)).to_numpy(), grid("up"), grid("down"))


@dataclass(frozen=True)
class Quote:
    contract: str
    price: float
    peak: bool = False
    extrapolated: bool = False

    @property
    def period(self) -> tuple[dt.date, dt.date]:
        return delivery(self.contract)

    @property
    def length(self) -> int:
        lo, hi = self.period
        return (hi - lo).days


@dataclass(frozen=True)
class Curve:
    hourly: pd.Series  # EUR/MWh by UTC hour start
    quotes: list[Quote]
    implied: dict[str, float]  # redundant quotes -> price implied by the finer contracts
    correction: pd.Series  # daily level correction a_d, EUR/MWh

    def average(self, lo: dt.date, hi: dt.date, peak: bool = False) -> float:
        h = self.hourly[hours(lo, hi)]
        if peak:
            h = h[features(h.index)["peak"].to_numpy()]
        return float(h.mean())

    def reprice(self, q: Quote) -> float:
        return self.average(*q.period, peak=q.peak)


def quotes_from(cfg_futures: dict, years: list[int]) -> list[Quote]:
    """Quotes from the config, with flat nominal calendar years beyond the last listed one."""
    out = [Quote(q["contract"], q["price"]) for q in cfg_futures["base"]]
    out += [Quote(q["contract"], q["price"], peak=True) for q in cfg_futures.get("peak", [])]
    cals = sorted((q for q in out if q.contract.startswith("Cal") and not q.peak), key=lambda q: q.period)
    last = cals[-1]
    for year in years:
        if year > last.period[0].year:
            out.append(Quote(f"Cal-{year % 100:02d}", last.price, extrapolated=True))
    return out


def build(quotes: list[Quote], shape: Shape, end: dt.date | None = None) -> Curve:
    lo = min(q.period[0] for q in quotes)
    hi = max(max(q.period[1] for q in quotes), end or lo)
    idx = hours(lo, hi)
    f = features(idx)

    level = _annual_levels(quotes, idx) * shape.level(f)
    prior = shape(f, level)

    days, day_of = np.unique(f["date"].to_numpy(), return_inverse=True)
    n_days = len(days)
    peak = f["peak"].to_numpy()
    up = shape.factors(f, shape.up)
    use_peak = any(q.peak for q in quotes)

    rows, rhs, kept, implied = [], [], [], {}
    for q in sorted(quotes, key=lambda q: (q.length, q.peak)):  # finest first; coarse ones may be implied
        mask = _mask(idx, q)
        n = mask.sum()
        a = np.bincount(day_of[mask], weights=up[mask], minlength=n_days) / n
        b = np.bincount(day_of[mask & peak], weights=up[mask & peak], minlength=n_days) / n if use_peak else np.zeros(0)
        row = np.concatenate([a, b])
        target = q.price - prior[mask].mean()
        if rows and np.linalg.matrix_rank(np.vstack(rows + [row]), tol=1e-10) == len(rows):
            implied[q.contract + (" peak" if q.peak else "")] = target  # resolved after the solve
            continue
        rows.append(row)
        rhs.append(target)
        kept.append(q)

    x = _solve(np.vstack(rows), np.array(rhs), n_days, use_peak)
    corr = (x[day_of] + (x[n_days + day_of] * peak if use_peak else 0.0)) * up
    curve = pd.Series(prior + corr, index=idx, name="hpfc")

    correction = pd.Series(x[:n_days], index=days, name="correction")
    result = Curve(curve, quotes, {}, correction)
    for q in quotes:
        key = q.contract + (" peak" if q.peak else "")
        if key in implied:
            implied[key] = result.reprice(q)
    return Curve(curve, quotes, implied, correction)


def _annual_levels(quotes: list[Quote], idx: pd.DatetimeIndex) -> np.ndarray:
    """Calendar-year base quotes, blended across New Year with each year's mean kept."""
    cal = {q.period[0].year: q.price for q in quotes if q.contract.startswith("Cal") and not q.peak}
    local = idx.tz_convert(TZ)
    years = np.arange(local.year.min(), local.year.max() + 1)
    table = np.array([cal.get(y, cal[min(cal)]) for y in years])
    t = local.year - years[0] + (local.day_of_year - 0.5) / (365 + local.is_leap_year) - 0.5
    return blend(nodes(table, periodic=False), t.to_numpy(), False)


def _mask(idx: pd.DatetimeIndex, q: Quote) -> np.ndarray:
    lo, hi = q.period
    window = hours(lo, hi)
    mask = idx.isin(window)
    if q.peak:
        mask &= features(idx)["peak"].to_numpy()
    return mask


def _solve(A: np.ndarray, r: np.ndarray, n_days: int, use_peak: bool) -> np.ndarray:
    """min x'Px s.t. Ax = r, with P the squared second-difference operator per component."""
    d2 = sp.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(n_days - 2, n_days))
    block = (d2.T @ d2) + 1e-9 * sp.identity(n_days)
    P = sp.block_diag([block, block]) if use_peak else block
    k = A.shape[0]
    kkt = sp.bmat([[2 * P, sp.csr_matrix(A.T)], [sp.csr_matrix(A), None]], format="csc")
    sol = spla.spsolve(kkt, np.concatenate([np.zeros(P.shape[0]), r]))
    return sol[: P.shape[0]] if k else np.zeros(P.shape[0])
