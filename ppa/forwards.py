"""Forward curve history: realised volatility, curve co-movement and a backtest of the PPA hedge.

Input is OMIP's daily German base futures reference prices (date, contract, price). Returns are
taken within each contract, never across a roll, and labelled with time to delivery.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import numpy as np
import pandas as pd

from .capture import TECH
from .hours import TZ, delivery

TRADING_DAYS = 252
BUCKETS = [(0.0, 1.0, "<1y"), (1.0, 2.0, "1–2y"), (2.0, 3.0, "2–3y"), (3.0, 10.0, "3y+")]


def clean(history: pd.DataFrame) -> pd.DataFrame:
    """Drop days on which no contract moved: exchange holidays republish the previous settlement."""
    wide = history.pivot(index="date", columns="contract", values="price").sort_index()
    moved = wide.diff().abs().sum(axis=1) > 0
    moved.iloc[0] = True
    return history[history["date"].isin(wide.index[moved])]


def returns(history: pd.DataFrame) -> pd.DataFrame:
    """Daily log returns per contract with product kind and years to delivery start."""
    h = clean(history).sort_values(["contract", "date"]).copy()
    h["ret"] = np.log(h["price"]).groupby(h["contract"]).diff()
    start = h["contract"].map(lambda c: pd.Timestamp(delivery(c)[0]))
    h["tau"] = (start - h["date"]).dt.days / 365.25
    h["kind"] = h["contract"].str.slice(0, 3).map(lambda k: "Cal" if k == "Cal" else ("Q" if k[0] == "Q" else "M"))
    return h[h["ret"].notna() & (h["tau"] > 0)]


def vol(r: pd.Series) -> float:
    return float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))


def term_structure(rets: pd.DataFrame, since: pd.Timestamp | None = None) -> list[dict]:
    """Annualised vol of calendar-year contracts by time to delivery: the Samuelson effect."""
    cal = rets[rets["kind"] == "Cal"]
    if since is not None:
        cal = cal[cal["date"] >= since]
    return [{"bucket": label, "vol": round(vol(cal[(cal["tau"] >= lo) & (cal["tau"] < hi)]["ret"]), 4)}
            for lo, hi, label in BUCKETS if ((cal["tau"] >= lo) & (cal["tau"] < hi)).sum() > 20]


def vol_curve(term: list[dict]) -> Callable[[np.ndarray], np.ndarray]:
    """The simulation's forward vol by years to delivery: flat within each bucket of `term` and beyond the last."""
    edge = {label: lo for lo, _, label in BUCKETS}
    lo = np.array([edge[b["bucket"]] for b in term])
    level = np.array([b["vol"] for b in term])
    return lambda tau: level[np.clip(np.searchsorted(lo, tau, side="right") - 1, 0, len(level) - 1)]


def next_calendar(history: pd.DataFrame) -> pd.Series:
    """Price of the next calendar-year contract on each day (Cal-27 during 2026, and so on)."""
    h = clean(history)
    want = "Cal-" + ((h["date"].dt.year + 1) % 100).astype(str).str.zfill(2)
    return h[h["contract"] == want].set_index("date")["price"].sort_index()


def curve_correlation(rets: pd.DataFrame, end: pd.Timestamp, months: int) -> float:
    """Correlation of daily returns of the next and the third calendar year: how one-factor the curve is."""
    r = rets[(rets["kind"] == "Cal") & (rets["date"] > end - pd.DateOffset(months=months)) & (rets["date"] <= end)]
    year = r["date"].dt.year
    lead = r["contract"].str.slice(4).astype(int) + 2000 - year
    wide = r.assign(lead=lead).pivot_table(index="date", columns="lead", values="ret")
    return float(wide[1].corr(wide[3]))


def hedge_backtest(history: pd.DataFrame, prices: pd.Series, gen: pd.DataFrame, capacity: pd.DataFrame,
                   tech: str, years: list[int], capacity_mw: float, lookback_months: int = 36) -> dict:
    """Realised P&L of a one-year pay-as-produced PPA bought at the end of October before delivery.

    The plant is the German fleet profile scaled to `capacity_mw`. The price is the Cal forward on the
    hedge date times the fleet capture rate of the preceding `lookback_months`, both known then. The
    PPA is held unhedged, hedged with P50-equivalent MW of Cal baseload (volume hedge), and hedged with
    that times the expected capture rate (value hedge, the dashboard's rule).
    """
    own, _ = TECH[tech]
    h = clean(history)
    local = gen.index.tz_convert(TZ).tz_localize(None)
    cap_mw = capacity[own].reindex(local.to_period("M").to_timestamp()).to_numpy() * 1000
    profile = pd.Series(gen[own].clip(lower=0).to_numpy() / cap_mw * capacity_mw, index=gen.index)
    df = pd.DataFrame({"g": profile, "p": prices}).dropna()
    year_of = df.index.tz_convert(TZ).year

    rows = []
    for y in years:
        hedge_date = h[h["date"] <= pd.Timestamp(y - 1, 10, 31)]["date"].max()
        quote = h[(h["date"] == hedge_date) & (h["contract"] == f"Cal-{y % 100:02d}")]["price"]
        past = df[(df.index < pd.Timestamp(hedge_date, tz=TZ)) &
                  (df.index >= pd.Timestamp(hedge_date, tz=TZ) - pd.DateOffset(months=lookback_months))]
        now = df[year_of == y]
        if quote.empty or len(now) < 8700 or len(past) < 8000:
            continue
        forward = float(quote.iloc[0])
        rate = float((past["g"] * past["p"]).sum() / past["g"].sum() / past["p"].mean())
        expected_mw = float(past["g"].mean())
        k = forward * rate
        hours = len(now)
        open_pnl = float((now["g"] * (now["p"] - k)).sum())
        spot = float(now["p"].mean())
        hedge = lambda mw, f=forward, s=spot, n=hours: mw * n * (f - s)  # noqa: E731, short baseload
        rows.append({
            "year": y, "hedge_date": hedge_date.date().isoformat(), "forward": round(forward, 2),
            "realised": round(spot, 2), "capture": round(rate, 3), "price": round(k, 2),
            "volume_mw": round(expected_mw, 2), "value_mw": round(expected_mw * rate, 2),
            "open": round(open_pnl / 1e6, 3),
            "volume_hedge": round((open_pnl + hedge(expected_mw)) / 1e6, 3),
            "value_hedge": round((open_pnl + hedge(expected_mw * rate)) / 1e6, 3),
        })
    sd = {k: round(float(np.std([r[k] for r in rows], ddof=1)), 3) for k in ("open", "volume_hedge", "value_hedge")}
    return {"rows": rows, "sd": sd}


def summary(history: pd.DataFrame, valuation: dt.date, months: int) -> dict:
    rets = returns(history)
    end = pd.Timestamp(valuation)
    cal = next_calendar(history)
    return {
        "first": history["date"].min().date().isoformat(),
        "days": int(clean(history)["date"].nunique()),
        "window_months": months,
        "term": term_structure(rets),
        "term_recent": term_structure(rets, end - pd.DateOffset(months=months)),
        "corr_1_3": round(curve_correlation(rets, end, months), 3),
        "next_calendar": {"dates": [d.date().isoformat() for d in cal.index], "values": cal.round(2).tolist()},
    }
