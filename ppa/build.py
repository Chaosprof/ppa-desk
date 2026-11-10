"""Run every model and write site/data.json and MEMO.md."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time

import numpy as np
import pandas as pd

from . import (
    capture,
    fairvalue,
    forwards,
    fundamental,
    generation,
    hpfc,
    options,
    quality,
    simulate,
    sources,
    valuation,
)
from .config import HISTORY_START, ROOT, Config, load
from .dataset import Dataset
from .hours import TZ, features, hours

TECHS = ["solar", "wind"]
BACKTEST_START = pd.Period("2021-01", "M")
HEADLINE = {"solar": "south", "wind": "north"}
WINDOWS = [24, 36, 48, 60]  # months of history the cannibalisation slope is refitted on, to show what the choice moves


def run(cfg: Config | None = None, log=print) -> dict:
    cfg = cfg or load()
    data = Dataset(cfg)
    t0 = time.time()
    say = lambda m: log(f"{time.time() - t0:6.1f}s  {m}")  # noqa: E731

    say("fetch")
    data.fetch_all(lambda m: None)

    say("hpfc")
    shape = hpfc.learn_shape(data.prices, cfg.window)
    quotes = hpfc.quotes_from(cfg["futures"], cfg.years)
    curve = hpfc.build(quotes, shape, end=dt.date(cfg.years[-1] + 1, 1, 1))

    say("capture")
    monthly = capture.fleet(data.prices, data.generation, "M")
    annual = capture.fleet(data.prices, data.generation, "Y")
    cannib, cannib_sd, capture_out = {}, {}, {}
    paid_cannib, paid_cannib_sd, paid_factor, refits = {}, {}, {}, {}
    for tech in TECHS:
        model = capture.fit(monthly, tech, pd.Period(cfg.window[1], "M"))
        today, future = capture.penetration_path(monthly, data.capacity, tech, cfg.window, cfg["capacity"][tech],
                                                 cfg["capacity"]["load_growth"])
        ahead = capture.year_ahead(monthly, tech, pd.Period(cfg.window[1], "M"))
        cannib[tech] = capture.cannibalisation(model, today, future)
        cannib_sd[tech] = capture.slope_error(model, today, future)
        refits[tech] = []
        for months in WINDOWS:
            alt = capture.fit(monthly, tech, pd.Period(cfg.window[1], "M"), lookback=months)
            refits[tech].append((months, alt, capture.cannibalisation(alt, today, future),
                                 capture.slope_error(alt, today, future)))
        paid = capture.fit(monthly, tech, pd.Period(cfg.window[1], "M"), target="rate_paid")
        unpaid = capture.fit(monthly, tech, pd.Period(cfg.window[1], "M"), target="unpaid")
        paid_cannib[tech] = capture.cannibalisation(paid, today, future)
        paid_cannib_sd[tech] = capture.slope_error(paid, today, future)
        paid_factor[tech] = capture.paid_volume(unpaid, monthly, tech, cfg.window, today, future)
        def fleet_rate(share, model=model, today=today):  # generation-weighted over months
            return round(float((model(today.index.to_numpy(), share.to_numpy()) * today / today.sum()).sum()), 4)

        capture_out[tech] = {
            "backtest": capture.backtest(monthly, tech, BACKTEST_START),
            "beta": round(model.beta, 4),
            "beta_se": round(model.se, 4),
            "paid": {"beta": round(paid.beta, 4), "beta_se": round(paid.se, 4)},
            "unpaid": {"beta": round(unpaid.beta, 4), "beta_se": round(unpaid.se, 4)},
            "year_ahead": {**ahead, "sd": round(ahead["sd"], 4), "rho": round(ahead["rho"], 3)},
            "today": fleet_rate(today),
            "projection": {str(y): fleet_rate(future[y]) for y in future},
        }

    say("forwards")
    fwd = forwards.summary(data.omip_history, cfg.valuation_date, cfg["forwards"]["vol_window_months"])
    vol = forwards.vol_curve(fwd["term_recent"])
    complete = [y for y in range(int(fwd["first"][:4]) + 1, cfg.valuation_date.year)]
    fwd["backtest"] = {tech: forwards.hedge_backtest(data.omip_history, data.prices, data.generation, data.capacity,
                                                     tech, complete, cfg["capacity_mw"]) for tech in TECHS}

    say("fundamental")
    fund, fund_x, fund_params = _fundamental(data, cfg)

    say("fair value")
    fair = _fair_value(data, cfg, fund_x, fund_params)

    say("balancing")
    balancing = _balancing(data, cfg)
    rates = data.discount_curve

    scenarios, yields, battery = {}, {}, None
    terms = cfg["commercial"]
    ones = pd.DataFrame(1.0, index=range(1, 13), columns=cfg.years)
    for tech in TECHS:
        scenarios[tech], yields[tech] = {}, {}
        ahead = capture_out[tech]["year_ahead"]
        for site, s in cfg.sites.items():
            say(f"simulate {tech} {site}")
            cf = generation.capacity_factor(tech, data.weather(site), s, cfg["technology"][tech])
            stats = generation.yield_stats(cf, cfg["yield"]["model_uncertainty"][tech])
            yields[tech][site] = stats
            pool = simulate.pool(data.prices, cf, cfg.window)
            sims = simulate.run(curve, shape, pool, cfg.years, np.array(stats["flh"]), cfg.valuation_date,
                                cfg["capacity_mw"], vol,
                                cfg["yield"]["model_uncertainty"][tech], cfg["risk"]["paths"], cfg["risk"]["seed"],
                                cfg["technology"][tech].get("degradation", 0.0))
            df = valuation.discount_factors(rates, np.array([y.tau for y in sims]))
            cost = valuation.hedge_cost(sims, cfg.valuation_date, vol, terms)
            paid_adj = valuation.cannibalisation_adj(sims, paid_cannib[tech], paid=True)
            paid_adj_sd = valuation.cannibalisation_adj(sims, paid_cannib_sd[tech], paid=True)
            paid = valuation.paid_volume(sims, paid_factor[tech])
            year_sd = ahead["sd"] * valuation.cannibalisation_adj(sims, ones)  # a capture-rate error in EUR/MWh
            paid_year_sd = ahead["sd"] * valuation.cannibalisation_adj(sims, ones, paid=True)

            def price(n, change, error, sims=sims, df=df, tech=tech, cost=cost, year_sd=year_sd, rho=ahead["rho"],
                      **clause):
                """The first n years, given the capture-rate change and one slope error by month and year."""
                adj, adj_sd = (valuation.cannibalisation_adj(sims, t)[:n] for t in (change, error))
                return valuation.value(sims[:n], df[:n], adj, adj_sd, balancing[tech], cfg["risk"]["volume_quantile"],
                                       year_sd=year_sd[:n], rho=rho, cost=cost[:n], credit=terms["credit"],
                                       origin=terms["origin"], **{k: v[:n] for k, v in clause.items()})

            scenarios[tech][site] = [
                price(n, cannib[tech], cannib_sd[tech], paid_adj=paid_adj, paid_adj_sd=paid_adj_sd, paid=paid,
                      paid_year_sd=paid_year_sd)
                for n in range(1, len(sims) + 1)
            ]
            if site == HEADLINE[tech]:  # the headline price again, with the slope fitted on other windows
                capture_out[tech]["windows"] = [
                    {"months": m, "beta": round(alt.beta, 4), "beta_se": round(alt.se, 4),
                     "price": price(len(sims), c, sd)["price"]}
                    for m, alt, c, sd in refits[tech]]
            if tech == "solar" and site == HEADLINE["solar"]:
                battery = _battery(sims[0], curve)

    say("options")
    gas = fundamental.fuel_prices(data.fuels)["gas"]
    estimated = forwards.spark_inputs(data.omip_history, data.eua, gas, cfg.valuation_date,
                                      cfg["forwards"]["vol_window_months"])
    opts = {"spark": _spark({**cfg["spark"], **estimated}, cfg.valuation_date), "battery": battery}

    say("quality")
    checks = quality.run(data, curve, HISTORY_START, fwd, fund)

    out = {
        "meta": {
            "valuation_date": cfg.valuation_date.isoformat(),
            "ppa_start": cfg.ppa_start.isoformat(),
            "capacity_mw": cfg["capacity_mw"],
            "years": cfg.years,
            "sites": {k: v["name"] for k, v in cfg.sites.items()},
            "headline": HEADLINE,
            "paths": cfg["risk"]["paths"],
            "futures_source": cfg["futures"]["source"],
            "built": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "sources": SOURCES,
            "assumptions": _assumptions(cfg, rates, fwd, capture_out),
        },
        "scenarios": scenarios,
        "yield": yields,
        "curve": _curve(curve, cfg),
        "forwards": fwd,
        "capture": {"annual": _annual(annual), **capture_out},
        "balancing": {k: round(v, 2) for k, v in balancing.items()},
        "options": opts,
        "fundamental": fund,
        "fair_value": fair,
        "quality": checks,
    }
    (ROOT / "site" / "data.json").write_text(json.dumps(out, separators=(",", ":"), allow_nan=False))
    (ROOT / "MEMO.md").write_text(memo(out))
    say("done")
    return out


SOURCES = [
    {"name": "OMIP", "use": "German base futures, daily since 2016", "url": "https://www.omip.pt/en/dados-mercado"},
    {"name": "Energy-Charts", "use": "Day-ahead prices, generation by fuel, trade, forecasts, capacity, neighbours",
     "url": "https://api.energy-charts.info"},
    {"name": "EEX", "use": "EU carbon auction prices since 2012",
     "url": "https://www.eex.com/en/market-data/environmentals/eu-ets-auctions"},
    {"name": "FRED", "use": "IMF monthly gas, coal and oil prices; USD/EUR", "url": "https://fred.stlouisfed.org"},
    {"name": "Open Power System Data", "use": "German plant list with efficiencies",
     "url": "https://data.open-power-system-data.org/conventional_power_plants/"},
    {"name": "SMARD", "use": "Price and solar cross-check", "url": "https://www.smard.de"},
    {"name": "Open-Meteo ERA5", "use": "Site weather 2005–2026", "url": "https://open-meteo.com/en/docs/historical-weather-api"},
    {"name": "ECB", "use": "AAA euro discount curve", "url": "https://data.ecb.europa.eu"},
]


def _assumptions(cfg: Config, rates: pd.Series, fwd: dict, captured: dict) -> list[dict]:
    r, b, c = cfg["risk"], cfg["balancing"], cfg["commercial"]
    lo, hi = cfg.window
    solar, wind = captured["solar"], captured["wind"]
    slope = f"{solar['beta']:.2f} ± {solar['beta_se']:.2f} per log share".replace("-", "−")
    term = fwd["term_recent"]
    return [
        {"name": "Shape window", "value": f"{lo:%b %Y} – {hi:%b %Y}"},
        {"name": "Forward vol", "value": f"{term[0]['vol']:.0%} to {term[-1]['vol']:.0%} by time to delivery, "
                                         f"realised over {fwd['window_months']} months"},
        {"name": "Risk premium", "value": f"P{round(100 - 100 * r['volume_quantile'])} break-even"},
        {"name": "Solar cannibalisation slope", "value": slope},
        {"name": "Capture rate, error a year ahead", "value": f"solar {solar['year_ahead']['sd']:.2f}, "
                                                              f"wind {wind['year_ahead']['sd']:.2f}"},
        {"name": "Load growth", "value": f"{cfg['capacity']['load_growth']:.1%} a year"},
        {"name": "Solar degradation", "value": f"{cfg['technology']['solar']['degradation']:.1%} a year"},
        {"name": "Bid-offer, half", "value": f"€{c['bid_offer']['liquid']:.2f}/MWh for {c['liquid_years']} years, "
                                             f"€{c['bid_offer']['illiquid']:.2f} beyond"},
        {"name": "Initial margin", "value": f"{c['margin']['confidence']:.0%} over {c['margin']['holding_days']} days, "
                                            f"funded at {c['margin']['funding_spread']:.1%}"},
        {"name": "Generator default", "value": f"{c['credit']['hazard']:.1%} a year, "
                                               f"{c['credit']['recovery']:.0%} recovered"},
        {"name": "Guarantees of origin", "value": f"€{c['origin']:.2f}/MWh"},
        {"name": "Imbalance spread", "value": f"€{b['imbalance_spread']:.0f}/MWh × {b['single_asset_factor']}"},
        {"name": "Discount rate, 5y", "value": f"{rates.loc[5]:.2%}"},
        {"name": "Solar 2030", "value": f"{cfg['capacity']['solar']['2030']} GW"},
        {"name": "Wind 2030", "value": f"{cfg['capacity']['wind']['2030']} GW"},
        {"name": "Paths", "value": f"{r['paths']}"},
    ]


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:10]


def _backtest(x: pd.DataFrame, data: Dataset, cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly backtest prices and the parameters fitted for each test year, cached together in data/."""
    fc = cfg["fundamental"]
    key = _key(fc, fundamental.VERSION)
    hourly = sources.DATA / f"fundamental_{cfg.history_end}_{key}.parquet"
    params = sources.DATA / f"fundamental_{cfg.history_end}_{key}_params.parquet"
    if not (hourly.exists() and params.exists()):
        h, p = fundamental.backtest(x, fundamental.efficiencies(data.plants), fc)
        h.to_parquet(hourly)
        p.to_parquet(params)
    return pd.read_parquet(hourly), pd.read_parquet(params)


def _fair_value(data: Dataset, cfg: Config, x: pd.DataFrame, params: pd.DataFrame) -> dict:
    """Monthly fundamental fair value of the next calendar year against its futures, and the gap as a signal."""
    fc = cfg["fundamental"]
    history = forwards.clean(data.omip_history)
    cal = forwards.next_calendar(data.omip_history)
    futures = fairvalue.month_ends(cal, pd.Timestamp(int(params.index.min()), 1, 1), pd.Timestamp(cfg.valuation_date))
    fv = sources._cached(
        f"fairvalue_{cfg.history_end}_{_key(fc, fundamental.VERSION, fairvalue.VERSION)}",
        lambda: fairvalue.run(x, data.mix["nuclear"], data.installed, data.fuels, data.eua, params,
                              fundamental.efficiencies(data.plants), futures, fc))
    as_of = fv.index[-1]

    def today(run):
        """`run` on the latest scenario: the last twelve months at that day's fuel, fleet and parameters."""
        year, eff = as_of.year, fundamental.efficiencies(data.plants)
        fl = fundamental.fleet(x[(x["year"] >= year - fc["train_years"]) & (x["year"] < year)], eff, fc)
        s = fairvalue.scenario(fairvalue.trailing(x, as_of), data.mix["nuclear"], data.installed,
                               fundamental.fuel_prices(data.fuels), data.eua, as_of, fc)
        return run(s, fl, params.loc[year].to_numpy(), fc)

    key = f"{cfg.history_end}_{_key(fc, fundamental.VERSION, fairvalue.VERSION)}"
    sens = sources._cached(f"sensitivity_{key}", lambda: today(fairvalue.sensitivities))
    drivers = sources._cached(f"drivers_{key}", lambda: today(fairvalue.drivers))
    buildout = sources._cached(f"buildout_{key}", lambda: today(fairvalue.buildout))
    change = fairvalue.next_change(history, fv)
    gap = fairvalue.signal_stats(fv["gap"], change)
    momentum = fairvalue.signal_stats(change.shift(1), change)
    always = fairvalue.signal_stats(pd.Series(1.0, index=change.index), change)
    stats = lambda st: {k: None if np.isnan(v) else round(v, 3) for k, v in st.items() if k != "cumulative"}  # noqa: E731
    years = fairvalue.delivery_years(fv, data.prices)
    last = fv.iloc[-1]
    return {
        "dates": [d.date().isoformat() for d in fv.index],
        "futures": fv["futures"].round(2).tolist(),
        "fair_value": fv["fair_value"].round(2).tolist(),
        "today": {"date": fv.index[-1].date().isoformat(), "contract": last["contract"],
                  "futures": round(float(last["futures"]), 2), "fair_value": round(float(last["fair_value"]), 2),
                  "gap": round(float(last["gap"]), 2),
                  # the model's gas is a month old; the fair value at the quote the spark spread uses
                  "gas_quote": cfg["spark"]["gas"],
                  "at_quote": round(float(last["fair_value"] + sens.at["gas", "base"]
                                          * (cfg["spark"]["gas"] - sens.at["gas", "input"])), 2)},
        "drivers": [{"source": k, "share": round(float(r["share"]), 3),
                     "low": None if pd.isna(r["low"]) else round(float(r["low"]), 1),
                     "high": None if pd.isna(r["high"]) else round(float(r["high"]), 1)}
                    for k, r in drivers.iterrows()],
        "buildout": {k: {"rate": round(float(r["rate"]), 3), "slope": round(float(r["slope"]), 3)}
                     for k, r in buildout.iterrows()},
        "sensitivity": [{"input": k, "level": round(float(r["input"]), 2), "base": round(float(r["base"]), 3),
                         "solar": round(float(r["solar"]), 3), "wind": round(float(r["wind"]), 3)}
                        for k, r in sens.iterrows()],
        "signal": {"gap": stats(gap), "momentum": stats(momentum), "long": stats(always)},
        "cumulative": {"dates": [d.date().isoformat() for d in gap["cumulative"].index],
                       "values": gap["cumulative"].round(2).tolist()},
        "years": [{"year": int(y), "futures": round(float(r["futures"]), 2),
                   "fair_value": round(float(r["fair_value"]), 2),
                   "realised": None if pd.isna(r["realised"]) else round(float(r["realised"]), 2)}
                  for y, r in years.iterrows()],
    }


def _fundamental(data: Dataset, cfg: Config) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Merit-order backtest: each year predicted from a fit on the years before it. Cached; fitting takes minutes.

    Returns the site's numbers, the hourly model inputs and the fitted parameters by test year.
    """
    fc = cfg["fundamental"]
    x = fundamental.inputs(data.mix, data.da_forecasts, data.prices, data.installed, data.fuels, data.eua,
                           data.neighbours, fc)
    hourly, params = _backtest(x, data, cfg)
    model = hourly["model"]
    m = fundamental.metrics(x, model)
    d = x.loc[model.index, ["price"]].assign(model=model)
    local = d.index.tz_convert(TZ)
    monthly = d.groupby(local.strftime("%Y-%m"))[["price", "model"]].mean()
    full = int(max(y for y, g in d.groupby(local.year) if len(g) > 8700))
    day = d[local.year == full].groupby(local[local.year == full].hour)[["price", "model"]].mean()
    return {
        "train_years": fc["train_years"],
        "years": [{"year": int(y), **{k: round(float(v), 3) for k, v in r.items()}} for y, r in m.iterrows()],
        "monthly": {"months": monthly.index.tolist(), "actual": monthly["price"].round(2).tolist(),
                    "model": monthly["model"].round(2).tolist()},
        "day": {"year": full, "actual": day["price"].round(2).tolist(), "model": day["model"].round(2).tolist()},
    }, x, params


def _balancing(data: Dataset, cfg: Config) -> dict[str, float]:
    """EUR/MWh: fleet day-ahead forecast error share x single-site factor x imbalance spread."""
    lo, hi = cfg.window
    idx = hours(lo, hi + dt.timedelta(days=1))
    out = {}
    for tech, col in [("solar", "solar"), ("wind", "wind_onshore")]:
        both = pd.concat([data.generation[col], data.forecasts[col]], axis=1).reindex(idx).dropna()
        share = (both.iloc[:, 0] - both.iloc[:, 1]).abs().sum() / both.iloc[:, 0].sum()
        b = cfg["balancing"]
        out[tech] = float(share * b["single_asset_factor"] * b["imbalance_spread"])
    return out


def _spark(s: dict, valuation: dt.date) -> dict:
    """Spread option on the contract in `s`, at its estimated vols and correlations, and across the power–gas
    correlation (the estimate included) as far as the three correlations stay consistent."""
    lo, _ = hpfc.delivery(s["contract"])
    t = (lo - valuation).days / 365.25
    mc, se = options.spark_mc(s, t)
    sweep = []
    for rho in sorted({0.0, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95, s["corr"]["power_gas"]}):
        v = {**s, "corr": {**s["corr"], "power_gas": rho}}
        if not options.consistent(v["corr"]):
            continue
        m, e = options.spark_mc(v, t, n=200_000)
        sweep.append({"rho": rho, "kirk": round(options.spark_kirk(v, t), 3), "mc": round(m, 3), "se": round(e, 3)})
    intrinsic = s["power"] - s["heat_rate"] * s["gas"] - s["emission_factor"] * s["carbon"] - s["vom"]
    return {"contract": s["contract"], "expiry_years": round(t, 4), "intrinsic": round(intrinsic, 3),
            "kirk": round(options.spark_kirk(s, t), 3), "mc": round(mc, 3), "se": round(se, 3), "sweep": sweep,
            "inputs": {k: s[k] for k in ["power", "gas", "carbon", "heat_rate", "emission_factor", "vom"]},
            "vol": s["vol"], "corr": s["corr"]}


def _battery(first: simulate.YearSim, curve: hpfc.Curve, cycles: int = 250) -> dict:
    """1 MW / 2 MWh battery, at most `cycles` cycles a year, on the first delivery year's prices."""
    spreads = first.daily_spread
    lsmc, foresight = options.swing_lsmc(spreads, cycles)
    F = curve.hourly[hours(dt.date(first.year, 1, 1), dt.date(first.year + 1, 1, 1))]
    _, day_of = np.unique(F.index.tz_convert(TZ).date, return_inverse=True)
    on_curve = simulate.battery_spread(F.to_numpy()[None, :], day_of, day_of.max() + 1)[0]
    intrinsic = float(np.sort(np.maximum(on_curve, 0))[-cycles:].sum())
    return {"year": first.year, "cycles": cycles, "intrinsic": round(intrinsic / 1e3, 2),
            "lsmc": round(lsmc / 1e3, 2), "foresight": round(foresight / 1e3, 2)}


def _curve(curve: hpfc.Curve, cfg: Config) -> dict:
    h = curve.hourly
    local = h.index.tz_convert(TZ)
    daily = h.groupby(local.date).mean()
    f = features(h.index)
    cal = f["year"] == cfg.years[0]
    heat = h[cal.to_numpy()].groupby([f.loc[cal, "month"], f.loc[cal, "hour"]]).mean().unstack()
    last_quote = max(q.period[1] for q in curve.quotes if not q.extrapolated)
    # Local date x local hour: the repeated hour of a 25-hour day is averaged, the skipped one of a 23-hour day is null.
    tenor = (f["year"] >= cfg.years[0]) & (f["year"] <= cfg.years[-1])
    grid = h[tenor.to_numpy()].groupby([f.loc[tenor, "date"], f.loc[tenor, "hour"]]).mean().unstack()
    grid = grid.reindex(columns=range(24)).round(1)
    return {
        "daily": {"start": str(daily.index[0]), "values": daily.round(2).tolist()},
        "hourly": {"start": str(grid.index[0]),
                   "values": [[None if np.isnan(v) else float(v) for v in row] for row in grid.to_numpy()]},
        "last_quoted": last_quote.isoformat(),
        "quotes": [{"contract": q.contract, "start": q.period[0].isoformat(), "end": q.period[1].isoformat(),
                    "price": q.price, "model": round(curve.reprice(q), 4), "extrapolated": q.extrapolated}
                   for q in curve.quotes],
        "profile": {"year": cfg.years[0], "values": heat.round(2).values.tolist()},
    }


def _annual(annual: pd.DataFrame) -> list[dict]:
    rows = []
    for period, r in annual.iterrows():
        rows.append({"year": period.year, "base": round(r["base"], 2), "solar": round(r["solar_rate"], 4),
                     "wind": round(r["wind_rate"], 4), "negative_hours": int(r["negative_hours"]),
                     "complete": bool(r["hours"] > 8700)})
    return rows


def memo(out: dict) -> str:
    """One page: the number, what it is made of, and the hedge."""
    meta = out["meta"]
    lines = [f"# German PPA pricing — {meta['valuation_date']}", ""]
    names = {"base": "Baseload forward", "profile": "Profile", "correlation": "Price–volume correlation",
             "cannibalisation": "Cannibalisation", "risk": "Risk premium (P90)", "balancing": "Balancing",
             "hedging": "Hedge execution and margin", "credit": "Credit", "origin": "Guarantees of origin"}
    for tech, site in meta["headline"].items():
        n = len(meta["years"])
        v, first = out["scenarios"][tech][site][n - 1], out["scenarios"][tech][site][0]
        y = out["yield"][tech][site]
        site_name = meta["sites"][site]
        bt = out["forwards"]["backtest"][tech]
        steps = v["steps"]
        shape = steps["profile"] + steps["correlation"] + steps["cannibalisation"]
        hedge, risk = v["hedge"], v["risk"]
        other = [p for p in v["premiums"] if p["premium"] != -steps["risk"]]
        lines += [
            f"## {n}-year {tech} PPA, {site_name}, {meta['capacity_mw']} MW from {meta['ppa_start']}",
            "",
            f"**Pay-as-produced bid: €{v['price']:.2f}/MWh** against a volume-weighted baseload forward "
            f"of €{steps['base']:.2f}. The shape and capture discount is €{-shape:.2f}, the risk premium "
            f"€{-steps['risk']:.2f} and balancing €{-steps['balancing']:.2f}. Executing and funding the hedge costs "
            f"€{-steps['hedging']:.2f} and credit €{-steps['credit']:.2f}; guarantees of origin add "
            f"€{steps['origin']:.2f}. Every MWh produced is paid.",
            "",
            f"**With a negative-price clause: €{v['negative']['price']:.2f}/MWh**, paid only in hours at or above "
            f"zero: {1 - first['negative']['volume_share']:.0%} of the output in {meta['years'][0]}, falling to "
            f"{1 - v['negative']['volume_share_last']:.0%} by {meta['years'][n - 1]} as negative hours grow with the "
            f"build-out. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth "
            f"€{v['negative']['put_mc']:.2f}.",
            "",
            "| Component | €/MWh |", "|---|---:|",
            *[f"| {names[k]} | {val:+.2f} |" for k, val in steps.items()],
            f"| **Price** | **{v['price']:.2f}** |",
            "",
            f"**Hedge:** sell {hedge[0]['mw']:.1f} MW of Cal-{hedge[0]['year'] % 100} baseload, stepping to "
            f"{hedge[-1]['mw']:.1f} MW by Cal-{hedge[-1]['year'] % 100} as the capture rate falls. The hedge cuts "
            f"lifetime P&L volatility from €{risk['sd_open']:.1f}m to €{risk['sd_hedged']:.1f}m. Taken alone, the "
            f"cannibalisation slope leaves €{risk['sd_slope']:.1f}m, the capture rate from year to year "
            f"€{risk['sd_year']:.1f}m, the hourly shape €{risk['sd_shape']:.1f}m and volume "
            f"€{risk['sd_volume']:.1f}m. The premium breaks even at P90; at "
            + " and ".join(f"P{p['confidence']} it is €{p['premium']:.2f}" for p in other).replace("and P", "and at P")
            + ". "
            f"In history ({bt['rows'][0]['year']}–{bt['rows'][-1]['year']}), the same rule on a one-year {tech} PPA "
            f"bought each October cut year-to-year P&L volatility from €{bt['sd']['open']:.1f}m to "
            f"€{bt['sd']['value_hedge']:.1f}m, against €{bt['sd']['volume_hedge']:.1f}m for a volume hedge.",
            "",
            f"**Volume:** P50 {y['p50']:.0f} full-load hours, P90 {y['p90_1y']:.0f} over one year and "
            f"{y['p90_10y']:.0f} over ten (ERA5 {y['years'][0]}–{y['years'][-1]}).",
            "",
            f"**Baseload-shaped alternative:** €{v['baseload']['price']:.2f}/MWh on {hedge[0]['p50_mw']:.1f} MW, "
            f"leaving the generator a realised €{v['baseload']['p10']:.2f}–{v['baseload']['p90']:.2f}/MWh (P10–P90).",
            "",
        ]
    term = out["forwards"]["term_recent"]
    slope = {t: f"{c['beta']:.2f} ± {c['beta_se']:.2f}".replace("-", "−")
             for t, c in out["capture"].items() if t != "annual"}
    other = [w for w in out["capture"]["solar"]["windows"] if w["months"] != capture.LOOKBACK]
    listed = lambda xs: ", ".join(xs[:-1]) + " and " + xs[-1]  # noqa: E731
    other_months = listed([str(w["months"]) for w in other])
    other_slopes = listed([f"{w['beta']:+.2f}".replace("-", "−") for w in other])
    other_prices = listed([f"€{w['price']:.2f}" for w in other])
    lines += [
        "## Assumptions",
        "",
        f"- Curve: {meta['futures_source']}; flat beyond the last listed calendar year.",
        "- Shape: day-ahead prices over the 36 months before the valuation month.",
        f"- Forward vol {term[0]['vol']:.0%} in the year before delivery, falling to {term[-1]['vol']:.0%} beyond "
        f"three years: realised on calendar contracts over the last {out['forwards']['window_months']} months of "
        f"OMIP settlements (history since {out['forwards']['first']}).",
        f"- Cannibalisation slope {slope['solar']} for solar and {slope['wind']} for wind, in capture rate per log "
        "share of load; the standard error is simulated.",
        f"- A year ahead, the capture rate has landed {out['capture']['solar']['year_ahead']['sd']:.2f} from the "
        f"model for solar and {out['capture']['wind']['year_ahead']['sd']:.2f} for wind (root mean square, "
        f"{out['capture']['solar']['year_ahead']['years'][0]}–{out['capture']['solar']['year_ahead']['years'][-1]}); "
        "each simulated year draws that error.",
        "- Hedge cost, credit and guarantees of origin are indicative terms from `inputs/market.toml`.",
        f"- The slope is fitted on {capture.LOOKBACK} months and depends on that choice more than its standard "
        f"error shows: on {other_months} months the solar slope is {other_slopes} and the solar price "
        f"{other_prices}.",
        f"- {meta['paths']} joint price–weather paths; imbalance spread and capacity build-out "
        "as set in `inputs/market.toml`.",
        "",
        *_memo_fundamental(out["fundamental"]),
        "",
        *_memo_fair_value(out["fair_value"], out["options"]["spark"]["inputs"]["gas"]),
        "",
        f"Built {meta['built']} from public data. Interactive version: `python -m ppa serve`.",
        "",
    ]
    return "\n".join(lines)


def _memo_fair_value(f: dict, gas_quote: float) -> list[str]:
    t, g = f["today"], f["signal"]["gap"]
    gas, carbon = (next(r for r in f["sensitivity"] if r["input"] == k) for k in ("gas", "carbon"))
    hit = int(g["hit_rate"] * 100 + 0.5)  # half up, as the site rounds
    return [
        "## Fair value",
        "",
        f"On {t['date']} the model priced {t['contract']} at €{t['fair_value']:.2f}/MWh against €{t['futures']:.2f} "
        f"on the exchange, from the last twelve months replayed at that day's fuel and carbon. Traded month by month "
        f"since 2019, the gap called the next move {hit}% of the time over {g['months']} months "
        f"(t = {g['t']:.1f}): not yet a signal.",
        "",
        f"€1/MWh on gas moves that fair value by €{gas['base']:.2f} and a solar capture price by "
        f"€{gas['solar']:.2f}; €1/t on carbon moves them by €{carbon['base']:.2f} and €{carbon['solar']:.2f}. "
        f"The model's gas is the last monthly average, €{gas['level']:.2f}; at the latest quote of "
        f"€{gas_quote:.2f} the fair value would be about €{gas['base'] * (gas_quote - gas['level']):.0f} higher.",
    ]


def _memo_fundamental(f: dict) -> list[str]:
    y = next(r for r in f["years"] if r["year"] == f["day"]["year"])
    return [
        "## Fundamental model",
        "",
        f"An hourly merit order of the German market, built only from public data and fitted on the "
        f"{f['train_years']} years before each test year. In {y['year']} it averaged €{y['model']:.0f}/MWh against "
        f"€{y['price']:.0f} actual, missed hourly prices by €{y['mae']:.0f} on average (knowing each month's actual "
        f"average would miss by €{y['month_mean_mae']:.0f}), and put the solar capture rate at "
        f"{y['solar_capture_model']:.2f} against {y['solar_capture']:.2f}.",
    ]
