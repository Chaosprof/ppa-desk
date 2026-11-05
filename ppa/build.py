"""Run every model and write site/data.json and MEMO.md."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time

import numpy as np
import pandas as pd

from . import capture, forwards, fundamental, generation, hpfc, options, quality, simulate, sources, valuation
from .config import HISTORY_START, ROOT, Config, load
from .dataset import Dataset
from .hours import TZ, features, hours

TECHS = ["solar", "wind"]
BACKTEST_START = pd.Period("2021-01", "M")
HEADLINE = {"solar": "south", "wind": "north"}


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
    cannib, capture_out = {}, {}
    for tech in TECHS:
        model = capture.fit(monthly, tech, pd.Period(cfg.window[1], "M"))
        today, future = capture.penetration_path(monthly, data.capacity, tech, cfg.window, cfg["capacity"][tech])
        cannib[tech] = capture.cannibalisation(model, today, future)
        def fleet_rate(share, model=model, today=today):  # generation-weighted over months
            return round(float((model(today.index.to_numpy(), share.to_numpy()) * today / today.sum()).sum()), 4)

        capture_out[tech] = {
            "backtest": capture.backtest(monthly, tech, BACKTEST_START),
            "beta": round(model.beta, 4),
            "today": fleet_rate(today),
            "projection": {str(y): fleet_rate(future[y]) for y in future},
        }

    say("forwards")
    fwd = forwards.summary(data.omip_history, cfg.valuation_date, cfg["forwards"]["vol_window_months"])
    complete = [y for y in range(int(fwd["first"][:4]) + 1, cfg.valuation_date.year)]
    fwd["backtest"] = {tech: forwards.hedge_backtest(data.omip_history, data.prices, data.generation, data.capacity,
                                                     tech, complete, cfg["capacity_mw"]) for tech in TECHS}

    say("fundamental")
    fund = _fundamental(data, cfg)

    say("balancing")
    balancing = _balancing(data, cfg)
    rates = data.discount_curve

    scenarios, yields, battery = {}, {}, None
    for tech in TECHS:
        scenarios[tech], yields[tech] = {}, {}
        for site, s in cfg.sites.items():
            say(f"simulate {tech} {site}")
            cf = generation.capacity_factor(tech, data.weather(site), s, cfg["technology"][tech])
            stats = generation.yield_stats(cf, cfg["yield"]["model_uncertainty"][tech])
            yields[tech][site] = stats
            pool = simulate.pool(data.prices, cf, cfg.window)
            sims = simulate.run(curve, shape, pool, cfg.years, np.array(stats["flh"]), cfg.valuation_date,
                                cfg["capacity_mw"], fwd["model_vol"],
                                cfg["yield"]["model_uncertainty"][tech], cfg["risk"]["paths"], cfg["risk"]["seed"])
            df = valuation.discount_factors(rates, np.array([y.tau for y in sims]))
            adj = valuation.cannibalisation_adj(sims, cannib[tech])
            scenarios[tech][site] = [
                valuation.value(sims[:n], df[:n], adj[:n], balancing[tech], cfg["risk"]["volume_quantile"])
                for n in range(1, len(sims) + 1)
            ]
            if tech == "solar" and site == HEADLINE["solar"]:
                battery = _battery(sims[0], curve)

    say("options")
    opts = {"spark": _spark(cfg), "battery": battery}

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
            "assumptions": _assumptions(cfg, rates, fwd),
        },
        "scenarios": scenarios,
        "yield": yields,
        "curve": _curve(curve, cfg),
        "forwards": fwd,
        "capture": {"annual": _annual(annual), **capture_out},
        "balancing": {k: round(v, 2) for k, v in balancing.items()},
        "options": opts,
        "fundamental": fund,
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


def _assumptions(cfg: Config, rates: pd.Series, fwd: dict) -> list[dict]:
    r, b = cfg["risk"], cfg["balancing"]
    lo, hi = cfg.window
    return [
        {"name": "Shape window", "value": f"{lo:%b %Y} – {hi:%b %Y}"},
        {"name": "Forward vol", "value": f"{fwd['model_vol']:.0%}, realised over {fwd['window_months']} months"},
        {"name": "Risk premium", "value": f"P{round(100 - 100 * r['volume_quantile'])} break-even"},
        {"name": "Imbalance spread", "value": f"€{b['imbalance_spread']:.0f}/MWh × {b['single_asset_factor']}"},
        {"name": "Discount rate, 5y", "value": f"{rates.loc[5]:.2%}"},
        {"name": "Solar 2030", "value": f"{cfg['capacity']['solar']['2030']} GW"},
        {"name": "Wind 2030", "value": f"{cfg['capacity']['wind']['2030']} GW"},
        {"name": "Paths", "value": f"{r['paths']}"},
    ]


def _fundamental(data: Dataset, cfg: Config) -> dict:
    """Merit-order backtest: each year predicted from a fit on the years before it. Cached; fitting takes a minute."""
    fc = cfg["fundamental"]
    x = fundamental.inputs(data.mix, data.da_forecasts, data.prices, data.installed, data.fuels, data.eua,
                           data.neighbours, fc)
    key = hashlib.sha1(json.dumps([fc, fundamental.VERSION], sort_keys=True, default=str).encode()).hexdigest()[:10]
    model = sources._cached(f"fundamental_{cfg.history_end}_{key}",
                            lambda: fundamental.backtest(x, fundamental.efficiencies(data.plants), fc)[0])["model"]
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
    }


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


def _spark(cfg: Config) -> dict:
    s = cfg["spark"]
    lo, _ = hpfc.delivery(s["contract"])
    t = (lo - cfg.valuation_date).days / 365.25
    mc, se = options.spark_mc(s, t)
    sweep = []
    for rho in [0.0, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95]:
        v = {**s, "corr": {**s["corr"], "power_gas": rho}}
        m, e = options.spark_mc(v, t, n=200_000)
        sweep.append({"rho": rho, "kirk": round(options.spark_kirk(v, t), 3), "mc": round(m, 3), "se": round(e, 3)})
    intrinsic = s["power"] - s["heat_rate"] * s["gas"] - s["emission_factor"] * s["carbon"] - s["vom"]
    return {"contract": s["contract"], "expiry_years": round(t, 4), "intrinsic": round(intrinsic, 3),
            "kirk": round(options.spark_kirk(s, t), 3), "mc": round(mc, 3), "se": round(se, 3), "sweep": sweep,
            "inputs": {k: s[k] for k in ["power", "gas", "carbon", "heat_rate", "emission_factor", "vom"]}}


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
             "clause": "Negative-price clause"}
    for tech, site in meta["headline"].items():
        n = len(meta["years"])
        v = out["scenarios"][tech][site][n - 1]
        y = out["yield"][tech][site]
        site_name = meta["sites"][site]
        bt = out["forwards"]["backtest"][tech]
        steps = v["steps"]
        shape = steps["profile"] + steps["correlation"] + steps["cannibalisation"]
        hedge = v["hedge"]
        lines += [
            f"## {n}-year {tech} PPA, {site_name}, {meta['capacity_mw']} MW from {meta['ppa_start']}",
            "",
            f"**Fair pay-as-produced price: €{v['price']:.2f}/MWh** against a volume-weighted baseload forward "
            f"of €{steps['base']:.2f}. The shape and capture discount is €{-shape:.2f}, the risk premium "
            f"€{-steps['risk']:.2f} and balancing €{-steps['balancing']:.2f}; the negative-price clause adds "
            f"€{steps['clause']:+.2f}.",
            "",
            "| Component | €/MWh |", "|---|---:|",
            *[f"| {names[k]} | {val:+.2f} |" for k, val in steps.items()],
            f"| **Fair price** | **{v['price']:.2f}** |",
            "",
            f"**Hedge:** sell {hedge[0]['mw']:.1f} MW of Cal-{hedge[0]['year'] % 100} baseload, stepping to "
            f"{hedge[-1]['mw']:.1f} MW by Cal-{hedge[-1]['year'] % 100}. The hedge cuts lifetime P&L "
            f"volatility from €{v['risk']['sd_open']:.1f}m to €{v['risk']['sd_hedged']:.1f}m; what remains is "
            f"shape (€{v['risk']['sd_shape']:.1f}m) and volume (€{v['risk']['sd_volume']:.1f}m). "
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
    lines += [
        "## Assumptions",
        "",
        f"- Curve: {meta['futures_source']}; flat beyond the last listed calendar year.",
        "- Shape: day-ahead prices over the 36 months before the valuation month.",
        f"- Forward vol {out['forwards']['model_vol']:.0%}: realised on calendar contracts over the last "
        f"{out['forwards']['window_months']} months of OMIP settlements (history since {out['forwards']['first']}).",
        f"- {meta['paths']} joint price–weather paths; imbalance spread and capacity build-out "
        "as set in `inputs/market.toml`.",
        "",
        *_memo_fundamental(out["fundamental"]),
        "",
        f"Built {meta['built']} from public data. Interactive version: `python -m ppa serve`.",
        "",
    ]
    return "\n".join(lines)


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
