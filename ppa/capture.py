"""Fleet capture rates and the cannibalisation model.

Capture rate = generation-weighted day-ahead price / baseload price. Monthly capture rates are
regressed on log penetration (fleet generation / load) with month fixed effects over the trailing
36 months — the same window as the price shape, so the slope reflects today's storage and
curtailment rules rather than the 2015–22 regime. A linear slope forecasts one month ahead slightly
better but extrapolates to near-zero capture as capacity doubles; the log form builds in the
diminishing marginal cannibalisation that storage and curtailment produce. The slope turns a
capacity path into the extra capture discount beyond today's shape.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .hours import TZ

TECH = {"solar": ("solar", ["solar"]), "wind": ("wind_onshore", ["wind_onshore", "wind_offshore"])}


def fleet(prices: pd.Series, gen: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    """Per local month ("M") or year ("Y"): baseload, capture rates, penetration, negative hours."""
    df = gen.join(prices.rename("price"), how="inner").dropna(subset=["price", "load"])
    period = df.index.tz_convert(TZ).tz_localize(None).to_period(freq)
    out = {}
    for tech, (own, share_cols) in TECH.items():
        g = df[own].clip(lower=0)
        paid = g * (df["price"] >= 0)
        out[f"{tech}_capture"] = (g * df["price"]).groupby(period).sum() / g.groupby(period).sum()
        out[f"{tech}_capture_paid"] = (paid * df["price"]).groupby(period).sum() / paid.groupby(period).sum()
        out[f"{tech}_unpaid"] = 1 - paid.groupby(period).sum() / g.groupby(period).sum()
        out[f"{tech}_share"] = df[share_cols].sum(axis=1).groupby(period).sum() / df["load"].groupby(period).sum()
    out["base"] = df["price"].groupby(period).mean()
    out["negative_hours"] = (df["price"] < 0).groupby(period).sum()
    out["hours"] = df["price"].groupby(period).count()
    m = pd.DataFrame(out)
    for tech in TECH:
        m[f"{tech}_rate"] = m[f"{tech}_capture"] / m["base"]
        m[f"{tech}_rate_paid"] = m[f"{tech}_capture_paid"] / m["base"]
    return m[m["hours"] > (600 if freq == "M" else 0)]  # drop incomplete months


LOOKBACK = 36


@dataclass(frozen=True)
class Model:
    alpha: np.ndarray  # month intercepts (12,)
    beta: float  # capture rate per unit of log penetration
    se: float = 0.0  # standard error of beta

    def __call__(self, month: np.ndarray, share: np.ndarray) -> np.ndarray:
        return self.alpha[month - 1] + self.beta * np.log(share)


def fit(monthly: pd.DataFrame, tech: str, upto: pd.Period | None = None, lookback: int = LOOKBACK,
        target: str = "rate") -> Model:
    """`target` on log penetration with month fixed effects: "rate" is the capture rate over all hours,
    "rate_paid" over hours at or above zero, "unpaid" the share of output at negative prices."""
    m = monthly if upto is None else monthly[monthly.index <= upto]
    m = m.iloc[-lookback:]
    design = np.column_stack([np.eye(12)[m.index.month.to_numpy() - 1], np.log(m[f"{tech}_share"].to_numpy())])
    rate = m[f"{tech}_{target}"].to_numpy()
    coef, *_ = np.linalg.lstsq(design, rate, rcond=None)
    residual = rate - design @ coef
    variance = residual @ residual / max(len(rate) - design.shape[1], 1)
    se = np.sqrt(variance * np.linalg.pinv(design.T @ design)[12, 12])
    return Model(coef[:12], float(coef[12]), float(se))


def backtest(monthly: pd.DataFrame, tech: str, start: pd.Period) -> dict:
    """Rolling one-month-ahead forecasts from `start`: each month predicted by a model fitted on the
    36 months before it, given that month's realised penetration. The benchmark is the same
    calendar month's average capture rate over the previous three years."""
    months, actual, predicted, naive = [], [], [], []
    for target in monthly.index[monthly.index >= start]:
        past = monthly[monthly.index < target]
        row = monthly.loc[target]
        model = fit(past, tech)
        months.append(str(target))
        actual.append(row[f"{tech}_rate"])
        predicted.append(float(model(np.array([target.month]), np.array([row[f"{tech}_share"]]))[0]))
        naive.append(past[past.index.month == target.month][f"{tech}_rate"].iloc[-3:].mean())
    actual, predicted, naive = map(np.array, (actual, predicted, naive))
    return {
        "months": months,
        "actual": actual.round(4).tolist(),
        "predicted": predicted.round(4).tolist(),
        "mae": round(float(np.mean(np.abs(predicted - actual))), 4),
        "naive_mae": round(float(np.mean(np.abs(naive - actual))), 4),
    }


def year_ahead(monthly: pd.DataFrame, tech: str, upto: pd.Period) -> dict:
    """How far the capture rate of a calendar year lands from the model fitted on the 36 months before it.

    Each year is predicted given its realised penetration, so the error is what penetration does not
    explain: weather, fuel prices and rule changes. Monthly errors are weighted by share and price. `sd` is
    the root mean square error and `rho` its correlation from one year to the next; together they are the
    year-to-year capture risk the valuation simulates, beside the slope's standard error.
    """
    m = monthly[monthly.index <= upto]
    years, errors = [], []
    for year in sorted(set(m.index.year)):
        past, now = m[m.index.year < year], m[m.index.year == year]
        if len(past) < LOOKBACK or len(now) < 9:
            continue
        predicted = fit(past, tech)(now.index.month.to_numpy(), now[f"{tech}_share"].to_numpy())
        weight = now[f"{tech}_share"] * now["base"]
        years.append(int(year))
        errors.append(float(((now[f"{tech}_rate"] - predicted) * weight).sum() / weight.sum()))
    e = np.array(errors)
    rho = float(np.clip(np.corrcoef(e[:-1], e[1:])[0, 1], 0.0, 0.9)) if len(e) > 2 else 0.0
    return {"years": years, "errors": e.round(4).tolist(), "sd": float(np.sqrt(np.mean(e**2))), "rho": rho}


def penetration_path(monthly: pd.DataFrame, capacity: pd.DataFrame, tech: str, window: tuple[dt.date, dt.date],
                     path: dict, load_growth: float = 0.0) -> tuple[pd.Series, pd.DataFrame]:
    """Window-average penetration by month, and its projection: installed capacity over load.

    Capacity follows `path` (GW by delivery year) and load grows by `load_growth` a year from the middle of
    the window, so the same build-out is a smaller share of a larger market.
    """
    lo, hi = pd.Period(window[0], "M"), pd.Period(window[1], "M")
    w = monthly[(monthly.index >= lo) & (monthly.index <= hi)]
    today = w[f"{tech}_share"].groupby(w.index.month).mean()
    cols = ["solar"] if tech == "solar" else ["wind_onshore", "wind_offshore"]
    cap = capacity[cols].sum(axis=1)
    cap = cap[(cap.index >= lo.to_timestamp()) & (cap.index <= hi.to_timestamp())].mean()
    middle = (lo.year + (lo.month - 1) / 12 + hi.year + hi.month / 12) / 2
    future = pd.DataFrame({int(y): today * gw / cap / (1 + load_growth) ** (int(y) + 0.5 - middle)
                           for y, gw in path.items()})
    return today, future


def cannibalisation(model: Model, today: pd.Series, future: pd.DataFrame) -> pd.DataFrame:
    """Change in capture rate vs today's shape, by month (rows) and delivery year (columns)."""
    months = today.index.to_numpy()
    base = model(months, today.to_numpy())
    return pd.DataFrame({y: model(months, future[y].to_numpy()) - base for y in future}, index=today.index)


def paid_volume(unpaid: Model, monthly: pd.DataFrame, tech: str, window: tuple[dt.date, dt.date],
                today: pd.Series, future: pd.DataFrame) -> pd.DataFrame:
    """Output paid under a negative-price clause relative to today's, by month and delivery year.

    The share of output at negative prices follows its own regression on penetration, so it grows with
    the build-out: (1 - share then) / (1 - share today).
    """
    lo, hi = pd.Period(window[0], "M"), pd.Period(window[1], "M")
    w = monthly[(monthly.index >= lo) & (monthly.index <= hi)]
    now = w[f"{tech}_unpaid"].groupby(w.index.month).mean()
    then = cannibalisation(unpaid, today, future).add(now, axis=0).clip(0.0, 0.95)
    return (1 - then).div(1 - now, axis=0)


def slope_error(model: Model, today: pd.Series, future: pd.DataFrame) -> pd.DataFrame:
    """One standard error of the slope as a capture-rate change, by month and delivery year.

    The slope is the one number that turns a capacity path into a capture rate, and 36 months pin it
    down only loosely. Its error is the same in every delivery year and grows with the build-out.
    """
    return cannibalisation(Model(np.zeros(12), model.se), today, future)
