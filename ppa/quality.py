"""Data validation: coverage, physical bounds and cross-source agreement.

Each check returns one row: what was tested, the measured value and whether it passed.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .dataset import Dataset
from .generation import solar_position
from .hours import hours
from .hpfc import Curve

PRICE_FLOOR, PRICE_CAP = -500.0, 4000.0  # harmonised SDAC limits


def _row(check: str, value: str, ok: bool) -> dict:
    return {"check": check, "value": value, "ok": bool(ok)}


def run(data: Dataset, curve: Curve, start: dt.date, fwd: dict, fund: dict) -> list[dict]:
    cfg = data.cfg
    rows = []

    expected = hours(start, cfg.history_end + dt.timedelta(days=1))
    p = data.prices.reindex(expected)
    missing = int(p.isna().sum())
    gaps = p.isna().astype(int).groupby(p.notna().cumsum()).sum().max()
    rows.append(_row("Day-ahead hours present", f"{len(p) - missing:,} of {len(p):,}", missing / len(p) < 1e-3))
    rows.append(_row("Longest price gap", f"{int(gaps)} h", gaps <= 6))
    out = int(((p < PRICE_FLOOR) | (p > PRICE_CAP)).sum())
    rows.append(_row("Prices outside −500…4000 €/MWh", f"{out}", out == 0))

    ec = data.prices
    sm = data.smard_prices
    both = pd.concat([ec, sm], axis=1, join="inner").dropna()
    diff = (both.iloc[:, 0] - both.iloc[:, 1]).abs()
    rows.append(_row("Price vs SMARD, hours within 0.01 €", f"{(diff <= 0.01).mean():.2%} of {len(diff):,}",
                     (diff <= 0.01).mean() > 0.99))
    rows.append(_row("Price vs SMARD, largest gap", f"{diff.max():.2f} €/MWh", diff.max() < 1.0))

    g = data.generation
    solar = pd.concat([g["solar"], data.smard_solar], axis=1, join="inner").dropna()
    solar_diff = solar.iloc[:, 0].sum() / solar.iloc[:, 1].sum() - 1
    rows.append(_row("Solar vs SMARD, energy", f"{solar_diff:+.2%}", abs(solar_diff) < 0.03))
    rows.append(_row("Solar vs SMARD, hourly correlation", f"{solar.corr().iloc[0, 1]:.4f}",
                     solar.corr().iloc[0, 1] > 0.99))

    zenith, _ = solar_position(g.index + pd.Timedelta(minutes=30), 51.2, 10.4)
    night = (np.degrees(zenith) > 100) & (g["solar"].to_numpy() > 500)
    rows.append(_row("Solar above 500 MW at night", f"{int(night.sum())} h", night.sum() == 0))

    cap = data.capacity
    for col, label in [("solar", "Solar"), ("wind_onshore", "Onshore wind")]:
        month_cap = cap[col].reindex(g.index.tz_convert(None).to_period("M").to_timestamp()).to_numpy() * 1000
        over = int((g[col].to_numpy() > month_cap * 1.02).sum())
        rows.append(_row(f"{label} above installed capacity", f"{over} h", over < 24))
    neg = int((g[["solar", "wind_onshore", "wind_offshore"]] < -1).sum().sum())
    rows.append(_row("Negative generation values", f"{neg}", neg == 0))

    f = data.forecasts
    lo, hi = cfg.window
    cover = f.reindex(hours(lo, hi + dt.timedelta(days=1))).notna().all(axis=1).mean()
    rows.append(_row("Day-ahead forecast coverage", f"{cover:.2%}", cover > 0.98))

    errors = [abs(curve.reprice(q) - q.price) for q in curve.quotes]
    rows.append(_row("HPFC repricing, largest error", f"{max(errors):.1e} €/MWh", max(errors) < 0.01))
    for name, implied in curve.implied.items():
        quote = next(q.price for q in curve.quotes if q.contract == name.split()[0])
        rows.append(_row(f"{name} implied by finer contracts", f"{implied:.3f} vs {quote:.2f}",
                         abs(implied - quote) < 0.01))

    h = data.omip_history
    on_date = h[h["date"] == pd.Timestamp(cfg.valuation_date)].set_index("contract")["price"]
    diffs = [abs(on_date[q.contract] - q.price) for q in curve.quotes if not q.extrapolated and q.contract in on_date]
    rows.append(_row("Futures inputs vs OMIP history", f"{len(diffs)} matched, largest gap {max(diffs, default=0):.2f}",
                     len(diffs) > 0 and max(diffs) < 0.005))
    rows.append(_row("OMIP settlement days", f"{fwd['days']:,} since {fwd['first']}", fwd["days"] > 2000))

    y = next(r for r in fund["years"] if r["year"] == fund["day"]["year"])
    rows.append(_row(f"Fundamental model {y['year']}, hourly error vs month-mean",
                     f"{y['mae']:.1f} vs {y['month_mean_mae']:.1f} €/MWh", y["mae"] < y["month_mean_mae"]))
    eua = data.eua
    rows.append(_row("EUA auction days", f"{len(eua):,} since {eua.index.min():%Y}", len(eua) > 2500))

    for site in cfg.sites:
        w = data.weather(site)
        w = w.loc[: w.dropna().index.max()]  # ERA5 is published with a ~5 day lag
        full = pd.date_range(w.index.min(), w.index.max(), freq="h", tz="UTC")
        miss = int(w.reindex(full).isna().any(axis=1).sum())
        rows.append(_row(f"ERA5 {cfg.sites[site]['name']} gaps", f"{miss} h", miss == 0))
    return rows
