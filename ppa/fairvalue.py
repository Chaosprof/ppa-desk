"""Fundamental fair value of the next calendar year against the futures market, month by month.

At each month end t, the merit-order model replays the twelve months before t with what was known at t:
    parameters      the backtest's fit for t's year, made on the three whole years before it
    the system      weather, demand, wind, solar, trade and storage of those twelve months, as they were.
                    Scaling older weather to newer wind and solar capacity is not done: the flexibility that
                    grew alongside (batteries, exports, curtailment) is buried in observed demand, so the
                    model would see 1,000–1,700 negative hours a year where the market had about 500
    nuclear         scaled to the capacity left in each month of the delivery year (closures are set by law)
    thermal         the capacity installed at t
    fuel, carbon    the month's average spot fuel prices and the last EUA auction before t; free forward
                    curves for gas and coal do not exist, so the fuel curve is taken as flat
The fair value is the mean clearing price: what the next year would cost if the system and fuel stayed as they
are. The gap to the Cal futures settlement at t is
then tested as a signal: does it predict the futures' change over the following month?
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import fundamental as fm
from .hours import TZ

VERSION = 2  # bump when the method changes, so build re-runs the cached fair values


def month_ends(cal: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """The next-calendar futures price on the last trading day of each month in [start, end]."""
    s = cal[(cal.index >= start) & (cal.index <= end)]
    return s.groupby(s.index.to_period("M")).tail(1)


def scenario(x: pd.DataFrame, nuclear: pd.Series, installed: pd.DataFrame, fuel: pd.DataFrame, eua: pd.Series,
             as_of: pd.Timestamp, cfg: dict) -> pd.DataFrame:
    """The hours of x restated for delivery in the year after `as_of`, using only what was known at `as_of`."""
    year = as_of.year + 1
    s = x.copy()
    local = s.index.tz_convert(TZ).tz_localize(None)
    month_start = local.to_period("M").to_timestamp()
    cap = installed.reindex(month_start).ffill()
    planned = installed["nuclear"].reindex(pd.date_range(f"{year}-01-01", periods=12, freq="MS")).fillna(
        installed["nuclear"].iloc[-1])
    then = cap["nuclear"].to_numpy()
    ratio = np.divide(planned.to_numpy()[s["month"].to_numpy() - 1], then, out=np.zeros(len(s)), where=then > 0)
    s["must_take"] += nuclear.reindex(s.index).fillna(0).to_numpy() * (ratio - 1)

    now = installed[: as_of].iloc[-1]
    for f in fm.FUELS:
        s[f"cap_{f}"] = now[f] * 1000
    prices = fuel[: as_of].iloc[-1]
    for f in ["gas", "coal", "oil"]:
        s[f"fuel_{f}"] = prices[f]
    s["fuel_lignite"] = cfg["lignite_fuel"]
    s["co2"] = float(eua[: as_of].iloc[-1])
    return s


def trailing(x: pd.DataFrame, as_of: pd.Timestamp, months: int = 12) -> pd.DataFrame:
    """The hours of the `months` months before the local day `as_of`."""
    end = pd.Timestamp(as_of.date()).tz_localize(TZ)
    return x[(x.index >= end - pd.DateOffset(months=months)) & (x.index < end)]


def _year(args) -> list[dict]:
    """Fair values for every month end in one year: one fleet and parameter set, re-run per month."""
    year, dates, x, nuclear, installed, fuel, eua, theta, eff, cfg = args
    fl = fm.fleet(x[(x["year"] >= year - cfg["train_years"]) & (x["year"] < year)], eff, cfg)
    out = []
    for as_of in dates:
        s = scenario(trailing(x, as_of), nuclear, installed, fuel, eua, as_of, cfg)
        price, _ = fm.solve(fm.Market(s, fl, theta, cfg), s, fl, cfg)
        out.append({"date": as_of, "fair_value": float(price.mean()), "negative_hours": int((price < 0).sum())})
    return out


def run(x: pd.DataFrame, nuclear: pd.Series, installed: pd.DataFrame, fuels: dict[str, pd.Series], eua: pd.Series,
        params: pd.DataFrame, eff: dict[str, np.ndarray], futures: pd.Series, cfg: dict) -> pd.DataFrame:
    """Fair value of the next calendar year at each date in `futures` (indexed by date), years in parallel."""
    fuel = fm.fuel_prices(fuels)
    jobs = []
    for year, dates in futures.groupby(futures.index.year):
        if year in params.index:
            jobs.append((int(year), list(dates.index), x, nuclear, installed, fuel, eua,
                         params.loc[year].to_numpy(), eff, cfg))
    with ProcessPoolExecutor(min(len(jobs), 8)) as pool:
        rows = [r for part in pool.map(_year, jobs) for r in part]
    out = pd.DataFrame(rows).set_index("date").sort_index()
    out["futures"] = futures.reindex(out.index)
    out["contract"] = [f"Cal-{(d.year + 1) % 100:02d}" for d in out.index]
    out["gap"] = out["fair_value"] - out["futures"]
    return out


# Central-difference bumps: column, size in the column's unit, and units of the quoted price per column unit.
BUMPS = {
    "gas": ("fuel_gas", 5.0 / fm.GAS_NET_PER_GROSS, fm.GAS_NET_PER_GROSS),  # EUR/MWh as quoted (gross), held net
    "carbon": ("co2", 10.0, 1.0),  # EUR/t
    "coal": ("fuel_coal", 10.0 / fm.MWH_PER_TONNE_COAL, fm.MWH_PER_TONNE_COAL),  # EUR/t, held in EUR/MWh thermal
}


def sensitivities(s: pd.DataFrame, fl: fm.Fleet, theta: np.ndarray, cfg: dict) -> pd.DataFrame:
    """What one unit of gas (EUR/MWh), carbon (EUR/t) and coal (EUR/t) moves, in EUR/MWh of power.

    The scenario `s` is cleared again with each input bumped up and down. Per input: its level in `s`, and the
    change in the baseload price and in the solar and wind capture prices. The capture prices move less than
    baseload when wind and solar produce in hours that fuel does not set.
    """
    def clear(column: str | None = None, shift: float = 0.0) -> pd.Series:
        b = s if column is None else s.assign(**{column: s[column] + shift})
        price, _ = fm.solve(fm.Market(b, fl, theta, cfg), b, fl, cfg)
        weighted = {k: float((price * b[f"{k}_fc"]).sum() / b[f"{k}_fc"].sum()) for k in ["solar", "wind"]}
        return pd.Series({"base": float(price.mean()), **weighted})

    rows = {}
    for name, (column, size, per_unit) in BUMPS.items():
        slope = (clear(column, size) - clear(column, -size)) / (2 * size * per_unit)
        rows[name] = pd.concat([pd.Series({"input": float(s[column].mean()) * per_unit}), slope])
    return pd.DataFrame(rows).T


def next_change(history: pd.DataFrame, fv: pd.DataFrame) -> pd.Series:
    """Change in each row's contract from its date to the next month end on which it still trades."""
    wide = history.pivot_table(index="date", columns="contract", values="price").sort_index()
    out = {}
    dates = list(fv.index)
    for d, nxt in zip(dates, dates[1:] + [None], strict=True):
        c = fv.at[d, "contract"]
        if nxt is None or nxt not in wide.index or c not in wide.columns:
            continue
        a, b = wide.at[d, c], wide.at[nxt, c]
        if pd.notna(a) and pd.notna(b):
            out[d] = b - a
    return pd.Series(out, dtype=float)


def signal_stats(signal: pd.Series, change: pd.Series) -> dict:
    """Trading sign(signal) one month at a time: hit rate, mean P&L per MWh, t-statistic, correlation."""
    d = pd.DataFrame({"s": signal, "c": change}).dropna()
    d = d[d["s"] != 0]
    pnl = np.sign(d["s"]) * d["c"]
    n = len(pnl)
    return {
        "months": n,
        "hit_rate": float((pnl > 0).mean()),
        "mean": float(pnl.mean()),
        "t": float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(n)),
        "corr": float(d["s"].corr(d["c"])),
        "cumulative": pnl.cumsum(),
    }


def delivery_years(fv: pd.DataFrame, prices: pd.Series) -> pd.DataFrame:
    """Per delivery year: mean futures and fair value over the month ends before it, and the realised price."""
    local = prices.index.tz_convert(TZ)
    realised = prices.groupby(local.year).agg(["mean", "count"])
    realised = realised[realised["count"] > 8700]["mean"]
    g = fv.groupby(fv.index.year + 1)[["futures", "fair_value"]].mean()
    g["realised"] = realised.reindex(g.index)
    return g
