# PPA Desk manual

How to run the repo, where every number comes from, and how every model works and why it was built that way.

[README.md](README.md) is the short description and [MEMO.md](MEMO.md) the one-page answer. This manual is the long form: it follows the pipeline from raw data to the bid, module by module. Numbers quoted here are from the build of 9 November 2026 for the valuation date 30 October 2026. They change when the inputs or the date change; the method does not.

## Contents

1. [What the repo answers](#1-what-the-repo-answers)
2. [Usage](#2-usage)
3. [Architecture](#3-architecture)
4. [Conventions](#4-conventions)
5. [Data: every source](#5-data-every-source)
6. [Assumptions: every input in market.toml](#6-assumptions-every-input-in-markettoml)
7. [Hourly price forward curve](#7-hourly-price-forward-curve)
8. [Site generation and yield](#8-site-generation-and-yield)
9. [Capture rates and cannibalisation](#9-capture-rates-and-cannibalisation)
10. [Forward curve history](#10-forward-curve-history)
11. [Joint price and generation simulation](#11-joint-price-and-generation-simulation)
12. [Valuation](#12-valuation)
13. [Options](#13-options)
14. [Fundamental model](#14-fundamental-model)
15. [Fair value against the market](#15-fair-value-against-the-market)
16. [Balancing cost](#16-balancing-cost)
17. [Data quality checks](#17-data-quality-checks)
18. [Outputs: data.json, MEMO.md and the site](#18-outputs-datajson-memomd-and-the-site)
19. [Tests](#19-tests)
20. [Limits](#20-limits)
21. [Recipes](#21-recipes)
22. [Glossary](#22-glossary)

---

## 1. What the repo answers

A desk is asked to buy the output of a German solar or wind plant at a fixed price for one to ten years (a pay-as-produced power purchase agreement, PPA). The repo answers four questions from public data alone:

1. **What fixed price should the desk bid?** A price per MWh, split into the parts a desk negotiates over: baseload forward, profile, price–volume correlation, cannibalisation, risk premium, balancing, hedge cost, credit and guarantees of origin.
2. **How should it hedge?** MW of calendar baseload futures to sell per delivery year, and how much risk that leaves, by source.
3. **What are the embedded options worth?** The negative-price clause, plus two reference structures (a clean spark spread option and a 2-hour battery).
4. **Is the futures market itself fairly priced?** An hourly merit-order model of the German market gives a fundamental fair value for the next calendar year, compared with the futures month by month since 2019.

The headline deals are a 100 MW plant from 1 January 2027: solar at the South site and wind at the North site. All four sites, both technologies and tenors of 1 to 10 years are valued (80 deals).

| Headline, 10 years | Solar, South | Wind, North |
|---|---:|---:|
| Pay-as-produced bid, €/MWh | 38.30 | 75.42 |
| With a negative-price clause, € per paid MWh | 61.94 | 85.79 |
| Volume-weighted baseload forward, €/MWh | 96.63 | 96.41 |
| Hedge, MW of Cal-27 → Cal-36 baseload sold | 9.4 → 7.2 | 35.1 → 33.4 |
| Lifetime P&L volatility, unhedged → hedged, €m | 18.7 → 6.5 | 74.3 → 11.4 |
| P50 full-load hours | 1,432 | 3,490 |

---

## 2. Usage

### Install, build, serve, test

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

```bash
.venv/bin/python -m ppa build
```

```bash
.venv/bin/python -m ppa serve
```

```bash
.venv/bin/pytest
```

```bash
.venv/bin/ruff check ppa tests
```

- `build` fetches every public source (cached under `data/`), runs every model, and writes `site/data.json` and `MEMO.md`. With no argument, `python -m ppa` also builds.
- `serve [port]` serves `site/` on `http://localhost:8000` (or the given port) with caching off, so a rebuild shows on refresh.
- `pytest` needs no network: the tests run on synthetic data and on `inputs/market.toml`.
- Python 3.11 or later. Dependencies: numpy, pandas, scipy, pyarrow, requests, holidays, openpyxl and xlrd (for the EEX spreadsheets). No API keys.

### How long a build takes

- **First build:** a little over an hour. It downloads about 11 years of hourly market data for Germany and ten neighbours, 21 years of ERA5 weather for four sites, and ten years of daily OMIP settlements. Energy-Charts rate-limits, and OMIP is a website fetched at one page a second.
- **Fundamental backtest:** about 10 minutes on 8 processes whenever its cache key changes (see below).
- **Later builds:** everything is read from `data/`. The models themselves (curve, eight 800-path simulations over ten years, 80 valuations, options, checks) are what is left: 23 seconds when measured for this manual, almost all of it the eight simulations.
- **Tests:** 91 tests in about 3 seconds.

The build is deterministic. With the same `data/`, inputs and seed it reproduces `site/data.json` exactly, apart from the `built` timestamp.

### The cache in `data/`

Every source is cached as a parquet file, and `sources._cached(name, build)` never refreshes a file that exists. The name is the cache key:

| File | Key | Refreshed when |
|---|---|---|
| `prices_`, `generation_`, `mix_`, `neighbours_`, `forecasts_` | start and end date | the valuation date changes |
| `chunks/price_…`, `power_…`, `mix_…`, `residual_…`, `forecast_…` | one calendar year (or part) per file | only the current year's chunk; past years are reused |
| `chunks/omip_DE_YYYY-MM` | one month of OMIP pages | never once written, even if the month was incomplete |
| `weather_<site>_`, `smard_`, `ecb_`, `eua_`, `fred_` | dates | the valuation date changes |
| `capacity`, `installed`, `opsd_plants` | none | never |
| `fundamental_<date>_<hash>` (+ `_params`) | history end, hash of `[fundamental]` and `fundamental.VERSION` | the config or the version changes |
| `fairvalue_`, `sensitivity_`, `drivers_`, `buildout_` | the same hash plus `fairvalue.VERSION` | either version or the config changes |

Two consequences. First, `capacity.parquet` and `installed.parquet` carry no date, and the OMIP chunk for the month of the old valuation date may be incomplete, so the safe way to revalue is the README's: delete `data/` and rebuild. Second, the fundamental caches hash only the `[fundamental]` table and the version constants. A change to the model's code does not invalidate them: bump `fundamental.VERSION` (or `fairvalue.VERSION`) when the code changes.

### Revalue on a new date

1. Set `valuation_date` in `inputs/market.toml`.
2. Replace the `[futures]` quotes with OMIP's German base reference prices for that date, and update `[spark]` (power is the front month from the same page, carbon the last EEX auction, gas an ICE TTF quote).
3. Check `ppa_start` and the `[capacity]` paths still cover `ppa_start.year` to `ppa_start.year + max_tenor_years − 1`.
4. Delete `data/` and build.

The Data view then checks the typed quotes against the scraped OMIP history for that date ("Futures inputs vs OMIP history") and fails if any differs by half a cent.

---

## 3. Architecture

```
inputs/market.toml
      │
  config.py ── valuation date, delivery years, shape window
      │
  sources.py ── HTTP + parquet cache in data/
      │
  dataset.py ── one lazy attribute per observed input
      │
      ├── hpfc.py ───────── shape from history + futures quotes → hourly curve
      ├── generation.py ─── ERA5 weather → hourly capacity factors, P50/P90
      ├── capture.py ────── fleet capture rates → cannibalisation slope and errors
      ├── forwards.py ───── OMIP history → vol by time to delivery, correlations, hedge backtest
      ├── fundamental.py ── hourly merit order, fitted and backtested year by year
      │        └── fairvalue.py ── next-year fair value vs futures, sensitivities, drivers
      │
      ├── simulate.py ───── curve + residual days + site weather + level shocks → per-year path totals
      │        ├── valuation.py ── price, steps, premium, hedge, risk, clause, terms
      │        └── options.py ──── puts, spark spread, battery swing
      │
      ├── quality.py ────── checks on the data and the curve
      │
  build.py ── runs everything, writes site/data.json and MEMO.md
      │
  site/ ── index.html, app.js, charts.js, style.css read data.json
```

`build.run` executes in this order: fetch → hpfc → capture → forwards → fundamental → fair value → balancing → simulate and value (each technology × site) → options → quality → write.

`site/data.json` is the only contract with the front end. A number appears on the site only if `build.py` writes it.

| Module | Lines | Role |
|---|---:|---|
| `config.py` | 59 | Loads the TOML; derives `years`, `history_end`, `window` |
| `hours.py` | 73 | Delivery calendar: UTC hours of local days, day types, peak flag, contract periods |
| `sources.py` | 463 | Every HTTP source and its cache |
| `dataset.py` | 91 | Lazy access to each source with the right dates |
| `hpfc.py` | 257 | Shape learning and the constrained smoothing spline |
| `generation.py` | 100 | PV and wind models, yield statistics |
| `capture.py` | 167 | Capture rates, the log-penetration regression, its errors, projections |
| `forwards.py` | 174 | Realised vol, term structure, spark inputs, hedge backtest |
| `simulate.py` | 224 | Joint Monte Carlo |
| `valuation.py` | 315 | The deal, the hedge, the premium, the terms |
| `options.py` | 101 | Bachelier, Kirk, three-asset Monte Carlo, least-squares Monte Carlo |
| `fundamental.py` | 394 | Merit-order model, calibration, backtest |
| `fairvalue.py` | 206 | Fair value scenarios, signal test, sensitivities, price drivers |
| `quality.py` | 96 | Data and curve checks |
| `build.py` | 538 | Orchestration, output, the memo |

---

## 4. Conventions

- **Time.** Every hourly series is indexed by the UTC start of the hour. Calendar logic (year, month, hour of day, day type, peak, holiday, local date) always goes through `hours.features`, which works in Europe/Berlin local time. `hours.hours(start, end)` returns the UTC hours of the local delivery days in `[start, end)`, so days at daylight-saving changes have 23 or 25 hours and a delivery period always has the right number of hours.
- **Day types.** Weekday (0), Saturday (1), Sunday (2). German national public holidays (the `holidays` package) count as Sundays.
- **Peak.** Phelix DE peak: Monday to Friday, 08:00–20:00 local, holidays included.
- **Season.** `season` is the position within the year in months since mid-January, in `[0, 12)`. It is what the month tables are blended on.
- **Contracts.** `hours.delivery` parses `Dec-26`, `Q1-27`, `Cal-27` into a local `[start, end)` date range.
- **Units.** Prices €/MWh, volumes MWh, capacity MW (installed fleet capacity GW), P&L € (the site shows €m), fuel €/MWh thermal, carbon €/t.
- **Capacity factor.** Site output is the AC capacity factor per hour, so a 100 MW plant produces `100 × cf` MWh.
- **P-levels.** For yield, P90 is the output exceeded with 90% probability. For P&L, "breaks even at P90" means the 10th percentile of lifetime P&L is zero.
- **Sign-split shape.** `price = level × up − down`. Positive prices scale with the level; negative ones do not. This runs through the curve, the simulation and the tests.
- **Where assumptions live.** Anything believed and not fetched is in `inputs/market.toml`. Anything measurable from fetched data is measured (forward vol, spark vols and correlations, both capture errors).

---

## 5. Data: every source

All sources are public and keyless. `sources._get` retries up to 12 times on HTTP 429 and 5xx and on connection errors, honouring `Retry-After`, with exponential back-off capped at 90 seconds. Energy-Charts mixes hourly and 15-minute resolution; `_to_hourly` drops duplicate stamps and averages to the hour, so the 15-minute day-ahead products (since October 2025) enter as hourly means.

History starts on 1 January 2015 (`config.HISTORY_START`) and ends the day before the valuation date (`history_end`). Weather starts on 1 January 2005 (`config.WEATHER_START`).

### 5.1 Energy-Charts (Fraunhofer ISE), `https://api.energy-charts.info`

| `Dataset` attribute | Endpoint | Content | Span | Used by |
|---|---|---|---|---|
| `prices` | `/price`, `bzn=DE-LU` (`DE-AT-LU` before 1 Oct 2018) | Day-ahead price, €/MWh | 2015 → valuation − 1 day; 103,679 hours | Shape, residual pool, capture rates, hedge backtest, fundamental target, fair value realised, checks |
| `generation` | `/public_power`, `country=de` | Solar, wind onshore, wind offshore, load, MW | same | Capture rates, penetration, balancing, hedge backtest, checks |
| `mix` | `/public_power`, `country=de` | 19 series: nuclear, lignite, hard coal, gas, oil, coal-derived gas, biomass, run-of-river, reservoir, pumped storage output and consumption, geothermal, waste, others, wind on- and offshore, solar, load, cross-border trading. Exports and pumping are negative | same | Fundamental model inputs; nuclear for the fair value |
| `neighbours` | `/public_power` for FR, NL, BE, AT, CH, CZ, PL, DK, SE, NO | Residual load per country: load less nuclear, run-of-river, reservoir, biomass, geothermal, wind and solar | same | The trade function |
| `forecasts` | `/public_power_forecast`, `forecast_type=day-ahead` | Day-ahead fleet forecast of solar, wind onshore, wind offshore, MW | the shape window | Balancing cost, coverage check |
| `da_forecasts` | same | same | 2015 → valuation − 1 day | What the fundamental model's market clears on |
| `capacity` | `/installed_power`, monthly | Solar AC, wind onshore, wind offshore, GW at month start | all published | Penetration path, hedge backtest profile, checks |
| `installed` | `/installed_power`, monthly and yearly | As above plus nuclear, lignite, hard coal, gas, oil, pumped storage, biomass, battery power (GW) and energy (GWh) | all published | Thermal and nuclear capacity in the fundamental model |

Details that matter:

- **The zone split.** Before 1 October 2018 the price is the joint Germany–Austria–Luxembourg zone. Fundamental fits for 2019–2021 therefore train partly on the joint zone.
- **Thermal capacity is yearly.** Energy-Charts publishes renewables and batteries monthly but thermal plants only as year-end values. `sources.installed` places each yearly value at the year end and interpolates linearly through the months, then forward-fills.
- **Residual load abroad** is what a neighbour must cover from fossil plants or imports, so it measures how much it needs from Germany.
- **Same endpoint, two caches.** `generation` and `mix` both read `/public_power`; they keep different columns and are cached separately.

### 5.2 SMARD (Bundesnetzagentur), `https://www.smard.de/app/chart_data`

Series 4169 (DE-LU day-ahead price) and 4068 (solar generation), hourly, over the shape window. Used only as an independent cross-check of Energy-Charts in `quality.py`.

### 5.3 Open-Meteo ERA5, `https://archive-api.open-meteo.com/v1/archive`

Hourly reanalysis (`models=era5`) at each site's coordinates from 2005, in UTC: global horizontal irradiance (`shortwave_radiation`), direct normal irradiance, diffuse horizontal irradiance, 2 m temperature, and wind speed at 10 m and 100 m in m/s. Radiation is the mean over the hour ending at the stamp; the PV model shifts it to the hour start. Fetched sequentially in four-year chunks because Open-Meteo rate-limits by volume. ERA5 is published with about five days' lag, so the last days before the valuation date can be empty.

| Site | Latitude | Longitude | Near |
|---|---:|---:|---|
| North | 54.48 | 9.05 | Husum, North Sea coast |
| East | 51.76 | 14.33 | Cottbus |
| South | 48.54 | 12.15 | Landshut |
| West | 51.96 | 7.63 | Münster |

### 5.4 OMIP, `https://www.omip.pt/en/dados-mercado`

OMIP lists German base futures (instrument FDB, zone DE) and publishes daily reference prices freely. `sources.omip_history` requests the page for every business day since 1 June 2016 (`[forwards].history_start`), one a second, and `parse_omip` reads the reference price (the tenth cell of each row, "column D") for every month (`FDB M Nov-25` → `Nov-25`), quarter (`Q1-26`) and year (`YR-26` → `Cal-26`) contract. A page that answers for another date is discarded. The result is 51,368 rows of date, contract, price over 196 contracts.

Two uses. The quotes for the valuation date are typed into `inputs/market.toml` (and checked against this history). The history itself gives realised volatility, the curve correlation, the hedge backtest and the futures leg of the fair value test.

Why OMIP and not EEX, where German power actually trades: EEX settlements are licensed, OMIP's are free. OMIP shows no trading in the German contracts, so these are reference marks that track EEX, not trade prints.

### 5.5 EEX carbon auctions

The daily EU allowance (EUA) price is the mean clearing price of that day's EEX primary auctions: a zip archive of yearly reports for 2012–2025, plus one spreadsheet per year from 2026. EEX moved the header row and renamed the price column over the years, so `_eua_sheet` finds the row containing "Date" and the column starting "Auction Price". 2,969 auction days since 2012; €88.05/t on 29 October 2026.

### 5.6 FRED (IMF commodity prices), `https://fred.stlouisfed.org`

| Series | Content | Unit | Frequency |
|---|---|---|---|
| `PNGASEUUSDM` | EU natural gas (TTF) | USD/MMBtu | monthly average |
| `PCOALAUUSDM` | Coal, Australia (Newcastle) | USD/t | monthly average |
| `POILBREUSDM` | Brent crude | USD/bbl | monthly average |
| `DEXUSEU` | USD per EUR | — | daily |

These are the only free fuel prices with a long history. Daily TTF and API2 (the Rotterdam coal German plants pay) are licensed. The cost is stated in the limits: within-month swings are missed and coal is the wrong basis.

### 5.7 Open Power System Data

The German conventional plant list of 1 October 2020 (BNetzA and UBA data): 909 units with net capacity, fuel, status and an efficiency estimate. Used for the spread of efficiencies within each fuel's fleet.

### 5.8 ECB

Euro-area AAA government spot yields for maturities of 1 to 15 years (`YC` dataflow, series `B.U2.EUR.4F.G_N_A.SV_C_YM.SR_<n>Y`), last fixing on or before the valuation date. Continuously compounded. 2.88% at one year, 3.10% at five, 3.37% at ten.

### 5.9 Derived inputs

| Quantity | Built from | Where |
|---|---|---|
| Fuel in €/MWh thermal | FRED series and the monthly mean exchange rate. Gas: `USD/MMBtu × 3.412 / (USD per EUR) / 0.9`; coal: `USD/t / (USD per EUR) / 6.978`; oil: `USD/bbl / (USD per EUR) / 1.70` | `fundamental.fuel_prices` |
| Lignite | €5.0/MWh thermal, an assumption (lignite is not traded) | `[fundamental].lignite_fuel` |
| Demand served by the public grid | All generation + net imports + net storage output | `fundamental.inputs` |
| Fleet efficiencies | OPSD list, 8 capacity-weighted quantiles per fuel | `fundamental.efficiencies` |
| Penetration | Fleet generation / load, by month | `capture.fleet` |
| Capture rate | Generation-weighted price / baseload price, by month and year | `capture.fleet` |

The gas conversion deserves a line. Gas trades per MWh of gross calorific value, while plant efficiencies and emission factors are stated on net calorific value, which is 90% of gross. A plant's gas therefore costs the quote over 0.9 (`GAS_NET_PER_GROSS`). The spark spread option uses the same convention the other way round: its heat rate of 2.02 MWh of gas as quoted per MWh of power is a CCGT at 55% net, 49.5% gross.

---

## 6. Assumptions: every input in market.toml

| Key | Value | Meaning | Used by |
|---|---|---|---|
| `valuation_date` | 2026-10-30 | The date of the futures quotes; history ends the day before | everything |
| `ppa_start` | 2027-01-01 | First delivery day; its year is the first delivery year | `Config.years` |
| `max_tenor_years` | 10 | Delivery years 2027–2036 | `Config.years` |
| `capacity_mw` | 100 | Plant size | simulation, hedge backtest |
| `[futures].base` | 19 quotes | Dec-26 to Apr-27 months, Q1-27 to Q3-28 quarters, Cal-27 to Cal-33 | curve |
| `[futures].peak` | empty | Optional peak quotes; the solver honours them when given | curve |
| `[shape].history_months` | 36 | Trailing whole months that define the shape window | shape, residual pool, today's penetration, balancing, SMARD checks |
| `[spark]` | power 223.04, gas 88.41, carbon 88.05, heat rate 2.02, emission factor 0.37, VOM 3.0 | Front-month clean spark spread; gas is a press quote of ICE TTF for 19 October | spark option; the gas quote also restates the fair value |
| `[forwards].history_start` | 2016-06-01 | First OMIP day | OMIP history |
| `[forwards].vol_window_months` | 24 | Trailing window for realised vol and spark inputs | simulation vol, spark inputs, curve correlation |
| `[fundamental].train_years` | 3 | Years each test year is fitted on | backtest, fair value |
| `[fundamental].first_test_year` | 2019 | First backtest year | backtest |
| `[fundamental].lignite_fuel` | 5.0 | €/MWh thermal | merit order |
| `[fundamental].emissions` | lignite 0.40, coal 0.34, gas 0.20, oil 0.27 | tCO₂ per MWh thermal, net | merit order |
| `[fundamental].vom` | lignite 4, coal 4, gas 2, oil 3 | €/MWh electric | merit order |
| `[fundamental].pumped_hours`, `pumped_efficiency` | 6.0, 0.75 | Pumped hydro: hours at full power, round trip | storage |
| `[fundamental].demand_response` | 5,000 MW at €500, spread 150 | Load that backs off at scarcity prices | backstop |
| `[fundamental].surplus_response` | 5,000 MW at −€150, spread 50 | Power-to-heat, extra pumping, plants running below cost | backstop |
| `[risk].volume_quantile` | 0.10 | The premium makes this quantile of hedged P&L zero (P90) | valuation |
| `[risk].paths`, `seed` | 800, 7 | Monte Carlo size and seed | simulation |
| `[balancing].imbalance_spread` | 20.0 | € per MWh of forecast error, the gap between the imbalance price (reBAP) and day-ahead | balancing |
| `[balancing].single_asset_factor` | 1.5 | Single-site error against the fleet's | balancing |
| `[yield].model_uncertainty` | solar 0.05, wind 0.08 | Yield uncertainty beyond weather, share of P50 | P90, simulated volume |
| `[commercial].origin` | 1.00 | €/MWh, guarantees of origin delivered with the power | price step |
| `[commercial].liquid_years` | 3 | Calendar years ahead trading at the tight spread | hedge cost |
| `[commercial].bid_offer` | liquid 0.25, illiquid 1.50 | Half the spread, € per MWh of baseload sold | hedge cost |
| `[commercial].margin` | 99%, 2 days, 1.5% | Initial margin: confidence, holding period, funding spread | hedge cost |
| `[commercial].credit` | hazard 1.5%, recovery 40% | Generator default per year, an unrated project company | credit step |
| `[capacity].load_growth` | 0.01 | Load growth per year in the penetration path | cannibalisation |
| `[capacity].solar` | 127 GW in 2027 → 232 GW in 2036 | Average installed solar per delivery year | cannibalisation |
| `[capacity].wind` | 86 GW → 145 GW | Onshore plus offshore | cannibalisation |
| `[sites]` | four | Name and coordinates | weather |
| `[technology.solar]` | tilt 25°, azimuth 180°, DC/AC 1.25, losses 14%, temperature coefficient −0.37%/K, irradiance scale 0.93, degradation 0.4%/yr | Fixed-tilt PV | generation, simulation |
| `[technology.wind]` | hub 135 m, losses 13%, power curve | Generic modern onshore turbine, about 280 W/m² | generation |

The capacity path continues the 2025–26 build rate (about 13 GW of solar a year), below the EEG 2023 targets of 215 GW of solar and 145 GW of wind by 2030. Installed today: 120.4 GW solar and 83.4 GW wind.

Constants that are in code and not in the TOML:

| Constant | Value | Where |
|---|---|---|
| History and weather start | 2015-01-01, 2005-01-01 | `config.py` |
| Capture regression look-back | 36 months | `capture.LOOKBACK` |
| Capture backtest start, slope refit windows | January 2021; 24, 36, 48, 60 months | `build.py` |
| Headline sites | solar South, wind North | `build.HEADLINE` |
| Trading days a year; vol buckets | 252; <1y, 1–2y, 2–3y, 3y+ | `forwards.py` |
| Premium ladder | P75, P90, P95 | `valuation.value` |
| Level-shock time step | one month | `simulate.level_shocks` |
| Battery | 2 hours, 88% round trip, 250 cycles a year | `simulate.battery_spread`, `build._battery` |
| Merit order | floor −500, cap 4,000, 8 efficiency bins, must-run quantile 2%, "no reason to curtail" above €20, zero-bid width €0.5, shut-down width €10, reference CCGT 55% | `fundamental.py` |
| Ground albedo; park smoothing | 0.2; 0.8 m/s | `generation.py` |

`[shape].history_months` and `capture.LOOKBACK` are both 36 by design, so the cannibalisation slope describes the same regime as the shape. They are separate settings: changing the TOML moves the shape window but not the regression's look-back.

---

## 7. Hourly price forward curve

`hpfc.py`. Input: the futures quotes and the last 36 whole months of day-ahead prices (October 2023 – September 2026). Output: one expected price for every hour from 1 December 2026 to 31 December 2036.

```
F_h = (level_h + a_d(h) + b_d(h) × peak_h) × up_h − down_h
```

`level × up − down` is a seasonal prior. `a` (and `b`, when peak quotes exist) is a daily correction that makes every quoted future reprice exactly.

### 7.1 Why a sign-split shape

A plain multiplicative shape (hourly price = level × factor) was the first version. With Cal-27 at €164, it scaled today's negative midday prices up with the level and produced −€131 hours. Negative prices are set by renewable support and must-run, not by gas, so a gas-driven move in the forward level should not deepen them. The shape is therefore split by sign:

| Table | Shape | Definition per cell (month × day type × hour) |
|---|---|---|
| `up` | 12 × 3 × 24 | mean of `max(price, 0) / (that month's mean price)`: dimensionless |
| `down` | 12 × 3 × 24 | mean of `max(−price, 0)`: €/MWh |
| `month` | 12 | the month's level relative to the year around it |

The expected price is `level × up − down`. Daylight-saving gaps in a cell (hour 2 can be missing) are forward-filled.

In the learned shape, a July weekday's `up` runs from 1.45 at 07:00 through 0.39 at 14:00 to 1.92 at 20:00: the duck curve. `down` on a May Sunday peaks at €61 at 13:00, against €3 on a May weekday.

### 7.2 Monthly seasonality without the trend

`hpfc.seasonality` computes each month's mean price over the centred twelve-month mean around it (weights ½, 1 × 11, ½), then averages those ratios by calendar month and normalises them to average one.

Why: prices rallied through 2026. Against fixed twelve-month blocks, a rally at the end of the window reads as a seasonal peak in whichever months come last. Dividing by the centred mean takes the trend out.

The centred mean needs six months on either side. The six months before the window are used where prices exist, and the last six months of the window have no ratio. Over this window that leaves ratios for October 2023 to March 2026: three observations for October–March and two for April–September. If a calendar month ends up with none (a window too short), the code falls back to whole twelve-month blocks.

The factors: January 1.18, February 1.10, March 0.99, April 0.88, May 0.85, June 0.85, July 0.94, August 0.95, September 0.95, October 1.03, November 1.22, December 1.06.

### 7.3 No steps at month or year boundaries

A table indexed by month would step at midnight on the first. Every monthly or yearly table is instead turned into node values at period midpoints and blended linearly between them (`nodes`, `blend`).

Naive nodes equal to the monthly values would not keep the monthly means, because blending borrows from the neighbours. Over period `k` a linear blend averages `(v[k−1] + 6·v[k] + v[k+1]) / 8`, so `nodes` solves that tridiagonal system for the node values that reproduce every period mean exactly. Month tables wrap around the year; the year table does not, and is flat beyond its first and last midpoints.

Exact nodes overshoot below zero next to a sharp edge: there are no negative prices in March and many in April. For `up` and `down` the nodes are therefore solved by non-negative least squares, keeping the total exactly and every period mean as closely as possible. A little of April's mass lands in late March.

### 7.4 The prior

```
level_h = annual_h × month_h
prior_h = level_h × up_h − down_h
```

`annual` is the calendar-year base quotes blended across New Year with each year's mean kept (`_annual_levels`). December 2026, before the first calendar quote, takes Cal-27's level; the Dec-26 quote then corrects it. `month` is the blended seasonality factor.

### 7.5 The correction: a constrained smoothing spline

For each quote, the hourly average of the curve over its delivery period must equal its price. The unknowns are one level correction `a_d` per day (plus `b_d` for peak hours if peak quotes exist). The correction is multiplied by `up`, so it scales only the positive part of the price.

```
minimise    Σ_d (a_{d−1} − 2·a_d + a_{d+1})²        (and the same for b)
subject to  mean over the quote's hours of [(a_d + b_d·peak_h) · up_h]  =  quote − mean of the prior
```

This is a discrete smoothing spline in the spirit of Benth, Koekebakker and Ollmar (2007): among all corrections that reprice the quotes, take the one with the least curvature, so the correction has no steps or kinks at contract boundaries. It is a quadratic programme with equality constraints, solved in one sparse linear solve of its KKT system (`_solve`). A ridge of 1e-9 keeps the system non-singular.

**Redundant quotes.** Q1-27 is implied by Jan-27, Feb-27 and Mar-27; Cal-27 by those months and Q2–Q4-27. Imposing both would make the constraints singular or, if the quotes disagree by rounding, inconsistent. Quotes are processed finest first, and a quote whose constraint row does not raise the matrix rank is set aside as implied. After the solve it is repriced and reported: Q1-27 implies 220.587 against 220.59 quoted, Cal-27 164.269 against 164.27. Independent quotes reprice to 1e-8.

**Beyond the last quote.** Cal-34 to Cal-36 are added as extrapolated quotes at Cal-33's price, €77.68: the curve is flat in nominal terms. This is an assumption, not a market price.

**Constraints are hourly averages.** Month lengths, leap years and the 23- and 25-hour days are handled by construction.

### 7.6 What the curve looks like

| | Q1 | Q2 | Q3 | Q4 | Cal |
|---|---:|---:|---:|---:|---:|
| 2027 | 220.6 | 134.7 | 143.0 | 159.7 | 164.27 |
| 2028 | 153.5 | 93.7 | 101.6 | 120.3 | 117.24 |
| 2030 | 101.6 | 77.8 | 84.3 | 96.6 | 90.05 |
| 2036 | 84.6 | 66.3 | 73.8 | 85.9 | 77.68 |

Quarters to Q3-28 are quoted, and Q4-28 follows from Cal-28 less the three quoted quarters. Later quarters come from the shape. The daily correction ranges from −€22 to +€51. The expected price is below zero in 180 hours of 2027 and about 240 hours a year by 2036, never below −€67. A simulated path has about 550 negative hours a year, because an hour whose expected price is positive is still negative on some days.

---

## 8. Site generation and yield

`generation.py`. Input: ERA5 weather at the site, 2005–2026. Output: an hourly AC capacity factor, and P50/P90 full-load hours.

### 8.1 Solar

A fixed-tilt PV plant, 25° tilt, facing south, DC/AC ratio 1.25.

1. **Sun position.** NOAA's simplified algorithm gives zenith and azimuth at the middle of each hour (the ERA5 stamp minus 30 minutes, since the radiation is the mean over the preceding hour).
2. **Irradiance bias.** ERA5 overstates surface irradiance over Germany by about 5–8%, so global, direct and diffuse irradiance are multiplied by 0.93.
3. **Transposition to the tilted plane: Hay–Davies.** The anisotropy index is `A = DNI / I₀`, with `I₀` the extraterrestrial irradiance for the day of year. Sky diffuse on the plane is `DHI × (A·cos(AOI)/cos(z) + (1 − A)·(1 + cos(tilt))/2)`: a circumsolar part that follows the beam and an isotropic rest. Ground-reflected is `GHI × 0.2 × (1 − cos(tilt))/2`. Plane-of-array irradiance is beam + sky + ground, zero when the sun is down. `cos(z)` is floored at 0.087 (85°) so the circumsolar term does not blow up at sunrise.
4. **Cell temperature: Faiman.** `T_cell = T_air + POA / (25 + 6.84 × wind₁₀)`.
5. **DC power.** `POA/1000 × (1 + (−0.0037) × (T_cell − 25)) × (1 − 0.14)`. The 14% covers soiling, mismatch, wiring, inverter and availability.
6. **Clipping.** `AC = clip(DC × 1.25, 0, 1)`: 1.25 kW of panels per kW of inverter, capped at the inverter.

The output is indexed at the hour start.

### 8.2 Wind

A generic modern onshore turbine at 135 m hub height.

1. **Shear to hub height, hour by hour.** The shear exponent is measured each hour from the two ERA5 heights, `α = ln(v₁₀₀/v₁₀) / ln(10)`, clipped to [0.05, 0.45]. Hub speed is `v₁₀₀ × (135/100)^α`. A fixed exponent would miss the stable nights when shear is strong.
2. **Air density.** `ρ = 1.225 × 288.15 / (T + 273.15)`, and the density-equivalent speed is `v × (ρ/1.225)^(1/3)`, because power goes with `ρ·v³`.
3. **Park power curve.** The single-turbine curve in the TOML (cut-in 3 m/s, rated 12.5 m/s, cut-out 25 m/s) is convolved with a normal kernel of 0.8 m/s. Turbines across a park see different speeds in the same hour, so the park's curve is smoother than one turbine's, most visibly at cut-out.
4. **Losses.** 13%.

### 8.3 Yield statistics

`annual_hours` sums the capacity factor over each complete calendar year: the full-load hours of 2005–2025 (21 years).

```
P50          = mean of annual full-load hours
weather_sd   = their standard deviation / mean
P90 over n years = P50 × (1 − 1.2816 × sqrt(weather_sd² / n + model_uncertainty²))
```

Weather averages out over n years; the model error (5% solar, 8% wind) does not, because a biased yield assessment is biased every year.

| | P50 | Weather sd | P90, 1 year | P90, 10 years |
|---|---:|---:|---:|---:|
| Solar North | 1,274 | 2.9% | 1,180 | 1,191 |
| Solar East | 1,296 | 4.0% | 1,189 | 1,210 |
| Solar South | 1,432 | 3.0% | 1,326 | 1,339 |
| Solar West | 1,252 | 4.0% | 1,149 | 1,169 |
| Wind North | 3,490 | 5.2% | 3,063 | 3,125 |
| Wind East | 2,131 | 7.0% | 1,841 | 1,905 |
| Wind South | 1,479 | 10.9% | 1,223 | 1,314 |
| Wind West | 2,374 | 7.7% | 2,037 | 2,120 |

---

## 9. Capture rates and cannibalisation

`capture.py`. The curve's shape is today's. As solar and wind are built out, they depress the prices of their own hours further. This module turns a capacity path into a change in the capture rate, and measures how wrong that change can be.

### 9.1 Definitions

`capture.fleet` computes, per local month or year, for solar and for wind:

| Quantity | Definition |
|---|---|
| Baseload | Mean day-ahead price |
| Capture rate | (Σ generation × price / Σ generation) / baseload |
| Paid capture rate | The same over hours with price ≥ 0 only |
| Unpaid share | Share of output in hours with price < 0 |
| Penetration | Fleet generation / load |
| Negative hours | Count of hours with price < 0 |

Solar's capture price uses solar generation. Wind's uses onshore generation, while wind penetration counts onshore plus offshore. Months with fewer than 600 hours are dropped.

History: solar's annual capture rate was 0.98 in 2015, 0.76 in 2023, 0.59 in 2024, 0.52 in 2025 and 0.54 in 2026 to date. Wind's has stayed between 0.71 and 0.88. Negative hours went from 126 in 2015 to 576 in 2025.

### 9.2 The regression

```
capture_rate_m = α_month(m) + β × ln(penetration_m) + ε_m        over the last 36 months
```

Twelve month intercepts and one slope, by ordinary least squares (`capture.fit`). The standard error of β comes from the residual variance and the design matrix.

| | β | Standard error |
|---|---:|---:|
| Solar, all hours | −0.215 | 0.090 |
| Solar, paid hours | −0.145 | 0.053 |
| Solar, unpaid share | +0.086 | 0.085 |
| Wind, all hours | −0.037 | 0.075 |
| Wind, paid hours | +0.022 | 0.077 |
| Wind, unpaid share | +0.056 | 0.052 |

Three choices, each made from the data:

- **Month fixed effects.** Penetration is seasonal and so is the capture rate. Without the intercepts, the slope would be the difference between summer and winter. With them, it is identified by the same calendar month at different penetrations, which over this window is mostly capacity growth: installed solar went from 75 to 119 GW, and capacity explains 73% of the variance of log penetration within calendar months.
- **A 36-month window.** The slope over the whole of 2015–26 mispredicts 2026: summer solar share rose 36% over 2024 while capture held flat, after the 2025 Solarspitzengesetz and about 20 GW of batteries. A recent window describes the current regime of storage and curtailment rules, and it is the same window as the price shape.
- **Log, not linear.** A linear slope forecasts one month ahead slightly better but extrapolates to a capture rate of 0.16 by 2036. The log form builds in diminishing marginal cannibalisation, which is what storage and curtailment produce.

### 9.3 From a capacity path to a capture-rate change

`penetration_path` takes today's penetration by calendar month (the window average) and scales it:

```
penetration(month, year) = today(month) × GW_year / GW_window_mean / (1 + load_growth)^(year + 0.5 − window_middle)
```

Window-mean capacity is 96.7 GW for solar and 74.5 GW for wind. Load grows 1% a year from the middle of the window, so the same build-out is a smaller share of a larger market. Solar penetration in June goes from 0.28 today to 0.36 in 2027 and 0.60 in 2036.

`cannibalisation` is then the model at the future penetration minus the model at today's, by month and delivery year. The intercepts cancel, so it is `β × ln(future / today)`: for solar −0.054 in 2027, growing to −0.164 in 2036; for wind −0.005 to −0.021. As a fleet capture rate: solar 0.56 today, 0.50 in 2027, 0.39 in 2036; wind 0.84 to 0.82.

The change is relative to today's shape because the curve and the simulation already contain today's capture rate. Only the change is added, in the valuation.

### 9.4 Two errors

**The slope error** (`slope_error`). One standard error of β as a capture-rate change, `se × ln(future / today)`. It is the same error in every delivery year and it grows with the build-out. Thirty-six months pin the slope down only loosely, and this is the one number that turns the capacity path into a price.

**The year-ahead error** (`year_ahead`). Each calendar year from 2018 is predicted by the model fitted on the 36 months before it, given the year's actual penetration. The error is therefore what penetration does not explain: weather, fuel prices, rule changes. Monthly errors are weighted by share × price.

| | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | RMS | ρ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Solar | +0.007 | −0.032 | −0.092 | −0.005 | +0.027 | −0.067 | −0.169 | −0.099 | +0.037 | 0.078 | 0.13 |
| Wind | +0.049 | +0.046 | −0.011 | −0.034 | −0.015 | +0.027 | +0.001 | +0.037 | +0.040 | 0.033 | 0.40 |

`sd` is the root mean square error and `ρ` the correlation of one year's error with the next (clipped to [0, 0.9]). The valuation simulates both.

### 9.5 Backtest one month ahead

From January 2021, each month is predicted by the model fitted on the 36 months before it, given that month's realised penetration. The benchmark is the same calendar month's average capture rate over the previous three years.

| Mean absolute error, 70 months | Model | Seasonal naive |
|---|---:|---:|
| Solar | 0.090 | 0.113 |
| Wind | 0.061 | 0.061 |

Wind's model is no better than naive, which fits its weak penetration effect.

### 9.6 The window is the largest uncertainty

The slope is refitted on other windows and the headline deal repriced, with everything else unchanged:

| Months | Solar slope | 10-year solar, €/MWh | Wind slope | 10-year wind, €/MWh |
|---|---:|---:|---:|---:|
| 24 | +0.01 ± 0.15 | 44.76 | −0.09 ± 0.10 | 72.95 |
| 36 | −0.22 ± 0.09 | 38.30 | −0.04 ± 0.08 | 75.42 |
| 48 | −0.33 ± 0.07 | 33.92 | −0.05 ± 0.06 | 75.65 |
| 60 | −0.47 ± 0.07 | 28.19 | −0.05 ± 0.05 | 75.65 |

The window moves the solar price by more than the risk premium, and by more than the slope's own standard error suggests. The fundamental model gives the other bound: with 10% more solar and nothing else built, solar's capture rate falls by 0.98 per log share and wind's by 0.30 (section 15.5). History's slope is flatter by what was built alongside: batteries went from 8 to 22 GW inside the window. The 10-year price therefore assumes flexibility keeps pace with solar as it has since 2023.

### 9.7 The negative-price clause

Two more regressions of the same form split the slope: the paid-hours capture rate (`rate_paid`) and the unpaid share (`unpaid`). `paid_volume` turns the unpaid share's regression into the volume paid under the clause relative to today's:

```
paid factor(month, year) = (1 − unpaid_then) / (1 − unpaid_today),   unpaid_then = unpaid_today + β_unpaid × ln(future / today)
```

clipped so the unpaid share stays within [0, 0.95]. For solar in May the unpaid share today is 35%, and the paid factor is 0.97 in 2027 and 0.90 in 2036.

---

## 10. Forward curve history

`forwards.py`. Input: OMIP's daily reference prices. Everything here is measured, because there are no free implied vols.

### 10.1 Cleaning and returns

- `clean` drops days on which no contract moved: on exchange holidays OMIP republishes the previous settlement. 2,655 settlement days remain.
- `returns` takes daily log returns within each contract, never across a roll, and labels each with `tau`, the years to the start of delivery. Contracts in delivery are excluded.

### 10.2 Volatility by time to delivery

`term_structure` is the annualised standard deviation (√252) of calendar-contract returns, bucketed by time to delivery.

| Years to delivery | Last 24 months | Since 2016 |
|---|---:|---:|
| < 1 | 27.2% | 41.4% |
| 1–2 | 18.5% | 31.4% |
| 2–3 | 14.4% | 25.2% |
| 3+ | 11.8% | 24.9% |

This is the Samuelson effect: a contract moves more as delivery approaches. `vol_curve` turns the 24-month column into a step function of time to delivery, flat within each bucket and beyond the last. The simulation uses it as its forward vol. The full history includes 2021–22 and is shown for comparison only.

### 10.3 One-factor check

`curve_correlation` is the correlation of daily returns of the next and the third calendar year over the last 24 months: 0.77. The simulation moves all years with one factor, which is a perfect correlation. This number says how much of a simplification that is.

### 10.4 Hedge backtest

`hedge_backtest` asks whether the hedging rule would have worked. For each delivery year 2017–2025:

- **The deal.** A one-year pay-as-produced PPA on the German fleet profile scaled to 100 MW, bought on the last settlement day of October before delivery.
- **The price.** That day's Cal forward times the fleet capture rate of the 36 months before. Both were known then.
- **Three ways to hold it.** Unhedged; short P50-equivalent MW of Cal baseload (volume hedge); short that times the expected capture rate (value hedge, the dashboard's rule).

| P&L volatility across years, €m | Unhedged | Volume hedge | Value hedge |
|---|---:|---:|---:|
| Solar | 8.7 | 1.7 | 1.3 |
| Wind | 14.4 | 5.4 | 1.8 |

The value hedge is smaller than the volume hedge because a plant with a capture rate below one earns less than one-for-one from a move in baseload. For wind in 2022–23, the volume hedge lost €9.4m and then made €11.4m; the value hedge lost €4.8m and €1.9m.

### 10.5 Spark-spread inputs

`spark_inputs` measures the vols and correlations the spread option needs, over the last 24 months:

| Input | How | Value |
|---|---|---:|
| Power vol | Daily returns of monthly contracts within two months of delivery | 44% |
| Carbon vol | Log changes from one EUA auction to the next, annualised by auctions per year | 28% |
| Gas vol | Monthly averages, scaled by √(3/2) | 64% |
| Power–gas correlation | Changes in monthly averages | 0.87 |
| Power–carbon | same | 0.35 |
| Gas–carbon | same | 0.06 |

Gas has no free daily price. The change between two months' averages of a random walk has two thirds of the variance of a month-end to month-end change (Working, 1960), so `averaged_vol` scales the monthly figure up by √(3/2). Correlations are those of monthly-average changes, which daily settlements taken at different hours would understate. Power is there the next calendar year, within a contract; January's change is dropped because the January average belongs to the next contract. Gas vol and the gas correlations rest on about 22 monthly changes.

---

## 11. Joint price and generation simulation

`simulate.py`. This is the centre of the valuation: hourly prices and site generation, drawn together, for every delivery year.

```
P_h = L_y × (F_h + D_h + M_h × du_h) − (D_h + dn_h)
```

| Symbol | Meaning |
|---|---|
| `F_h` | The hourly curve |
| `M_h` | The curve's monthly baseload level |
| `D_h` | The expected negative part for the hour's cell, `shape.down` |
| `du_h` | Positive part of a recent historical price relative to its month's level, minus its cell mean |
| `dn_h` | Negative part of the same price in €/MWh, minus its cell mean |
| `L_y` | A shock to the calendar year's price level, mean one |

`F + D` is the expected positive part of the price. The level shock multiplies the positive part and its residual; the negative part and its residual are left in €/MWh. Since `E[L] = 1`, `E[du] = 0` and `E[dn] = 0`, the simulation is unbiased: `E[P_h] = F_h`. The curve stays arbitrage-free under simulation, and the futures hedge has zero expected P&L.

### 11.1 Resampling days, not assuming a correlation

The central difficulty in a PPA is the dependence between the plant's output and the price: a sunny noon is a cheap noon. Rather than model that dependence, the simulation resamples it. For each simulated day it picks one historical day from the shape window and takes from it both the price residuals (`du`, `dn`) and the site's capacity factor (`cf`) in every hour. Whatever joint behaviour of weather and price was in that day comes along.

`simulate.pool` builds the pool: 1,096 days (October 2023 – September 2026), each with 24 values of `du`, `dn` and `cf` on a local-hour grid. On a 25-hour day the repeated hour is averaged; on a 23-hour day the missing one is filled.

`_analogues` chooses the candidates for a future day: the same calendar date in each pool year, shifted by up to three days so the weekday matches. A Tuesday in July 2031 can draw the nearest Tuesday in July 2024, 2025 or 2026. Candidates are padded to 12 columns by repetition; 12 is divisible by every possible count, so a uniform draw gives each candidate equal weight.

Days are drawn in **weekly blocks**: one pool year is picked per path and week, and all seven days come from it. Weather systems and price regimes last longer than a day, and a block keeps that persistence.

### 11.2 Volume

Three years of pool days do not span long-run weather variability. Each path-year's capacity factors are therefore rescaled so the year's total equals a target:

```
target_flh(path, year) = (a random ERA5 year's full-load hours, 2005–2025)
                         × (1 + model_sd × z_path)
                         × (1 − degradation)^(years since start)
```

The first term gives each year an independent draw from the 21-year record. The second is the yield model's error, drawn once per path and kept for every year. The third takes 0.4% of a solar plant's output per year of age. The hourly pattern comes from the pool; the annual total from the long record.

### 11.3 The level shock

`level_shocks`. One Brownian path moves every calendar year, so levels move together. At each moment, a year moves with the vol of a contract that far from delivery:

```
log L_y = Σ_t σ(start_y − t) × sqrt(exposed_{y,t}) × Z_t  −  ½ Σ_t σ(start_y − t)² × exposed_{y,t}
```

in monthly steps `t` from the valuation date. `start_y` is the time to the year's delivery start, `σ` the measured vol curve, and a year stays exposed until its mid-delivery (1 July); in delivery it keeps the front vol. The same `Z_t` drives every year. The levels are then divided by their sample mean, so the shock adds no sample bias.

The standard deviation of the log level is 0.22 for 2027, 0.38 for 2029 and 0.50 for 2036. A far year therefore moves less in total than a flat front vol would give it: it spends most of its life in the 12% bucket.

### 11.4 The two capture draws

Two standard normal draws ride along for the valuation: `slope_error`, one per path, and `capture_noise`, one per path and year. They come from their own random streams (`[seed, 1]` and `[seed, 2]`), so adding them left every other draw as it was. Each is regressed on a constant, every year's level and every year's level × volume, and the residual is standardised. They are therefore exactly orthogonal to the level in the sample: they add risk without moving the expected price, and no baseload hedge can reach them.

### 11.5 What a simulated year returns

`run` loops over delivery years and returns one `YearSim` per year. Hourly arrays (800 paths × 8,760 hours) are reduced to per-path totals:

| Field | Definition | Used for |
|---|---|---|
| `volume`, `revenue` | Σ G, Σ G·P | The deal |
| `spot` | Σ P: € per MW of baseload | The hedge |
| `volume_pos`, `revenue_pos` | The same over hours with P ≥ 0 | The clause |
| `revenue_det`, `volume_det_pos`, `revenue_det_pos` | With expected generation E[G] in place of G | Shape risk alone |
| `revenue_fwd`, `volume_fwd_pos`, `revenue_fwd_pos` | With the curve F in place of P | Volume risk alone |
| `put` | Σ G·max(−P, 0) | The put strip |
| `put_normal` | Σ E[G] × Bachelier put(F, strike 0, sd of P) | The normal-model comparison |
| `profile_value` | Σ E[G]·F | The profile step |
| `monthly_volume`, `monthly_volume_pos`, `monthly_base` | Expected by month | Weighting the capture-rate change |
| `negative_hours` | Count of P < 0 | Diagnostics |
| `level`, `slope_error`, `capture_noise`, `tau`, `base`, `hours` | The draws and the year's facts | Valuation |
| `daily_spread` | Best daily 2-hour battery cycle, first year only | Battery |

The seed is the same for every technology and site, so differences between sites are differences in weather, not in random numbers.

---

## 12. Valuation

`valuation.py`. The desk buys the plant's output at a fixed price `K` and sells calendar baseload futures against it.

```
deal_y   = revenue_y + (cannibalisation_y − balancing) × volume_y − K × volume_y
hedge_y  = −q_y × (spot_y − E[spot_y])                          q_y in MW
P&L      = Σ_y DF_y × (deal_y + hedge_y)
```

per path. `DF_y = exp(−r(τ_y) × τ_y)` discounts each year from its mid-delivery, with `r` interpolated linearly on the ECB curve. The futures settle against the simulated mean of spot, not the curve's `hours × base`, so the hedge adds no sample bias.

### 12.1 Cannibalisation enters as a share of the price

`cannibalisation_adj` converts the capture-rate change (by month and year, from section 9.3) into €/MWh for each delivery year: the change times the curve's monthly baseload, weighted by expected monthly volume. Applied to the two errors, the same function gives the slope error and the year error in €/MWh.

`cannibalisation_paths` then builds the path-wise value:

```
cann(path, year) = (adj_year + adj_sd_year × slope_error_path + year_sd_year × e(path, year)) × L(path, year)
```

- A capture rate is a share of the price, so its € value moves with the level shock `L`. This is why the hedge shrinks as the capture rate falls: less of the plant's revenue is exposed to the level.
- `slope_error` is one draw per path shared by every year. It is the one risk that does not diversify over the tenor.
- `e` is the year effect, an AR(1) built from `capture_noise` in `year_effects`: `e_j = ρ × e_{j−1} + sqrt(1 − ρ²) × noise_j`, with unit variance. `year_sd` is the year-ahead RMS error (0.078 solar, 0.033 wind) times the volume-weighted baseload.

Because cannibalisation is applied to the plant's revenue and not to the hourly curve, the curve for 2036 keeps today's shape. That is a stated limit.

### 12.2 The price before risk

`Deal.expected_price` is the `K` at which expected discounted P&L is zero:

```
K₀ = Σ_y DF_y × E[cash_y] / Σ_y DF_y × E[volume_y],     cash = revenue + (cann − balancing) × volume
```

### 12.3 The hedge

`Deal.hedge` is the minimum-variance amount of baseload per year: the regression coefficient of the deal's cash flow on spot.

```
q_y = Cov(cash_y − K × volume_y, spot_y) / Var(spot_y)
```

`spot` is in € per MW, so `q` is in MW. For the 10-year solar deal it is 9.4 MW in 2027 against a P50-equivalent of 16.3 MW (57%), stepping to 7.2 MW by 2036. It is below the P50-equivalent because the plant's revenue moves less than one-for-one with baseload (its capture rate is below one, and volume is negatively related to price), and it steps down as the capture rate falls. For wind it is 35.1 MW against 40.0 MW.

### 12.4 The risk premium

`_premium` solves, by Brent's method, the price cut `x` that makes the chosen quantile of hedged lifetime P&L exactly zero:

```
quantile_10%( P&L(K₀ − x, q) ) = 0        →        fair = K₀ − x
```

The desk breaks even or better in 90% of paths. The hedge is the one sized at `K₀`. The confidence level is a choice, so the premium is also solved at P75 and P95:

| 10-year solar | Premium | Price |
|---|---:|---:|
| P75 | 3.23 | 41.39 |
| P90 | 6.32 | 38.30 |
| P95 | 8.30 | 36.32 |

The premium per MWh is largest on short tenors: €16.85 for one-year solar, because the year-to-year capture error has no other years to average against.

### 12.5 The steps of the price

Each step is a difference of discounted, volume-weighted averages, so the steps add up to the price. They are rounded first and the price is their sum, so the steps as shown add up to the price as shown.

| Step | Definition | Solar S, 10y | Wind N, 10y |
|---|---|---:|---:|
| Baseload forward | Calendar base prices, weighted by discounted expected volume | +96.63 | +96.41 |
| Profile | Σ E[G]·F per MWh, less the base: the expected hourly profile valued on the curve | −35.58 | +2.81 |
| Price–volume correlation | E[G·P] per MWh less the two above: output is high when prices are low | −4.44 | −15.02 |
| Cannibalisation | E[cann × volume] per MWh | −9.71 | −1.15 |
| Risk premium (P90) | −x | −6.32 | −4.53 |
| Balancing | Section 16 | −2.49 | −2.82 |
| Hedge cost | Below | −0.65 | −1.12 |
| Credit | Below | −0.14 | −0.16 |
| Guarantees of origin | `[commercial].origin` | +1.00 | +1.00 |
| **Price** | | **38.30** | **75.42** |

Solar's discount is mostly profile: it produces at midday, in summer. Wind's expected profile is worth slightly more than baseload (it produces more in winter), and its discount is the correlation: windy hours are cheap whenever they occur.

By tenor, the solar South price falls from €71.35 for one year to €38.30 for ten, and wind North from €132.61 to €75.42, mostly because the curve is backwardated (Cal-27 at €164, Cal-33 at €78).

### 12.6 Commercial terms

These turn the model price into a bid. They are steps of the price that leave the model deal and its hedge alone. All inputs are indicative.

**Hedge cost** (`hedge_cost`), per MWh of baseload sold, by delivery year:

```
cost_y = half bid-offer + funding_spread × cover × (∫ vol dt to mid-delivery) × base_y
cover  = Φ⁻¹(0.99) × sqrt(2 / 252) = 0.207
```

Half the spread is €0.25 for the three liquid calendar years and €1.50 beyond. Initial margin is the move a clearing house covers: a 99% normal quantile over a two-day holding period, at the vol of a contract that far from delivery. It is posted from today to mid-delivery and funded at 1.5%. The result is €0.34 per MWh hedged for 2027, €0.44 for 2029 and €1.71–1.85 from 2030. `_terms` converts it to € per MWh bought: `Σ DF × |q| × hours × cost / Σ DF × E[volume]`.

**Credit** (`credit_loss`). The expected loss if the generator defaults. The hedge sits on the exchange and offsets none of it, so the exposure is the PPA alone: what the remaining years are worth to the desk, when they are worth something.

```
loss = (1 − recovery) × Σ_j hazard × exp(−hazard × j) × E[max(W_j, 0)]
```

`W_j` is the value of year `j` and every year after it at the start of year `j`: the expected discounted P&L of those years given the price level of the year before, by a quadratic regression on its log. Before the first year it is the expected lifetime P&L. In a backwardated curve a fixed price is worth most in the early years, so most of the exposure is there.

**Guarantees of origin** come with the power and add their value, €1.00/MWh.

### 12.7 What the hedge leaves

Lifetime P&L volatility, and each source on its own:

| €m | Solar S, 10y | Wind N, 10y | How it is measured |
|---|---:|---:|---|
| Unhedged | 18.7 | 74.3 | `pnl(fair, None)` |
| Hedged | 6.5 | 11.4 | `pnl(fair, q)` |
| Cannibalisation slope | 5.2 | 7.4 | `adj_sd × slope_error × L × volume`, discounted |
| Capture rate, year to year | 3.4 | 4.8 | `year_sd × e × L × volume`, discounted |
| Hourly shape | 0.8 | 0.8 | Expected generation against simulated prices, under its own hedge |
| Volume | 0.7 | 5.0 | Simulated generation against the curve and the expected cannibalisation |

The shape-only deal gets its own hedge. Expected generation earns the profile's price, not the correlation-reduced one, so it carries more of the level than the plant does; under the deal's hedge part of it would be left open and the number would overstate shape risk. The parts do not add up to the hedged figure: the rest is price and volume moving together. Also reported are the 10th percentile of hedged P&L (zero by construction), the expected shortfall beyond the 5th percentile (−€6.7m for solar), the discounted volume (1,202 GWh) and a 36-bin histogram of hedged P&L.

After the hedge, the 10-year solar deal's risk is mostly the cannibalisation slope. On a one-year deal it is mostly the year-to-year error.

### 12.8 The negative-price clause

A second structure, not a step of the first: only output in hours at or above zero is paid. Its price is per paid MWh, so it is higher on less volume.

- **Volume and revenue** are the simulated `volume_pos` and `revenue_pos`, scaled each year by the paid factor of section 9.7. The paths keep today's negative hours; the factor makes the paid share shrink with the build-out. Solar is paid for 78% of its output in 2027 and 74% in 2036; wind for 91% and 89%.
- **Cannibalisation** is that of the paid hours alone (the `rate_paid` regression and its standard error). The part of the capture-rate drop that comes from more negative hours is already in the volume.
- **Price, hedge, premium and terms** are solved again for this deal: €61.94 for solar, €85.79 for wind.

The strip of hourly puts struck at zero, `E[G × max(−P, 0)]` per MWh produced and scaled up with the unpaid volume, is the smaller part of the clause: €4.84 for solar and €1.27 for wind. Most of the higher price comes from the volume that is no longer paid. Section 13.1 compares the simulated strip with a normal model.

### 12.9 The baseload-shaped alternative

Instead of selling as produced, the generator sells its P50-equivalent MW as baseload at the forward and keeps shape and volume risk on every MWh. The output shows the baseload price (€96.40, hours-weighted) and the realised revenue per MWh the generator would keep: P10–P90 of €24.02–60.46 for solar and €72.63–86.16 for wind. It makes visible what the pay-as-produced price takes off the generator's hands.

---

## 13. Options

`options.py`.

### 13.1 Negative-price puts

The simulated strip is `Σ G × max(−P, 0)`. For comparison, `bachelier_put` values each hour as a put struck at zero under a normal distribution with mean `F_h` and the simulated standard deviation of `P_h`:

```
put = (K − F) × Φ(d) + sd × φ(d),     d = (K − F) / sd
```

Across all sites and tenors the normal model is within 15% of the simulated strip for solar (0.87 to 1.09 times it; €5.20 against €4.84 for the headline deal) but between 0.73 and 1.85 times it for wind (€1.96 against €1.27), because negative prices are bounded and do not scale with the level, and a normal tail does. The comparison is the reason the clause is valued by simulation.

### 13.2 Clean spark spread

A call on `power − heat_rate × gas − emission_factor × carbon − VOM` for the front month (Dec-26), expiring at delivery start, 0.088 years out. Two methods:

- **Kirk's approximation.** `fuel_basket` moment-matches `HR × gas + EF × carbon` to one lognormal (forward and vol), `basket_corr` gives its log-correlation with power, and `kirk` values the call on `F₁ − F₂ − K` by treating `F₂ + K` as lognormal, which reduces it to Black's formula.
- **Monte Carlo.** Three correlated lognormal forwards, 400,000 antithetic draws.

Intrinsic €8.87, Kirk €11.90, Monte Carlo €11.91 ± 0.01 at the measured correlation of 0.87. The value is swept over the power–gas correlation from 0 (€22.49) to 0.95 (€9.93); a sweep point is skipped if the three correlations cannot hold together (`consistent`: the correlation matrix must be positive definite). The option is here as a check of the pricing tools and of the inputs measured in section 10.5, not as part of the PPA.

### 13.3 Battery as a swing option

A 1 MW / 2 MWh battery on the 2027 price paths, at most 250 cycles a year.

- **Daily exercise value** (`simulate.battery_spread`): buy the two cheapest hours before a split, sell the two dearest after it, choosing the best split so charging always precedes discharging: `sell − buy / 0.88`.
- **Swing value** (`swing_lsmc`): least-squares Monte Carlo (Longstaff–Schwartz). Working back from the last day, for each number of rights left, the value of keeping a right is regressed on a cubic of the day's spread; the battery cycles today if today's spread beats that fitted value. Decisions use fitted values, cash flows realised ones.
- **Bounds.** Intrinsic: the 250 best days on the expected curve. Perfect foresight: the 250 best days of each path.

Intrinsic €105.0k, least-squares Monte Carlo €121.9k, perfect foresight €122.6k per MW-year. Because cannibalisation is not in the curve, this battery sees today's daily spreads at 2027's level.

---

## 14. Fundamental model

`fundamental.py`. An hourly merit order of the German day-ahead market, built only from free data. It exists for three reasons: to test the futures against a fundamental fair value, to give fuel and carbon sensitivities, and to bound the cannibalisation slope from below.

Each hour, supply at price `p` plus net storage output must meet demand:

```
must_take + thermal(p) + wind(p) + solar(p) + trade(p) + demand_response(p) + surplus_response(p) + storage = demand
```

Supply rises with price, so the clearing price is found by bisection on [−500, 4,000] for all hours at once (32 halvings, `Market.clear`).

### 14.1 The pieces

**Demand** is observed: all generation plus net imports plus net storage output. It is what the public grid served, and it is inelastic except for the backstops.

**Must-take supply** is observed: nuclear, run-of-river, reservoir hydro, biomass, geothermal, waste, others and coal-derived gas.

**Wind and solar** start from the day-ahead forecast, which is what the market cleared on, scaled by `level`: actual output per MWh of forecast in training hours priced above €20, where nothing has a reason to curtail. Solar's forecast runs 3–6% above output (0.953 for 2023–25), wind's is level (0.999). Demand is built from actual output, so an unscaled forecast would be 1–2 GW of phantom surplus at every sunny midday. Below zero:

```
output = forecast × level × max(1 − zero × Φ(−p / 0.5) − curtail × (1 − exp(min(p, 0) / scale)), 0)
```

A block (`zero`) switches off at once at €0: subsidised plants lose their market premium when the price is negative, so their marketers bid at zero. More (`curtail`) backs off gradually as the price falls. The rest, rooftop solar and old fixed-tariff plants, runs at any price. Wind and solar have their own shares, because wind curtails and solar barely does.

**Thermal plants**: lignite, hard coal, gas, oil.

- *Availability and must-run are read from dispatch, not fitted* (`fleet`). Per fuel and calendar month over the training window, availability is the 99th percentile of output over installed capacity (floored at 85% of the window's peak, so a month that peaked low only because the fuel was out of the money does not cap it), and must-run is the 2nd percentile: what runs in the month's quietest hours (heat-led CHP, inflexible units). For 2023–25, lignite's must-run is 15–20% of capacity and its availability 74–92%.
- *The flexible rest bids marginal cost.* For each of 8 efficiency bins of the fuel's real fleet: `cost = (fuel + emissions × CO₂) / efficiency + VOM + markup`. Gas efficiencies run from 36% to 60%, coal 37–46%, lignite 32–43%.
- *Bid noise.* A plant does not switch on exactly at its cost. Output per bin is `Φ((p − cost) / σ)`, with `σ = noise × median cost + noise_floor`. This smooths the supply curve and stands in for start-up costs and part-load.
- *Commitment.* A fitted share of the flexible output is committed for the day: it follows the day's mean merit-order dispatch, not the hour's price. Plants needed for the evening stay on through a cheap midday, and cannot all ramp up for the peak. Committed plants back off only below a fitted shut-down price, `Φ((p − shutdown) / 10)`.

```
thermal_f(p) = must_run_f + committed_f(day) × Φ((p − shutdown) / 10) + (1 − share) × merit_f(p)
```

**Trade** is net imports (`trade_at`):

```
trade = c₀ + c₁ × (demand − must_take) + c₂ × wind_fc + c₃ × solar_fc + c₄ × abroad + response × z
z = 2Φ((p − ccgt) / (spread × ccgt)) − 1
```

A line in German fundamentals and in the neighbours' residual load (`abroad`), plus a bounded S-shaped response to the German price around a 55% gas plant's cost, sized separately for imports (`z > 0`) and exports. It is clipped to the 0.2% and 99.8% quantiles of training trade. Line and response are fitted together on observed trade and prices (`fit_trade`), so neither double counts. For 2023–25: each MW of wind forecast lowers net imports by 0.53 MW, each MW of solar by 0.40 MW, each MW of residual load abroad by 0.29 MW; the import response is 3.9 GW and the export response zero.

**Backstops.** At scarcity prices, 5 GW of demand backs off around €500. At very negative prices, 5 GW of load is taken on around −€150 (power-to-heat, extra pumping, plants running below cost). The second keeps a surplus off the −€500 floor.

**Storage** (`arbitrage`). Pumped hydro charges in each day's cheapest hours and discharges in its dearest: up to 6 hours at full power each way, pairing the cheapest with the dearest hour while `dear × 0.75 > cheap`. Its power is the 99th percentile of observed pumped-storage flows (6.0 GW).

### 14.2 Solving a day

Commitment and storage depend on the whole day's prices, which depend on commitment and storage. `solve` iterates: clear; commit against those prices; clear; then four rounds of schedule storage (half-way towards the new schedule), commit (half-way), clear. Damping makes both settle.

### 14.3 Calibration

Thirteen behavioural parameters are fitted by clearing the market (`fit`): a markup per fuel (4), bid noise and its floor (2), wind and solar gradual curtailment and its price scale (3), wind and solar zero-bid shares (2), the committed share and its shut-down price (2).

- **Target.** Hourly prices, generation of each of the four fuels, wind and solar output, and trade, all together. Fitting to prices alone would let a wrong fuel mix produce the right price.
- **Scaling.** Price errors are divided by `10 + 0.2 × |month's mean price|`, so 2022 does not drown out every other year. Dispatch errors are divided by 3,000 MW.
- **Loss.** Soft-L1 (robust to spikes), bounded least squares, at most 200 function evaluations.
- **Sample.** Every sixth day, whole, since commitment depends on the day. Observed storage is taken as given so the fit is about the merit order alone.

### 14.4 Backtest

Each test year from 2019 is predicted by a model fitted only on the three years before it, with that year's actual fuel prices, carbon, demand, weather and capacity. Storage is scheduled by the model. Years run in parallel.

The bar to clear is the hourly error of knowing each month's actual average price in advance: a model that gets the level right but has no hourly shape.

| | Actual | Model | Hourly error | Month-mean error | Solar capture | Model | Negative hours | Model |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2019 | 37.7 | 40.3 | 7.3 | 9.8 | 0.93 | 0.96 | 211 | 592 |
| 2020 | 30.5 | 33.4 | 8.0 | 10.3 | 0.81 | 0.88 | 298 | 425 |
| 2021 | 96.9 | 95.6 | 16.1 | 30.3 | 0.78 | 0.78 | 139 | 245 |
| 2022 | 235.4 | 207.5 | 55.9 | 80.2 | 0.94 | 0.85 | 69 | 306 |
| 2023 | 95.2 | 97.2 | 18.9 | 32.5 | 0.76 | 0.76 | 301 | 562 |
| 2024 | 78.5 | 72.1 | 18.0 | 31.9 | 0.59 | 0.61 | 457 | 861 |
| 2025 | 89.3 | 89.3 | 19.3 | 34.5 | 0.52 | 0.52 | 576 | 503 |
| 2026 | 113.0 | 105.8 | 23.3 | 46.7 | 0.54 | 0.53 | 475 | 662 |

The model beats the bar in every year. Solar capture is close from 2021. Negative hours are the least stable output: still 1.4–1.9 times the market's count in 2023, 2024 and 2026.

Fitted parameters by test year show what is and is not pinned down:

| | Committed share | Shut-down price | Wind at zero | Solar at zero | Gas markup |
|---|---:|---:|---:|---:|---:|
| 2019 | 0.06 | −19.4 | 0.00 | 0.07 | 8.1 |
| 2021 | 0.50 | +1.6 | 0.01 | 0.00 | 2.5 |
| 2023 | 0.46 | −0.2 | 0.05 | 0.00 | 2.3 |
| 2025 | 0.48 | +4.9 | 0.09 | 0.06 | 4.5 |
| 2026 | 0.34 | −4.3 | 0.10 | 0.02 | 5.6 |

From 2021 a third to a half of flexible output is committed and the shut-down price lands within €6 of zero. The share of wind bidding at zero rises from nothing to 0.10, which is what more plants falling under the negative-price rule would produce. The oil markup is not identified: it runs from −38 to +150 across the eight fits.

### 14.5 Five findings that shaped it

- **Trade has to see the neighbours.** Germany's net position swung by about 5 GW between 2022 and 2024 as French nuclear recovered. Trade fitted on German conditions alone priced 2024 €36 too high; the neighbours' residual load brought it within €10.
- **Exports saturate at sunny middays,** because the neighbours have solar too. A price response that let exports keep growing put 2025 midday prices at €92 against €46.
- **The solar forecast runs high,** hence the `level` scaling.
- **Wind curtails, solar barely does.** Against its forecast, wind output falls 5% at €0, 15% at −€20 and 44% below −€50. One shared curtailment curve left too many shallow negative hours and surpluses that hit the floor.
- **Thermal plants cannot follow the hour.** At actual prices, coal, lignite and gas ran about 2 GW more at midday and 3–6 GW less in the evening than an hour-by-hour merit order offers. Without commitment the day came out too flat (2025: €66 against €46 at 13:00, €116 against €136 at 19:00) and solar capture at 0.61 against 0.52.

---

## 15. Fair value against the market

`fairvalue.py`. At every month end since January 2019, the fundamental model prices the next calendar year with what was known that day, and the result is compared with the Cal futures settlement.

### 15.1 The scenario

`scenario` replays the twelve months before the date `t`, restated for delivery in the following year:

| Input | Value |
|---|---|
| Parameters and fleet | The backtest's fit for `t`'s year: the three whole years before it |
| Weather, demand, wind, solar, trade, storage | Those twelve months as they were |
| Nuclear | Scaled to the capacity left in each month of the delivery year (closures are set by law) |
| Thermal capacity | As installed at `t` |
| Gas, coal, oil | The last monthly average at `t`, held flat |
| Carbon | The last EUA auction before `t`, held flat |

The fair value is the mean clearing price: what the next year would cost if the system and fuel stayed as they are.

Older weather is deliberately not rescaled to today's wind and solar capacity. The flexibility that grew alongside them (batteries, exports, curtailment) is buried in observed demand. Scaled up, the model saw 1,000–1,700 negative hours a year where the market had about 500, and priced Cal-27 at €90.

### 15.2 Today

On 30 October 2026, Cal-27 settled at €164.27 against a fair value of €133.08, a gap of −€31.19. The futures rose €36 in October. The model's gas is September's average, €75.61 as quoted; at the mid-October quote of €88.41 the fair value would be €140.73 (the gas sensitivity times the difference), so stale fuel explains a quarter of the gap.

### 15.3 What sets the price

`drivers` clears the scenario, then moves the price by ±€1 and records which sources respond. A source's share is its part of the price-responsive supply at the clearing price, averaged over hours.

| Source | Share | Bids at that day's fuel and carbon, €/MWh |
|---|---:|---:|
| Lignite | 31% | 97–128 |
| Hard coal | 28% | 127–156 |
| Gas | 25% | 177–294 |
| Wind and solar (curtailment) | 9% | |
| Trade and other | 8% | |

### 15.4 Sensitivities

`sensitivities` clears the scenario again with one input bumped up and down (central difference: gas ±€5/MWh as quoted, carbon ±€10/t, coal ±€10/t) and reports the change in the baseload price and in the solar and wind capture prices.

| Per unit | Level | Baseload | Solar capture price | Wind capture price |
|---|---:|---:|---:|---:|
| Gas, €1/MWh | 75.61 | +0.60 | +0.21 | +0.45 |
| Carbon, €1/t | 88.05 | +0.66 | +0.44 | +0.65 |
| Coal, €1/t | 133.72 | +0.09 | +0.06 | +0.09 |

Gas is marginal in a quarter of the hours, which is why its delta is 0.60 and not a gas plant's heat rate of 2.0. A solar capture price takes 36% of baseload's move when gas drives it and 66% when carbon does, because coal and lignite set the midday price more often than gas does. The valuation's hedge ratio (57% for 2027 solar) sits between the two: the right hedge depends on what moves the level.

### 15.5 Build-out with nothing else built

`buildout` scales the solar (or wind) forecast up by 10% and reports the change in capture rate per log share: −0.98 for solar and −0.30 for wind. No storage, flexible demand or export capacity is built alongside, so this is the steepest the cannibalisation slope can be. The regression's −0.22 is flatter by what was built with it.

### 15.6 Is the gap a signal?

`next_change` takes each contract's change from one month end to the next on which it still trades (a December gap has no next month and is skipped). `signal_stats` trades the sign of the signal one month at a time.

| 86 months | Gap | Momentum | Always long |
|---|---:|---:|---:|
| Hit rate | 64% | 59% | 56% |
| Mean, €/MWh a month | +4.32 | +8.48 | +3.00 |
| t-statistic | 1.1 | 2.2 | 0.8 |

Not yet a signal. Last month's move does better, mostly from the 2021–22 trend. The reason is in the inputs: fuel enters at the month's spot price held flat, because free gas and coal forward curves do not exist. When the fuel curve is in steep backwardation or contango, as in 2021–23, the gap mostly measures that and not mispricing.

`delivery_years` compares, per delivery year, the mean futures and fair value over the month ends before it with the realised price. Fair value was closer than the futures in 2020, 2023 and 2024, and further in 2021 and 2025; both missed the crisis by more than €100.

---

## 16. Balancing cost

`build._balancing`. A pay-as-produced buyer takes the plant's forecast error to the imbalance market.

```
balancing = (Σ |actual − day-ahead forecast| / Σ actual) × single_asset_factor × imbalance_spread
```

The first term is the fleet's day-ahead forecast error over the shape window: 8.3% of output for solar and 9.4% for onshore wind. A single site forecasts worse than the fleet even after pooling in a direct marketer's portfolio (×1.5), and each MWh of error costs the assumed gap between the imbalance price and day-ahead (€20). Result: €2.49/MWh for solar, €2.82 for wind.

The spread is assumed because reBAP data needs registration. It is the first thing a desk would replace.

---

## 17. Data quality checks

`quality.py`. Each check is one row on the Data view: what was tested, the measured value, and whether it passed.

| Check | Passes if | Build of 9 Nov |
|---|---|---|
| Day-ahead hours present | < 0.1% missing | 103,679 of 103,680 |
| Longest price gap | ≤ 6 h | 1 h |
| Prices outside −500…4,000 | none (harmonised SDAC limits) | 0 |
| Price vs SMARD, hours within €0.01 | > 99% | 100% of 26,304 |
| Price vs SMARD, largest gap | < €1 | €0.01 |
| Solar vs SMARD, energy | within 3% | **−3.24%, flagged** |
| Solar vs SMARD, hourly correlation | > 0.99 | 0.9994 |
| Solar above 500 MW at night | 0 h (sun more than 10° below the horizon) | 0 |
| Solar, onshore wind above installed capacity | < 24 h above 102% | 0, 0 |
| Negative generation values | none | 0 |
| Day-ahead forecast coverage | > 98% of the window | 100% |
| HPFC repricing, largest error | < €0.01 | €0.0025 |
| Q1-27, Cal-27 implied by finer contracts | within €0.01 of the quote | 220.587, 164.269 |
| Futures inputs vs OMIP history | all typed quotes within €0.005 | 19 matched |
| OMIP settlement days | > 2,000 | 2,655 |
| Fundamental model, last full year | hourly error below the month-mean bar | 19.3 vs 34.5 |
| EUA auction days | > 2,500 | 2,969 |
| ERA5 gaps, each site | 0 h | 0 |

One check is flagged on purpose: Energy-Charts solar energy runs 3.2% below SMARD while the hourly correlation is 0.9994. The repo does not settle which source is right; the check is left failing so the difference stays visible.

---

## 18. Outputs: data.json, MEMO.md and the site

### 18.1 `site/data.json`

| Key | Content |
|---|---|
| `meta` | Valuation date, start, capacity, delivery years, site names, headline sites, paths, futures source, build time, the sources list and the assumptions list |
| `scenarios[tech][site][tenor − 1]` | One valuation: `price`, `steps`, `premiums` (P75/P90/P95), `hedge` (MW and P50 MW per year), `pnl` (histogram), `risk`, `baseload`, `negative` |
| `yield[tech][site]` | Annual full-load hours, P50, weather sd, P90 over 1 and 10 years |
| `curve` | Daily curve, hourly grid (local date × hour), the quotes with their repriced values, the first year's month × hour profile |
| `forwards` | Vol term structures, the 1–3 year correlation, the next-calendar price history, the hedge backtest |
| `capture` | Annual history; per technology the slopes, backtest, year-ahead errors, today's and projected fleet capture rate, and the window refits |
| `balancing` | €/MWh per technology |
| `options` | Spark spread (inputs, vols, correlations, sweep) and battery |
| `fundamental` | Backtest metrics per year, monthly actual and model, the last full year's average day |
| `fair_value` | Monthly futures and fair value, today's gap, drivers, build-out slopes, sensitivities, signal statistics, cumulative gap trade, by delivery year |
| `quality` | The checks |

The file is written with `allow_nan=False`, so a NaN anywhere fails the build instead of reaching the site.

### 18.2 `MEMO.md`

Generated by `build.memo`: for each headline deal the bid, the clause price, the steps table, the hedge, the residual risk, the yield and the baseload alternative, followed by the assumptions and one paragraph each on the fundamental model and the fair value. Never edit it by hand; it is overwritten on every build.

### 18.3 The site

Plain ES modules, no build step. `app.js` keeps state in the URL hash (`#price?tech=solar&site=south&tenor=10`) and renders one view per sidebar item; `charts.js` draws SVG (lines, bars, waterfall, histogram, heatmap). Technology, site and tenor controls appear on the views that depend on a deal.

| View | Shows | From |
|---|---|---|
| Price | The bid, the waterfall of steps, price by tenor (click to pick), the three structures | `scenarios` |
| Hedge | Unhedged and hedged volatility, MW to sell per year, P&L histogram, what the hedge leaves, premium by confidence, the historical hedge backtest | `scenarios`, `forwards.backtest` |
| Curve | Hourly heatmap by delivery year, daily curve against the futures, the average day, quotes against the curve, next-calendar history, vol by time to delivery, 1–3 year correlation | `curve`, `forwards` |
| Capture | Capture rates, history and projection; the solar one-month-ahead backtest; errors; slope and price by window, with the fundamental model's slope | `capture`, `fair_value.buildout` |
| Fundamentals | Monthly actual against model, the average day, capture and negative hours, the year-by-year backtest | `fundamental` |
| Fair value | Futures, fair value, gap, fair value at the gas quote; history; sensitivities; what sets the price; the gap trade | `fair_value` |
| Options | The clause; the spark spread across correlation with measured vols and correlations; the battery | `scenarios[…].negative`, `options` |
| Data | Checks, assumptions, sources | `quality`, `meta` |

Design rules (from `AGENTS.md`): three colours plus a diverging scale for heatmaps; no borders, legends or grid lines; titles and numbers only. If a view needs explanatory text, the view is simplified instead.

---

## 19. Tests

`.venv/bin/pytest` runs without network. `tests/conftest.py` builds three years of synthetic hourly prices with a duck curve, weekly and seasonal shape and noise, learns a shape from them, and builds a curve on the real quotes. The tests guard invariants, not numbers:

| File | What must hold |
|---|---|
| `test_hours.py` | DST days have 23 and 25 hours; contract periods parse; holidays count as Sundays |
| `test_hpfc.py` | Every quote reprices (independent ones to 1e-8); redundant quotes are detected and an inconsistent one flagged; no jumps at contract boundaries; no steps in the prior at month or year boundaries; extrapolated years are flat; a trend does not read as seasonality; negative prices do not scale with the level; the blend keeps every period mean; peak quotes are honoured |
| `test_generation.py` | The sun is due south and high at solar noon in June; PV is zero at night and clipped; the wind curve is monotone to cut-out; P90 is below P50 and tighter over ten years |
| `test_capture.py` | The fit recovers a known slope within its standard error; a shorter look-back uses the trailing months alone; paid volume shrinks with the build-out; the slope error is one standard error of the change; the year-ahead error recovers what penetration does not explain; load growth makes the same build-out a smaller share |
| `test_forwards.py` | OMIP pages parse; republished days are dropped; realised vol recovers a known vol; a one-factor curve has correlation one; vol from monthly averages recovers the daily vol |
| `test_simulate.py` | Level shocks have mean one and the right variance; far years move less; analogue days are equally weighted; simulated prices are unbiased against the curve; volume matches its target; the decomposition adds up; the premium makes the hedged P10 exactly zero; the hedge cuts risk; each risk alone stays within the hedged risk; the clause pays more per MWh on less volume; the hedge shrinks with the capture rate; both capture errors raise the premium and not the expected price; the terms are steps that leave the model deal alone; credit loss behaves; degradation takes the same share each year |
| `test_options.py` | Bachelier put–call parity and agreement with simulation; Kirk reduces to Black at zero strike and matches Monte Carlo; value falls with correlation; the swing value lies between intrinsic and perfect foresight; impossible correlations are rejected |
| `test_fundamental.py` | Clearing meets demand; supply rises with price; more demand never lowers the price; fuel and carbon pass through within the fleet's heat rates; gas is costed per net MWh; curtailment only below zero and never beyond the forecast; the surplus backstop keeps a surplus off the floor; committed output is flat within the day and widens the daily price range; storage respects power and losses and idles when the spread does not cover them; the trade fit recovers a known response; EEX reports parse whatever their layout; price-setting shares add to one; more solar alone lowers its capture rate |
| `test_fairvalue.py` | The scenario uses nothing published after its date; it replays the twelve months before; closed nuclear is removed; a contract's next move never crosses its expiry |

---

## 20. Limits

What the model does not do, grouped by where it bites. The README has the same list in brief; `TODO.md` has the planned work.

**Market data**

- **Venue.** German power trades on EEX. The curve, the vols and the hedge are marked on OMIP's free reference prices, which track EEX but are not trade prints. A desk would use EEX settlements.
- **No implied vols.** Forward vol is realised.
- **No peak quotes.** The peak/base ratio comes from the historical shape.
- **Flat beyond Cal-33.** Three of the ten delivery years are extrapolated.
- **Seasonality beyond the quoted quarters** comes from spot history. In 2030 the curve's Q2 is 0.86 of the calendar price, against 0.80 quoted for Q2-28 over Cal-28. If the market's deeper summer discount persisted, solar in 2029–36 would be worth less than shown.
- **Gas** for the spark spread is a press quote; gas vol and correlations come from monthly averages.
- **Quarter-hours.** The day-ahead auction has cleared in 15-minute periods since October 2025, and they are averaged to hours. `TODO.md` puts the effect at 0.010 of fleet solar capture, about €1.1/MWh, over October 2025 – October 2026.

**Valuation**

- **One level factor.** All calendar years move together; the measured correlation between the next and the third year is 0.77.
- **Hedge liquidity.** Every calendar year is sold today, at a wider spread beyond three years. In practice a desk would stack and roll, and a one-factor curve cannot price that risk.
- **Hedge ratio.** The simulation moves the whole positive shape with the level. The fundamental model says a solar capture price takes 36% of baseload's move when gas drives it and 66% when carbon does. In a gas-driven rally the simulated hedge is too large.
- **Cannibalisation risk.** The slope's standard error and the year-ahead error are simulated. The capacity path, load growth, the log form and the 36-month window are taken as given, and the window alone moves the solar price by more than the premium. 2036 is 0.76 log units of penetration beyond the window's mean, against ±0.3 inside it.
- **The year-ahead error is simulated with mean zero.** Solar's nine errors average −0.044: the model has more often overstated the capture rate than understated it. That bias is treated as risk (through the RMS), not as expected cannibalisation.
- **Cannibalisation is outside the curve.** It is applied to the plant's revenue, so the curve for 2036 keeps today's shape, and the battery and the negative hours in the paths do not see the build-out.
- **Negative hours.** The clause's paid share follows the build-out, but the paths keep today's negative hours. The timing of new ones and the error in the unpaid share's slope are not simulated. Wind's regressions use wind penetration alone, although solar drives many of its negative hours.
- **Commercial terms** are indicative inputs. Variation margin is not costed: a short hedge in a rally needs cash long before the PPA pays it back. Credit exposure follows the price level alone and ignores collateral, guarantees and step-in rights. Change of law, grid curtailment and availability guarantees are not modelled.
- **Balancing** rests on an assumed imbalance spread.
- **Monte Carlo noise.** 800 paths, and prices are shown to the cent. Across four seeds the 10-year solar price ran from €38.30 to €38.50 and the 10-year wind price from €75.42 to €76.39; the one-year prices moved by about €1 (solar) and €1.40 (wind).

**Fundamental model and fair value**

- **Fuel prices** are monthly IMF averages, and coal is Newcastle, not API2.
- **Fuel held flat.** No free forward curves for gas and coal, so the gap to the futures partly measures the fuel curve's slope.
- **One node.** Germany with a fitted trade function, not a European dispatch. Start-up costs and ramping show up only as must-run, bid noise and the committed share.
- **Negative hours** are overstated in most years and unstable from fit to fit.
- **No capacity growth in the fair value,** until the model can grow flexibility with it.
- **Small look-aheads in the fair value history.** Thermal capacity is interpolated towards year-end values not yet published at mid-year dates. Historical month ends use their own month's average fuel price, which the IMF publishes only afterwards; the latest date has September's.

---

## 21. Recipes

**Change the plant.** `capacity_mw`, `[technology.solar]` or `[technology.wind]` in the TOML, then build. Prices per MWh do not depend on `capacity_mw`; MW and €m do.

**Add a site.** Add a line to `[sites]` with a name and coordinates. The build fetches its weather (a few minutes), values both technologies there, and the site appears in the controls.

**Change the premium's confidence level.** `[risk].volume_quantile` (0.10 is P90). The ladder at P75/P90/P95 is fixed in `valuation.value`.

**Change the build-out.** `[capacity].solar` and `.wind` need a GW value for every delivery year. `load_growth` scales penetration down.

**Change the shape window.** `[shape].history_months` moves the shape, the residual pool, today's penetration, balancing and the SMARD checks. The capture regression's look-back is `capture.LOOKBACK`; change both to keep them aligned.

**More paths or another seed.** `[risk].paths`, `[risk].seed`. Memory and time scale with paths: each simulated year holds several 800 × 8,760 arrays.

**Change the fundamental model's inputs.** Any change under `[fundamental]` changes the cache key, and the next build re-runs the backtest (about 10 minutes) and the fair values.

**Change the fundamental model's code.** Bump `fundamental.VERSION` (and `fairvalue.VERSION` if the scenario method changed). Without that, the build silently reuses the old backtest.

**Add peak quotes.** Fill `[futures].peak` with `{ contract, price }` entries. The solver adds a second daily correction for peak hours and reprices them exactly.

**Add a number to the site.** Compute it in a model module, add it to the dictionary `build.run` writes, then read it in `app.js`. Add a test for the invariant it should satisfy.

**Value a single deal in Python.**

```python
from ppa import build
out = build.run()                                # writes site/data.json and MEMO.md
deal = out["scenarios"]["solar"]["south"][9]     # 10-year
deal["price"], deal["steps"], deal["hedge"]
```

---

## 22. Glossary

| Term | Meaning |
|---|---|
| Pay-as-produced PPA | The buyer pays a fixed price for every MWh the plant produces, whenever it produces it |
| Baseload | A constant MW in every hour of a period; a base future pays the average hourly price |
| Peakload | Monday–Friday, 08:00–20:00 |
| Cal, Q, month contracts | Futures on a calendar year, quarter or month of baseload |
| HPFC | Hourly price forward curve: an expected price per future hour consistent with the futures |
| Capture price, capture rate | The generation-weighted average price a technology earns, and that over baseload |
| Cannibalisation | The fall in a technology's capture rate as more of it is built |
| Penetration | Fleet generation over load |
| Profile | The discount (or premium) from when a plant produces on average |
| Full-load hours | Annual output over capacity; 8,760 × the mean capacity factor |
| P50, P90 | Yield exceeded with 50% and 90% probability |
| Samuelson effect | Futures become more volatile as delivery approaches |
| Minimum-variance hedge | The futures position that minimises the variance of the hedged P&L: a regression coefficient |
| Initial and variation margin | Collateral a clearing house holds against a futures position, and the daily cash settlement of its gains and losses |
| Guarantee of origin | A certificate that a MWh was renewable, traded separately from the power |
| reBAP | The German uniform imbalance price |
| Merit order | Plants ranked by marginal cost; the last one needed sets the price |
| Must-run | Output that runs at any price: heat-led CHP, inflexible units |
| Clean spark spread | A gas plant's margin: power less gas and carbon per MWh of power |
| EUA | EU emission allowance, one tonne of CO₂ |
| Gross and net calorific value | Gas is traded on gross (with the heat of condensation); plant efficiencies are stated on net, 90% of gross |
| Residual load | Load less must-take and renewable generation: what dispatchable plants or imports must cover |
| Kirk's approximation | A closed form for a spread option that treats the second leg plus strike as lognormal |
| Least-squares Monte Carlo | Longstaff–Schwartz: an exercise rule from regressing continuation values on the state |
| Bachelier model | An option model with normally distributed prices, which allows negative ones |
| Solarspitzengesetz | The 2025 German law removing support for new solar plants in negative-price hours |
