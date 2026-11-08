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

The first build downloads about 11 years of hourly market data (for Germany and its ten neighbours), 21 years of ERA5 weather for four sites and ten years of daily OMIP futures settlements. Energy-Charts rate-limits and OMIP is fetched at one page a second, so this takes a little over an hour. Later builds take about 20 seconds.

## Method

**Hourly forward curve** — [`hpfc.py`](ppa/hpfc.py)
OMIP Phelix DE base futures for 30 Oct 2026 (Dec-26 through Cal-33) are turned into an hourly curve. A seasonal prior (month × day type × hour, learned from the last 36 months of day-ahead prices, blended linearly between mid-month points so it has no steps at month or year boundaries) gets a daily correction that minimises squared second differences. This is a discrete smoothing spline in the spirit of Benth, Koekebakker and Ollmar. The correction is constrained so every quoted future reprices exactly. Quotes that finer contracts already imply (Q1-27, Cal-27) are detected by rank, then checked rather than imposed. Beyond Cal-33 the curve is flat in nominal terms.

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

**Fundamental model** — [`fundamental.py`](ppa/fundamental.py)
An hourly merit order of the German day-ahead market, built only from free data. Every hour, demand served by the public grid clears against:
- **Must-take supply:** nuclear, hydro, biomass and waste as observed.
- **Wind and solar:** the day-ahead forecast, scaled to the output it turned into in training hours with no reason to curtail. Below zero, a block of each switches off at once, because subsidised plants lose their premium at negative prices and are bid at zero. More curtails gradually as the price falls, and the rest (rooftop solar, old fixed-tariff plants) runs at any price.
- **Thermal plants:** lignite, hard coal, gas and oil. A must-run part (heat-led CHP, inflexible units) runs at any price. The flexible rest bids fuel plus carbon over efficiency, spread across the real fleet's efficiencies and blurred by a fitted bid noise. A fitted share of that flexible output is committed for the day: it follows the day's mean dispatch rather than the hour's price, so plants needed for the evening stay on through a cheap midday and cannot all ramp up for the peak. Committed plants back off only below a fitted shut-down price.
- **Trade:** net imports, linear in German demand, the wind forecast and the solar forecast, and in the residual load of all ten neighbours. A bounded response to the German price around a gas plant's cost sits on top.
- **Storage:** pumped hydro charges in each day's cheapest hours and discharges in its dearest.
- **Backstops:** demand backs off at scarcity prices, and power-to-heat, extra pumping and plants running below cost take on load at very negative ones.

Availability and must-run are read from observed dispatch, and the trade function is fitted on observed trade and prices. Thirteen behavioural parameters (a markup per fuel, bid noise, wind and solar curtailment, and the committed share and its shut-down price) are then fitted by clearing the market against prices, generation by fuel, wind and solar output, and trade together. Each test year is predicted by a model fitted only on the three years before it, with that year's actual fuel prices, carbon, demand, weather and capacity.

The bar to clear is the hourly error of knowing each month's actual average price in advance. Out of sample, the model beats it in every year from 2019 to 2026: for example €18 against €32 in 2023, €19 against €35 in 2025, and €56 against €80 in the 2022 crisis. Solar capture comes out at 0.76 against 0.76 actual in 2023, 0.61 against 0.59 in 2024 and 0.51 against 0.52 in 2025. Five findings shaped it:
- **Trade has to see the neighbours.** Germany's net position swung by about 5 GW between 2022 and 2024 as French nuclear recovered. Trade fitted on German conditions alone priced 2024 €36 too high; adding the ten neighbours' residual load brought it to within €10.
- **Exports saturate at sunny middays,** because the neighbours have solar too. A price response that let exports keep growing put 2025 midday prices at €92 against €46.
- **The solar forecast runs high.** It is 3–6% above actual output even in hours with no reason to curtail, while demand is built from actual output. Unscaled, that gap was 1–2 GW of phantom surplus at every sunny midday.
- **Wind curtails, solar barely does.** Against its forecast, wind output falls 5% at €0, 15% at −€20 and 44% below −€50, while solar falls a few percent. One shared curtailment curve left the model with too many shallow negative hours and, in 2024–25, surpluses that hit the −€500 floor.
- **Thermal plants cannot follow the hour.** At actual prices, coal, lignite and gas ran about 2 GW more at midday and 3–6 GW less in the evening than an hour-by-hour merit order offers. Without commitment the day came out too flat (2025: €66 against €46 at 13:00, €116 against €136 at 19:00) and solar capture at 0.61 against 0.52. Committing about half of the flexible output to the day brings it to €57 and €127, cuts hourly error by €2–3 from 2023, and the fitted shut-down price lands at about zero from 2021 on.

Negative hours are close in 2025 (553 against 576) but still 1.2–1.8× the market's count in 2023, 2024 and 2026, and no hour hits the floor. The evening peak is still about €10 short in 2025 and €20 short in 2026.

**Fair value against the market** — [`fairvalue.py`](ppa/fairvalue.py)
At every month end since January 2019, the fundamental model prices the next calendar year with what was known that day. It replays the twelve months before, with their weather, demand, wind, solar, trade and storage as they were. That replay runs on that day's fuel and carbon prices, the thermal fleet installed then, nuclear on its legal closure path and the parameters fitted on the three years before. The gap to the Cal futures settlement is then traded for one month: long when fair value is above the market, short when below.

- **On 30 October 2026**, Cal-27 settled at €164.27 against a fair value of €130.32. The futures rose €36 in October, and the September fuel prices the model uses have not caught up.
- **As a signal, the gap is weak.** Over 86 months it called the next move 61% of the time for +€1.79/MWh a month, with a t-statistic of 0.5. Outside the July 2021–June 2023 crisis it is right 56% of the time, t 0.9. Last month's move does better (t 2.2), mostly from the 2021–22 trend.
- **Against the realised year**, fair value was closer than the futures in 2020, 2023 and 2024, and further in 2021 and 2025. Both missed the crisis by more than €100.
- **Older weather is not rescaled to today's wind and solar.** The flexibility that grew alongside them is embedded in observed demand: batteries went from 8 to 22 GW over 2023–26. Scaled up, the model saw 1,000–1,700 negative hours a year against about 500 in the market, and priced Cal-27 at €90. The fair value is therefore what the next year would cost if the system and fuel stayed as they are.

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
- **Spark-spread gas:** an indicative press quote; replace it with an ICE TTF settlement for the valuation date. Carbon is EEX's auction price.
- **No peak quotes:** the peak/base ratio comes from the historical shape. The solver honours peak quotes when they are given.
- **Balancing:** fleet day-ahead forecast error × single-site factor × an assumed imbalance spread, because reBAP needs registration.
- **15-minute day-ahead products (since Oct 2025):** averaged to hourly.
- **Price levels:** one factor moves all calendar years together.
- **Fundamental model fuel prices:** gas, coal and oil are IMF monthly averages (free daily TTF and API2 are licensed), and coal is Newcastle rather than the Rotterdam API2 that German plants pay. Within-month swings, as in August 2022, are missed.
- **Fair value inputs:** fuel enters at the month's spot price, held flat, because free gas and coal forward curves do not exist. When the curve is in steep backwardation or contango, as in 2021–23, the gap mostly measures that rather than mispricing. Capacity growth is left out until the model can grow flexibility with it.
- **Fundamental model scope:** one German node with a fitted trade function, not a European dispatch. Intertemporal constraints (start-up costs, ramping) show up only as must-run and bid noise.
- **Clause and cannibalisation overlap:** the projected capture-rate drop applies to positive-price volume, while simulated negative hours stay at today's frequency. Part of the future drop comes from more negative hours, which the clause excludes, so some overlap is likely.

## Layout

```
inputs/market.toml   market snapshot and assumptions
ppa/                 sources → dataset → hpfc, generation, capture → simulate → valuation, options → build
tests/               invariants: exact repricing, unbiased simulation, decomposition sums, option bounds
site/                static front end: index.html, style.css, app.js, charts.js, data.json (generated)
MEMO.md              generated one-page answer
```
