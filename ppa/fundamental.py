"""Fundamental model of the German day-ahead price: an hourly merit order calibrated on public data.

Each hour, demand served by the public grid (generation plus net imports plus net storage output) clears
against
    must-take  nuclear, hydro, biomass, waste and others as observed
    wind/solar the day-ahead forecast, scaled to the output it turned into in training hours that had no
               reason to curtail. Below zero, a block of each switches off at once (subsidised plants
               lose their premium when the price is negative, so their marketers bid at zero), more
               curtails gradually as prices fall, and the rest (rooftop solar, old fixed-tariff
               plants) runs at any price; wind and solar each have their own shares
    thermal    lignite, hard coal, gas and oil. A must-run part (heat-led CHP, inflexible units) runs at
               any price; the flexible rest bids fuel plus carbon over efficiency, spread across the
               fleet's efficiencies (Open Power System Data) and blurred by a calibrated bid noise.
               A fitted share of the flexible output is committed for the day: it follows the day's
               mean dispatch rather than the hour's price, so plants needed for the evening stay on
               through a cheap midday and cannot all ramp up for the peak, and it backs off only once
               the price falls below a fitted shut-down price
    trade      net imports, linear in German demand less must-take, in the wind and in the solar
               forecast separately (solar is shared with the neighbours, wind less so) and in the
               neighbours' residual load (their demand less nuclear, hydro, biomass, wind and solar),
               plus a bounded S-shaped response to the German price around a gas plant's cost, sized
               separately for imports and exports (at sunny middays the neighbours are flooded too,
               so extra exports are scarce),
               held within the export and import limits seen in training
    backstops  demand response that steps in at very high prices, and load and losses taken on at very
               negative ones (power-to-heat, extra pumping, plants running below cost), which keeps
               a surplus off the price floor
    storage    pumped hydro charges in the cheapest hours of each day and discharges in the dearest

Commitment and storage depend on the whole day's prices, so a day is cleared, committed and scheduled, and
cleared again until it settles.

Availability and must-run are read from observed dispatch, and the trade function is fitted to observed
trade and prices. Thirteen behavioural parameters (a markup per fuel, bid noise, curtailment of wind and
solar, the committed share and its shut-down price) are then fitted by clearing the market, to prices,
generation by fuel, wind, solar and trade together.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import ndtr

from .hours import TZ

VERSION = 3  # bump when the model changes, so build re-runs the cached backtest
FUELS = ["lignite", "coal", "gas", "oil"]
PLANT_SOURCE = {"lignite": "Lignite", "coal": "Hard coal", "gas": "Natural gas", "oil": "Oil"}
MUST_TAKE = ["nuclear", "ror", "reservoir", "biomass", "geothermal", "waste", "others", "coal_gas"]
VARIABLE = ["solar", "wind_onshore", "wind_offshore"]
OBSERVED = MUST_TAKE + FUELS + VARIABLE + ["pumped", "pumping", "trade"]

MMBTU_PER_MWH = 3.412
MWH_PER_TONNE_COAL = 6.978  # 6,000 kcal/kg
MWH_PER_BARREL = 1.70
FLOOR, CAP = -500.0, 4000.0
CCGT = 0.55  # efficiency of the gas plant whose cost centres the trade response
BINS = 8
MUST_RUN_QUANTILE = 0.02  # share of capacity running in a month's quietest hours
UNCURTAILED = 20.0  # EUR/MWh; above this, wind and solar have no reason to curtail
ZERO_WIDTH = 0.5  # EUR/MWh; spread of the bids at zero
SHUTDOWN_WIDTH = 10.0  # EUR/MWh; spread of committed plants' shut-down prices


# --- Inputs --------------------------------------------------------------------------------------


def inputs(mix: pd.DataFrame, forecasts: pd.DataFrame, prices: pd.Series, installed: pd.DataFrame,
           fuels: dict[str, pd.Series], eua: pd.Series, neighbours: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """One row per hour: what had to be served, what was available, what fuel cost, what happened."""
    df = mix.join(prices.rename("price"), how="inner").dropna(subset=["price"])
    df[OBSERVED] = df.reindex(columns=OBSERVED).fillna(0.0)
    local = df.index.tz_convert(TZ)
    month = local.tz_localize(None).to_period("M").to_timestamp()

    x = pd.DataFrame(index=df.index)
    x["price"] = df["price"]
    x["demand"] = df[MUST_TAKE + FUELS + VARIABLE].sum(axis=1) + df["trade"] + df["pumped"] + df["pumping"]
    x["must_take"] = df[MUST_TAKE].sum(axis=1)
    fc = forecasts.reindex(df.index)
    expected = {c: fc[c].fillna(df[c]) if c in fc else df[c] for c in VARIABLE}  # what the market cleared on
    x["solar_fc"] = expected["solar"]
    x["wind_fc"] = expected["wind_onshore"] + expected["wind_offshore"]
    x["solar"] = df["solar"]
    x["wind"] = df["wind_onshore"] + df["wind_offshore"]
    x["storage"] = df["pumped"] + df["pumping"]
    x["trade"] = df["trade"]
    x["abroad"] = neighbours.reindex(df.index).interpolate(limit=6).sum(axis=1, min_count=len(neighbours.columns))
    for f in FUELS:
        x[f] = df[f]

    cap = installed.reindex(month).ffill()
    for f in FUELS:
        x[f"cap_{f}"] = cap[f].to_numpy() * 1000

    monthly = fuel_prices(fuels)
    monthly = monthly.reindex(monthly.index.union(month.unique())).ffill()
    for f in ["gas", "coal", "oil"]:
        x[f"fuel_{f}"] = monthly[f].reindex(month).to_numpy()
    x["fuel_lignite"] = cfg["lignite_fuel"]
    days = pd.date_range(eua.index.min(), local.tz_localize(None).normalize().max(), freq="D")
    x["co2"] = eua.reindex(days).ffill().reindex(local.tz_localize(None).normalize()).to_numpy()

    x["month"] = local.month
    x["year"] = local.year
    x["day"] = (local.tz_localize(None).normalize() - pd.Timestamp("2000-01-01")).days
    return x.dropna(subset=["co2", "fuel_gas", "cap_gas", "abroad"])


def fuel_prices(fuels: dict[str, pd.Series]) -> pd.DataFrame:
    """Monthly gas, coal and oil in EUR/MWh thermal, from the IMF USD series and the monthly mean exchange rate."""
    usd = fuels["usd"].resample("MS").mean()
    return pd.DataFrame({
        "gas": fuels["gas"] * MMBTU_PER_MWH / usd,
        "coal": fuels["coal"] / usd / MWH_PER_TONNE_COAL,
        "oil": fuels["oil"] / usd / MWH_PER_BARREL,
    }).dropna()


def efficiencies(plants: pd.DataFrame) -> dict[str, np.ndarray]:
    """Capacity-weighted efficiency at BINS equally weighted quantiles of each fuel's fleet."""
    live = plants[plants["status"].isin(["operating", "reserve", "special_case", "shutdown_temporary"])]
    out = {}
    for f, source in PLANT_SOURCE.items():
        p = live[live["energy_source"] == source].dropna(subset=["capacity_net_bnetza", "efficiency_estimate"])
        order = np.argsort(p["efficiency_estimate"].to_numpy())
        eff = p["efficiency_estimate"].to_numpy()[order]
        w = np.cumsum(p["capacity_net_bnetza"].to_numpy()[order])
        w /= w[-1]
        out[f] = eff[np.searchsorted(w, (np.arange(BINS) + 0.5) / BINS)]
    return out


# --- Fleet and parameters ------------------------------------------------------------------------


@dataclass(frozen=True)
class Fleet:
    """What the training window says about the fleet, read from dispatch rather than fitted."""

    eff: dict[str, np.ndarray]  # (BINS,) per fuel
    avail: dict[str, np.ndarray]  # (12,) share of installed capacity that can run, by month
    must_run: dict[str, np.ndarray]  # (12,) share that runs at any price, by month
    trade: np.ndarray  # line coefficients, then import and export response (MW) and its spread
    trade_limits: tuple[float, float]  # export and import limit, MW
    pumped_mw: float
    level: dict[str, float]  # actual output per MWh of day-ahead forecast, for wind and solar


def fleet(x: pd.DataFrame, eff: dict[str, np.ndarray], cfg: dict) -> Fleet:
    avail, must_run = {}, {}
    for f in FUELS:
        share = (x[f] / x[f"cap_{f}"]).clip(0, 1)
        by_month = share.groupby(x["month"])
        # Seasonal maintenance shows in the monthly peak; a month that peaked low only because the fuel was
        # out of the money (gas in 2022) must not cap it, hence the floor at 85% of the window's peak.
        avail[f] = np.maximum(by_month.quantile(0.99).reindex(range(1, 13)).to_numpy(), 0.85 * share.quantile(0.995))
        must_run[f] = np.minimum(by_month.quantile(MUST_RUN_QUANTILE).reindex(range(1, 13)).to_numpy(), avail[f])
    limits = (float(x["trade"].quantile(0.002)), float(x["trade"].quantile(0.998)))
    # The forecast runs a few percent above what wind and solar turned out to produce (more so for solar), while
    # demand is built from actual output; unscaled, the gap would be a surplus at every sunny midday.
    free = x["price"] > UNCURTAILED
    level = {k: float(x.loc[free, k].sum() / x.loc[free, f"{k}_fc"].sum()) for k in ["wind", "solar"]}
    return Fleet(eff, avail, must_run, fit_trade(x, cfg), limits, float(x["storage"].abs().quantile(0.99)), level)


def trade_design(x: pd.DataFrame) -> np.ndarray:
    return np.column_stack([np.ones(len(x)), (x["demand"] - x["must_take"]).to_numpy(), x["wind_fc"].to_numpy(),
                            x["solar_fc"].to_numpy(), x["abroad"].to_numpy()])


def ccgt_cost(x: pd.DataFrame, cfg: dict) -> np.ndarray:
    gas = x["fuel_gas"].to_numpy() + cfg["emissions"]["gas"] * x["co2"].to_numpy()
    return gas / CCGT + cfg["vom"]["gas"]


def trade_at(design: np.ndarray, ccgt: np.ndarray, p: np.ndarray, coef: np.ndarray) -> np.ndarray:
    """Net imports: a line in the fundamentals plus a bounded response to price around a gas plant's cost."""
    k = design.shape[1]
    z = 2 * ndtr((p - ccgt) / (coef[k + 2] * ccgt)) - 1
    return design @ coef[:k] + np.where(z > 0, coef[k], coef[k + 1]) * z


def fit_trade(x: pd.DataFrame, cfg: dict) -> np.ndarray:
    """Line and price response fitted together on observed trade and prices, so neither double counts."""
    design, ccgt, p, y = trade_design(x), ccgt_cost(x, cfg), x["price"].to_numpy(), x["trade"].to_numpy()
    line = np.linalg.lstsq(design, y, rcond=None)[0]
    k = design.shape[1]
    lower = np.r_[np.full(k, -np.inf), 0.0, 0.0, 0.02]
    upper = np.r_[np.full(k, np.inf), 20000.0, 20000.0, 3.0]
    start = np.r_[line, 2000.0, 2000.0, 0.5]
    scale = np.r_[np.abs(line) + 1e-3, 1000.0, 1000.0, 0.1]
    return least_squares(lambda c: (trade_at(design, ccgt, p, c) - y) / 1000.0, start, bounds=(lower, upper),
                         x_scale=scale).x


PARAMS = ["markup_lignite", "markup_coal", "markup_gas", "markup_oil", "noise", "noise_floor", "wind_curtail",
          "solar_curtail", "curtail_scale", "wind_zero", "solar_zero", "committed", "shutdown"]
X0 = np.array([5.0, 5.0, 5.0, 10.0, 0.05, 5.0, 0.3, 0.05, 50.0, 0.05, 0.02, 0.3, -20.0])
LOWER = np.array([-40.0, -40.0, -40.0, -40.0, 0.0, 0.5, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0, -150.0])
UPPER = np.array([80.0, 80.0, 80.0, 150.0, 0.8, 60.0, 0.9, 0.9, 400.0, 0.5, 0.5, 0.95, 60.0])


class Market:
    """Supply curves for a block of hours, precomputed so that clearing is a vectorised bisection."""

    def __init__(self, x: pd.DataFrame, fl: Fleet, theta: np.ndarray, cfg: dict):
        self.theta = theta
        self.day = x["day"].to_numpy()
        self.share, self.shutdown = theta[11:13]
        self.committed = {f: np.zeros(len(x)) for f in FUELS}  # MW, set by commit()
        m = x["month"].to_numpy() - 1
        self.demand = x["demand"].to_numpy()
        self.must_take = x["must_take"].to_numpy()
        self.wind = x["wind_fc"].to_numpy() * fl.level["wind"]
        self.solar = x["solar_fc"].to_numpy() * fl.level["solar"]
        self.cost, self.sigma, self.mr, self.flex = {}, {}, {}, {}
        markup = dict(zip(FUELS, theta[:4], strict=True))
        for f in FUELS:
            heat = x[f"fuel_{f}"].to_numpy() + cfg["emissions"][f] * x["co2"].to_numpy()  # EUR/MWh thermal
            self.cost[f] = heat[:, None] / fl.eff[f][None, :] + cfg["vom"][f] + markup[f]
            self.sigma[f] = theta[4] * np.median(self.cost[f], axis=1, keepdims=True) + theta[5]
            cap = x[f"cap_{f}"].to_numpy()
            self.mr[f] = cap * fl.must_run[f][m]
            self.flex[f] = cap * (fl.avail[f][m] - fl.must_run[f][m])
        self.ccgt = ccgt_cost(x, cfg)
        self.design = trade_design(x)
        self.trade, self.trade_limits = fl.trade, fl.trade_limits
        self.response, self.absorb = cfg["demand_response"], cfg["surplus_response"]

    def merit(self, p: np.ndarray) -> dict[str, np.ndarray]:
        """Flexible thermal output if every plant followed the hour's price alone."""
        return {f: self.flex[f] * ndtr((p[:, None] - self.cost[f]) / self.sigma[f]).mean(axis=1) for f in FUELS}

    def commit(self, p: np.ndarray, damping: float = 0.5) -> None:
        """Move the committed output towards the share of each day's mean merit-order dispatch at prices p."""
        _, days = np.unique(self.day, return_inverse=True)
        for f, v in self.merit(p).items():
            target = self.share * (np.bincount(days, v) / np.bincount(days))[days]
            self.committed[f] = (1 - damping) * self.committed[f] + damping * target

    def dispatch(self, p: np.ndarray) -> dict[str, np.ndarray]:
        stay = ndtr((p - self.shutdown) / SHUTDOWN_WIDTH)
        out = {f: self.mr[f] + self.committed[f] * stay + (1 - self.share) * v for f, v in self.merit(p).items()}
        wind_curtail, solar_curtail, scale, wind_zero, solar_zero = self.theta[6:11]
        gradual = 1 - np.exp(np.minimum(p, 0) / scale)
        at_zero = ndtr(-p / ZERO_WIDTH)
        out["wind"] = self.wind * np.maximum(1 - wind_zero * at_zero - wind_curtail * gradual, 0)
        out["solar"] = self.solar * np.maximum(1 - solar_zero * at_zero - solar_curtail * gradual, 0)
        out["trade"] = np.clip(trade_at(self.design, self.ccgt, p, self.trade), *self.trade_limits)
        r, a = self.response, self.absorb
        out["demand_response"] = r["mw"] * ndtr((p - r["price"]) / r["spread"])
        out["surplus_response"] = -a["mw"] * ndtr((a["price"] - p) / a["spread"])
        return out

    def supply(self, p: np.ndarray) -> np.ndarray:
        return self.must_take + sum(self.dispatch(p).values())

    def clear(self, storage: np.ndarray, iterations: int = 32) -> np.ndarray:
        """Price at which supply plus net storage output meets demand, by bisection on [FLOOR, CAP]."""
        need = self.demand - storage
        lo, hi = np.full(len(need), FLOOR), np.full(len(need), CAP)
        for _ in range(iterations):
            mid = (lo + hi) / 2
            short = self.supply(mid) < need
            lo, hi = np.where(short, mid, lo), np.where(short, hi, mid)
        return (lo + hi) / 2


# --- Storage -------------------------------------------------------------------------------------


def arbitrage(price: np.ndarray, day: np.ndarray, power: np.ndarray, hours: float, eff: float) -> np.ndarray:
    """Net output of a store that charges in each day's cheapest hours and discharges in its dearest.

    Up to `hours` hours at full power each way, a pair of hours only while the spread covers the losses.
    """
    out = np.zeros(len(price))
    for d in np.unique(day):
        idx = np.flatnonzero(day == d)
        order = idx[np.argsort(price[idx])]
        k = int(min(hours, len(order) // 2))
        for i in range(k):
            cheap, dear = order[i], order[-1 - i]
            if price[dear] * eff <= price[cheap]:
                break
            out[cheap] -= power[cheap]
            out[dear] += power[dear] * eff
    return out


def solve(market: Market, x: pd.DataFrame, fl: Fleet, cfg: dict, storage: np.ndarray | None = None,
          rounds: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Clear, commit plants and schedule storage against those prices, clear again; damped so both settle.

    Given a storage schedule (observed, in calibration), only the commitment is iterated.
    """
    day = x["day"].to_numpy()
    pumped = np.full(len(x), fl.pumped_mw)
    scheduled = storage is None
    storage = np.zeros(len(x)) if scheduled else storage
    price = market.clear(storage)
    market.commit(price, damping=1.0)
    price = market.clear(storage)
    for _ in range(rounds):
        if scheduled:
            target = arbitrage(price, day, pumped, cfg["pumped_hours"], cfg["pumped_efficiency"])
            storage = 0.5 * storage + 0.5 * target
        market.commit(price)
        price = market.clear(storage)
    return price, storage


# --- Calibration and backtest --------------------------------------------------------------------


def fit(x: pd.DataFrame, fl: Fleet, cfg: dict, every: int = 6) -> np.ndarray:
    """Behavioural parameters that best reproduce prices, generation by fuel, wind, solar and trade together.

    Fitted on every `every`-th day, whole, since commitment depends on the day. Observed storage is taken as
    given so that the fit is about the merit order alone.
    """
    s = x[x["day"] % every == 0]
    storage = s["storage"].to_numpy()
    observed = {k: s[k].to_numpy() for k in FUELS + ["wind", "solar", "trade"]}
    price = s["price"].to_numpy()
    # Errors in proportion to the month's price level, so that 2022 does not drown out every other year.
    scale = 10.0 + 0.2 * s.groupby(["year", "month"])["price"].transform("mean").abs().to_numpy()

    def residuals(theta):
        market = Market(s, fl, theta, cfg)
        p, _ = solve(market, s, fl, cfg, storage, rounds=2)
        d = market.dispatch(p)
        return np.concatenate([(p - price) / scale] + [(d[k] - observed[k]) / 3000.0 for k in observed])

    return least_squares(residuals, X0, bounds=(LOWER, UPPER), loss="soft_l1", x_scale="jac", max_nfev=200).x


def _test_year(args) -> tuple[pd.DataFrame, np.ndarray]:
    train, test, eff, cfg = args
    fl = fleet(train, eff, cfg)
    theta = fit(train, fl, cfg)
    price, storage = solve(Market(test, fl, theta, cfg), test, fl, cfg)
    return pd.DataFrame({"model": price, "storage_model": storage}, index=test.index), theta


def backtest(x: pd.DataFrame, eff: dict[str, np.ndarray], cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each test year predicted by a model fitted only on the years before it; years run in parallel.

    Returns the hourly model price and storage output, and the fitted parameters by test year.
    """
    years = sorted(int(y) for y in x["year"].unique() if cfg["first_test_year"] <= y)
    jobs = [(x[(x["year"] >= y - cfg["train_years"]) & (x["year"] < y)], x[x["year"] == y], eff, cfg) for y in years]
    with ProcessPoolExecutor(min(len(jobs), 8)) as pool:
        results = list(pool.map(_test_year, jobs))
    hourly = pd.concat([r[0] for r in results])
    params = pd.DataFrame([r[1] for r in results], index=pd.Index(years, name="year"), columns=PARAMS)
    return hourly, params


def capture_rate(g: pd.DataFrame, price: str, weight: str) -> float:
    return float((g[price] * g[weight]).sum() / g[weight].sum() / g[price].mean())


def metrics(x: pd.DataFrame, model: pd.Series) -> pd.DataFrame:
    """Per year: level, hourly fit, a month-mean oracle as the bar to clear, capture rates, negative hours."""
    d = x.loc[model.index].assign(model=model)
    month_mean = d.groupby(["year", "month"])["price"].transform("mean")
    rows = []
    for y, g in d.groupby("year"):
        mm = month_mean[g.index]
        monthly = g.groupby("month")[["price", "model"]].mean()
        rows.append({
            "year": int(y), "price": g["price"].mean(), "model": g["model"].mean(),
            "mae": (g["model"] - g["price"]).abs().mean(), "month_mean_mae": (mm - g["price"]).abs().mean(),
            "corr": g[["model", "price"]].corr().iloc[0, 1],
            "monthly_mae": (monthly["model"] - monthly["price"]).abs().mean(),
            "solar_capture": capture_rate(g, "price", "solar"),
            "solar_capture_model": capture_rate(g, "model", "solar"),
            "wind_capture": capture_rate(g, "price", "wind"),
            "wind_capture_model": capture_rate(g, "model", "wind"),
            "negative_hours": int((g["price"] < 0).sum()), "negative_hours_model": int((g["model"] < 0).sum()),
        })
    return pd.DataFrame(rows).set_index("year")
