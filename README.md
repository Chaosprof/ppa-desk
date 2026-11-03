# PPA Desk

Valuation and risk for German solar and wind power purchase agreements, built only from public data.

It takes the exchange forward curve, builds an arbitrage-free hourly curve, simulates prices jointly with site weather, and prices a pay-as-produced PPA. The fair price is split into the parts a desk negotiates over: baseload, profile, price–volume correlation, cannibalisation, risk premium, balancing and the negative-price clause. The output also includes the exchange hedge, the residual risk and the embedded options.

**[MEMO.md](MEMO.md)** has the answer on one page: a fair 10-year price, what it is made of, and the hedge.

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m ppa build    # fetch public data (cached in data/), run every model, write site/data.json and MEMO.md
.venv/bin/python -m ppa serve    # http://localhost:8000
.venv/bin/pytest
```

The first build downloads about 11 years of hourly market data, 21 years of ERA5 weather for four sites and ten years of daily OMIP futures settlements. Energy-Charts rate-limits and OMIP is fetched at one page a second, so this takes a little over an hour. Later builds take about 20 seconds.

## Method

**Hourly forward curve** — [`hpfc.py`](ppa/hpfc.py)
OMIP Phelix DE base futures for 30 Oct 2026 (Dec-26 through Cal-33) are turned into an hourly curve. A seasonal prior (month × day type × hour, learned from the last 36 months of day-ahead prices) gets a daily correction that minimises squared second differences. This is a discrete smoothing spline in the spirit of Benth, Koekebakker and Ollmar. The correction is constrained so every quoted future reprices exactly. Quotes that finer contracts already imply (Q1-27, Cal-27) are detected by rank, then checked rather than imposed. Beyond Cal-33 the curve is flat in nominal terms.

The shape is sign-split. Positive prices scale with the forward level, while negative prices stay in €/MWh. Negative prices are set by renewable support and must-run, not by gas, so a purely multiplicative shape made Cal-27 (€164) show −€131 hours. The split brings those back to the historical depth.

**Generation** — [`generation.py`](ppa/generation.py)
ERA5 weather from 2005 to 2026 comes from Open-Meteo for four sites (North, East, South, West).
- **Solar:** Hay–Davies transposition, Faiman cell temperature and DC/AC clipping, with ERA5's surface-irradiance bias corrected.
- **Wind:** hourly shear to hub height, air-density correction and a park-smoothed power curve.

P50 and P90 combine inter-annual weather variability with a model-error term.

**Joint simulation** — [`simulate.py`](ppa/simulate.py)
`P = L·(F + D + M·Δup) − (D + Δdown)`. Here `F` is the hourly curve, `M` its monthly level and `D` the expected negative part for the hour. `Δup` and `Δdown` are price residuals from a recent historical day, and `L` is a lognormal shock to the calendar-year price level that leaves negative prices alone. Site generation is taken from the same historical day, so the weather–price dependence (sunny noon, cheap noon) is resampled rather than assumed. Days are drawn in weekly blocks, with weekdays aligned and each pool year equally likely. Each year's volume is rescaled to a full-load-hours draw from the long ERA5 record. The simulation is unbiased: `E[P] = F`.

**Valuation** — [`valuation.py`](ppa/valuation.py)
The fair price is the expected price, lowered by a risk premium. That premium is solved so the hedged deal breaks even at its P90 outcome (the 10th percentile of lifetime P&L). After the hedge, the remaining risk is mostly shape, with some volume. The hedge is the minimum-variance amount of calendar baseload per year. It is smaller than the P50-equivalent MW, because a solar plant's revenue moves less than one-for-one with the baseload level. The baseload-shaped alternative shows the realised range the generator would keep under it.

**Cannibalisation** — [`capture.py`](ppa/capture.py)
Monthly fleet capture rates are regressed on log penetration with month fixed effects over the same 36-month window as the shape. The model is backtested one month ahead from 2021 against a seasonal naive forecast:
- **Solar:** mean absolute error 0.090 vs 0.113 for the naive forecast.
- **Wind:** no better than naive, which fits wind's weak penetration effect.

Two choices came from the data, not from priors. First, the full 2015–26 slope mispredicts 2026: summer solar share rose 36% over 2024 while capture held flat, after the 2025 Solarspitzengesetz and about 20 GW of batteries. A recent window captures that regime. Second, a linear slope forecasts one month ahead slightly better but extrapolates to a 0.16 capture rate by 2036. The log form builds in diminishing marginal cannibalisation instead.

**Options** — [`options.py`](ppa/options.py)
- **Negative-price clause:** priced as a strip of hourly puts struck at zero, plus the payments it avoids. The normal-model strip overstates the simulated value 2–5×, because negative prices are bounded and do not scale with the level.
- **Clean spark spread:** Kirk's approximation on a moment-matched gas-plus-carbon basket, against a three-asset Monte Carlo, swept over correlation.
- **2-hour battery:** valued as a swing option with 250 cycles a year by least-squares Monte Carlo, bounded by the intrinsic and perfect-foresight values. Each day it charges before it discharges.

**Forward curve history** — [`forwards.py`](ppa/forwards.py)
Daily OMIP settlements for every German base month, quarter and calendar contract since June 2016. Returns are taken within each contract, never across a roll. From them:
- **Forward vol:** the simulation's level vol is the realised vol of calendar contracts up to three years out over the last 24 months, not an assumption.
- **Samuelson effect:** volatility by time to delivery, rising as delivery approaches.
- **One-factor check:** the correlation between next-year and third-year daily returns, which tests the simulation's single level factor.
- **Hedge backtest:** a one-year PPA on the German fleet profile, bought each October from 2017 at the Cal forward times the trailing capture rate, held unhedged, with a volume hedge, and with the dashboard's value hedge.

**Data quality** — [`quality.py`](ppa/quality.py)
The checks cover:
- Coverage and gaps in the price history.
- Price bounds against the harmonised limits.
- Energy-Charts against SMARD, hour by hour.
- Solar output at night.
- Generation above installed capacity.
- Forecast coverage.
- HPFC repricing, and implied against quoted prices.

One check is flagged on purpose: Energy-Charts solar energy runs 3.2% below SMARD.

## Inputs

Everything the model believes that is not fetched lives in [`inputs/market.toml`](inputs/market.toml). That covers the valuation date, futures quotes, spark-spread inputs, the forward-history window, imbalance spread, capacity build-out, sites and plant parameters. To revalue on a new date, change `valuation_date` and the futures, delete `data/`, and build.

## Limits

- **No free implied vols:** forward vol is realised, from OMIP settlements. OMIP shows no trading in German contracts, so its prices are reference marks that track EEX, not trade prints.
- **Spark-spread gas and carbon:** indicative press quotes; replace them with EEX/ICE settlements for the valuation date.
- **No peak quotes:** the peak/base ratio comes from the historical shape. The solver honours peak quotes when they are given.
- **Balancing:** fleet day-ahead forecast error × single-site factor × an assumed imbalance spread, because reBAP needs registration.
- **15-minute day-ahead products (since Oct 2025):** averaged to hourly.
- **Price levels:** one factor moves all calendar years together.
- **Clause and cannibalisation overlap:** the projected capture-rate drop applies to positive-price volume, while simulated negative hours stay at today's frequency. Part of the future drop comes from more negative hours, which the clause excludes, so some overlap is likely.

## Layout

```
inputs/market.toml   market snapshot and assumptions
ppa/                 sources → dataset → hpfc, generation, capture → simulate → valuation, options → build
tests/               invariants: exact repricing, unbiased simulation, decomposition sums, option bounds
site/                static front end: index.html, style.css, app.js, charts.js, data.json (generated)
MEMO.md              generated one-page answer
```
