# PPA Desk

Valuation and risk for German solar and wind power purchase agreements, built only from public data.

It takes the exchange forward curve, builds an arbitrage-free hourly curve, simulates prices jointly with site weather, and prices a pay-as-produced PPA. The fair price is split into the parts a desk negotiates over: baseload, profile, price–volume correlation, cannibalisation, risk premium and balancing. The model price is then turned into a bid by the terms a desk adds: hedge cost, credit and guarantees of origin. The output also includes the same deal with a negative-price clause, the exchange hedge, the residual risk and the embedded options.

**[MEMO.md](MEMO.md)** has the answer on one page: a 10-year bid, what it is made of, and the hedge.

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m ppa build    # fetch public data (cached in data/), run every model, write site/data.json and MEMO.md
.venv/bin/python -m ppa serve    # http://localhost:8000
.venv/bin/pytest
```

The first build downloads about 11 years of hourly market data (for Germany and its ten neighbours), 21 years of ERA5 weather for four sites and ten years of daily OMIP futures settlements. Energy-Charts rate-limits and OMIP is fetched at one page a second, so this takes a little over an hour. Later builds take about 20 seconds.

## Method

**Hourly forward curve** — [`hpfc.py`](ppa/hpfc.py)
OMIP Phelix DE base futures for 30 Oct 2026 (Dec-26 through Cal-33) are turned into an hourly curve. A seasonal prior (month × day type × hour, learned from the last 36 months of day-ahead prices, blended linearly between mid-month points so it has no steps at month or year boundaries) gets a daily correction that minimises squared second differences. This is a discrete smoothing spline in the spirit of Benth, Koekebakker and Ollmar. The correction is constrained so every quoted future reprices exactly. Quotes that finer contracts already imply (Q1-27, Cal-27) are detected by rank, then checked rather than imposed. Beyond Cal-33 the curve is flat in nominal terms. The monthly level of the prior is each month against the twelve months centred on it, so a trend inside the window (the 2026 rally) does not read as a seasonal peak.

The shape is sign-split. Positive prices scale with the forward level, while negative prices stay in €/MWh. Negative prices are set by renewable support and must-run, not by gas, so a purely multiplicative shape made Cal-27 (€164) show −€131 hours. The split brings those back to the historical depth.

**Generation** — [`generation.py`](ppa/generation.py)
ERA5 weather from 2005 to 2026 comes from Open-Meteo for four sites (North, East, South, West).
- **Solar:** Hay–Davies transposition, Faiman cell temperature and DC/AC clipping, with ERA5's surface-irradiance bias corrected.
- **Wind:** hourly shear to hub height, air-density correction and a park-smoothed power curve.

P50 and P90 combine inter-annual weather variability with a model-error term.

**Joint simulation** — [`simulate.py`](ppa/simulate.py)
`P = L·(F + D + M·Δup) − (D + Δdown)`. Here `F` is the hourly curve, `M` its monthly level and `D` the expected negative part for the hour. `Δup` and `Δdown` are price residuals from a recent historical day, and `L` is a lognormal shock to the calendar-year price level that leaves negative prices alone. Site generation is taken from the same historical day, so the weather–price dependence (sunny noon, cheap noon) is resampled rather than assumed. Days are drawn in weekly blocks, with weekdays aligned and each pool year equally likely. Each year's volume is rescaled to a full-load-hours draw from the long ERA5 record, and a solar plant loses 0.4% of its output per year of age. The simulation is unbiased: `E[P] = F`.

The level shock has one Brownian path for all calendar years. At each moment a year moves with the vol of a contract that far from delivery: 12% while it is more than three years out, rising to 27% in the last year. A far year therefore moves less in total than a flat vol would give it.

**Valuation** — [`valuation.py`](ppa/valuation.py)
The headline deal pays a fixed price for every MWh produced. The fair price is the expected price, lowered by a risk premium. That premium is solved so the hedged deal breaks even at its P90 outcome (the 10th percentile of lifetime P&L).

- **Cannibalisation** is a change in the capture rate, a share of the price, so in €/MWh it moves with the price level. Two errors sit on it, and no exchange hedge reaches either.
  - **The slope:** each path draws one error from the slope's standard error and keeps it for every year. It grows with the build-out and does not diversify over the tenor, so it is most of the premium on a long solar deal.
  - **The year:** each year also lands away from the model for reasons penetration does not explain. Its size is how far the model has missed a calendar year ahead since 2018, and a year keeps part of the year before. It diversifies over a long tenor and is most of the premium on a short one: the one-year solar premium is €16.85.
- **What the hedge leaves** is reported for each source alone: the slope, the year, the hourly shape and volume. Expected generation carries more of the price level than the plant does, so the shape part gets its own hedge.
- **The premium's confidence level** is a choice, so the price is also given at P75 and P95: €41.39 and €36.32 for the 10-year solar deal, against €38.30 at P90.
- **Terms that turn the model price into a bid** come from `inputs/market.toml` and are indicative.
  - **Hedge cost:** half the bid-offer spread on every MWh of baseload sold, tight for the three liquid calendar years and wide beyond, plus funding the initial margin. The margin is the 99% two-day move at the vol of a contract that far from delivery.
  - **Credit:** the expected loss if the generator defaults. The hedge sits on the exchange and offsets none of it, so the exposure is the PPA alone: what the years left are worth to the desk, by regression on the price level. In a backwardated curve a fixed price is worth most in the early years, so most of the exposure is there.
  - **Guarantees of origin** come with the power and add their value.
- **The hedge** is the minimum-variance amount of calendar baseload per year. It is smaller than the P50-equivalent MW, because a solar plant's revenue moves less than one-for-one with the baseload level, and it steps down as the capture rate falls.
- **The negative-price clause** is a second structure: only output in hours at or above zero is paid. Its price is per paid MWh, so it is higher on less volume and is not a step of the first price. The paid volume shrinks as negative hours grow with the build-out (78% of a solar plant's output in 2027, 74% in 2036), and the price takes the cannibalisation of the paid hours alone.
- **The baseload-shaped alternative** shows the realised range the generator would keep after selling its P50-equivalent MW as baseload.

**Cannibalisation** — [`capture.py`](ppa/capture.py)
Monthly fleet capture rates are regressed on log penetration with month fixed effects over the same 36-month window as the shape. The model is backtested one month ahead from 2021 against a seasonal naive forecast:
- **Solar:** mean absolute error 0.090 vs 0.113 for the naive forecast.
- **Wind:** no better than naive, which fits wind's weak penetration effect.

The slope is −0.22 ± 0.09 for solar and −0.04 ± 0.08 for wind, in capture rate per log share of load. The valuation simulates that standard error. Penetration is capacity over load: capacity follows the build-out path and load grows 1% a year.

A calendar year ahead, given the year's actual penetration, the model fitted on the 36 months before has missed the capture rate by 0.078 for solar and 0.033 for wind (root mean square, 2018–2026). The largest miss is 2024, at −0.17. A year's error carries 0.13 of the year before for solar and 0.40 for wind. This is the year-to-year error the valuation simulates.

The window moves the slope by more than its standard error. Refitted on other windows, with the shape, the build-out and everything else unchanged:

| Months | Solar slope | 10-year solar, €/MWh | Wind slope | 10-year wind, €/MWh |
|---|---:|---:|---:|---:|
| 24 | +0.01 ± 0.15 | 44.76 | −0.09 ± 0.10 | 72.95 |
| 36 | −0.22 ± 0.09 | 38.30 | −0.04 ± 0.08 | 75.42 |
| 48 | −0.33 ± 0.07 | 33.92 | −0.05 ± 0.06 | 75.65 |
| 60 | −0.47 ± 0.07 | 28.19 | −0.05 ± 0.05 | 75.65 |

The fundamental model gives the other end. With 10% more solar and nothing else built, its capture rate falls by 0.98 per log share, and wind's by 0.30. That is the slope with no new storage, flexible demand or export capacity. History's slope is flatter by what was built alongside: batteries went from 8 to 22 GW inside the 36 months. The 10-year price therefore assumes flexibility keeps pace with solar as it has since 2023.

Two more regressions of the same form split the slope for the negative-price clause. For solar, the capture rate over hours at or above zero falls by 0.14 ± 0.05 per log share, and the share of output at negative prices rises by 0.09 ± 0.09.

Two choices came from the data, not from priors. First, the full 2015–26 slope mispredicts 2026: summer solar share rose 36% over 2024 while capture held flat, after the 2025 Solarspitzengesetz and about 20 GW of batteries. A recent window captures that regime. Second, a linear slope forecasts one month ahead slightly better but extrapolates to a 0.16 capture rate by 2036. The log form builds in diminishing marginal cannibalisation instead.

**Options** — [`options.py`](ppa/options.py)
- **Negative-price clause:** the strip of hourly puts struck at zero is the smaller part of the clause; most of the higher price comes from the volume that is no longer paid. A normal model per hour is within 15% of the simulated strip for solar but up to twice it for wind, because negative prices are bounded and do not scale with the level.
- **Clean spark spread:** Kirk's approximation on a moment-matched gas-plus-carbon basket, against a three-asset Monte Carlo, swept over correlation. The plant is a CCGT at 55% net efficiency, which is 49.5% of the gas as it is quoted. Vols and correlations are measured over the last 24 months: power 44% (monthly contracts within two months of delivery), carbon 28% (EEX auctions) and gas 64%. Gas has no free daily price, so its vol comes from monthly averages, scaled up by √(3/2) because averaging removes a third of the variance of a change. Correlations are those of monthly average changes: power–gas 0.87, power–carbon 0.35, gas–carbon 0.06.
- **2-hour battery:** valued as a swing option with 250 cycles a year by least-squares Monte Carlo, bounded by the intrinsic and perfect-foresight values. Each day it charges before it discharges.

**Forward curve history** — [`forwards.py`](ppa/forwards.py)
Daily OMIP settlements for every German base month, quarter and calendar contract since June 2016. Returns are taken within each contract, never across a roll. From them:
- **Forward vol:** the simulation's level vol is the realised vol of calendar contracts by time to delivery over the last 24 months, not an assumption.
- **Samuelson effect:** volatility by time to delivery, rising as delivery approaches.
- **One-factor check:** the correlation between next-year and third-year daily returns, which tests the simulation's single level factor.
- **Hedge backtest:** a one-year PPA on the German fleet profile, bought each October from 2017 at the Cal forward times the trailing capture rate, held unhedged, with a volume hedge, and with the dashboard's value hedge.

**Fundamental model** — [`fundamental.py`](ppa/fundamental.py)
An hourly merit order of the German day-ahead market, built only from free data. Every hour, demand served by the public grid clears against:
- **Must-take supply:** nuclear, hydro, biomass and waste as observed.
- **Wind and solar:** the day-ahead forecast, scaled to the output it turned into in training hours with no reason to curtail. Below zero, a block of each switches off at once, because subsidised plants lose their premium at negative prices and are bid at zero. More curtails gradually as the price falls, and the rest (rooftop solar, old fixed-tariff plants) runs at any price.
- **Thermal plants:** lignite, hard coal, gas and oil. A must-run part (heat-led CHP, inflexible units) runs at any price. The flexible rest bids fuel plus carbon over efficiency, spread across the real fleet's efficiencies and blurred by a fitted bid noise. Gas trades per MWh of gross calorific value while plant efficiencies and emission factors are net, so a plant's gas costs the quote over 0.9. A fitted share of that flexible output is committed for the day: it follows the day's mean dispatch rather than the hour's price, so plants needed for the evening stay on through a cheap midday and cannot all ramp up for the peak. Committed plants back off only below a fitted shut-down price.
- **Trade:** net imports, linear in German demand, the wind forecast and the solar forecast, and in the residual load of all ten neighbours. A bounded response to the German price around a gas plant's cost sits on top.
- **Storage:** pumped hydro charges in each day's cheapest hours and discharges in its dearest.
- **Backstops:** demand backs off at scarcity prices, and power-to-heat, extra pumping and plants running below cost take on load at very negative ones.

Availability and must-run are read from observed dispatch, and the trade function is fitted on observed trade and prices. Thirteen behavioural parameters (a markup per fuel, bid noise, wind and solar curtailment, and the committed share and its shut-down price) are then fitted by clearing the market against prices, generation by fuel, wind and solar output, and trade together. Each test year is predicted by a model fitted only on the three years before it, with that year's actual fuel prices, carbon, demand, weather and capacity.

The bar to clear is the hourly error of knowing each month's actual average price in advance. Out of sample, the model beats it in every year from 2019 to 2026: for example €19 against €32 in 2023, €19 against €35 in 2025, and €56 against €80 in the 2022 crisis. Solar capture comes out at 0.76 against 0.76 actual in 2023, 0.61 against 0.59 in 2024 and 0.52 against 0.52 in 2025. Five findings shaped it:
- **Trade has to see the neighbours.** Germany's net position swung by about 5 GW between 2022 and 2024 as French nuclear recovered. Trade fitted on German conditions alone priced 2024 €36 too high; adding the ten neighbours' residual load brought it to within €10.
- **Exports saturate at sunny middays,** because the neighbours have solar too. A price response that let exports keep growing put 2025 midday prices at €92 against €46.
- **The solar forecast runs high.** It is 3–6% above actual output even in hours with no reason to curtail, while demand is built from actual output. Unscaled, that gap was 1–2 GW of phantom surplus at every sunny midday.
- **Wind curtails, solar barely does.** Against its forecast, wind output falls 5% at €0, 15% at −€20 and 44% below −€50, while solar falls a few percent. One shared curtailment curve left the model with too many shallow negative hours and, in 2024–25, surpluses that hit the −€500 floor.
- **Thermal plants cannot follow the hour.** At actual prices, coal, lignite and gas ran about 2 GW more at midday and 3–6 GW less in the evening than an hour-by-hour merit order offers. Without commitment the day came out too flat (2025: €66 against €46 at 13:00, €116 against €136 at 19:00) and solar capture at 0.61 against 0.52. Committing about half of the flexible output to the day brings it to €58 and €128, cuts hourly error by €2–3 from 2023, and the fitted shut-down price lands within €6 of zero from 2021 on.

Negative hours are close in 2025 (503 against 576) but still 1.4–1.9× the market's count in 2023, 2024 and 2026, and no hour hits the floor. Their count is the least stable output: restating gas on a net basis moved hourly error by €1 at most in any year, but 2024 from 693 to 861 negative hours and 2019 from 307 to 592. The evening peak is still about €8 short in 2025 and €17 short in 2026.

**Fair value against the market** — [`fairvalue.py`](ppa/fairvalue.py)
At every month end since January 2019, the fundamental model prices the next calendar year with what was known that day. It replays the twelve months before, with their weather, demand, wind, solar, trade and storage as they were. That replay runs on that day's fuel and carbon prices, the thermal fleet installed then, nuclear on its legal closure path and the parameters fitted on the three years before. The gap to the Cal futures settlement is then traded for one month: long when fair value is above the market, short when below.

- **On 30 October 2026**, Cal-27 settled at €164.27 against a fair value of €133.08. The futures rose €36 in October. The model's gas is September's average, €75.61; at the mid-October quote of €88.41 the fair value would be €140.73, so stale fuel explains a quarter of the gap.
- **What sets the price.** At that day's fuel and carbon, lignite bids €97–128/MWh, hard coal €127–156 and gas €177–294. Of the supply that responds at the clearing price, lignite is 31%, coal 28% and gas 25% on average over the year; curtailed wind and solar, trade and demand response are the rest. Gas is the marginal fuel in a quarter of the hours, which is why its delta is 0.60 and not a gas plant's 2.0.
- **Fuel and carbon.** The same year is cleared again with one input bumped. €1/MWh on gas moves the Cal-27 fair value by €0.60, €1/t on carbon by €0.66 and €1/t on coal by €0.09. A solar capture price moves by €0.21, €0.44 and €0.06: about a third of baseload's gas exposure but two thirds of its carbon exposure, because in the model coal and lignite set the midday price more often than gas does.
- **As a signal, the gap is weak.** Over 86 months it called the next move 64% of the time for +€4.32/MWh a month, with a t-statistic of 1.1. Outside the July 2021–June 2023 crisis it is right 59% of the time, t 1.9. Last month's move does better (t 2.2), mostly from the 2021–22 trend.
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
- **Venue:** German power trades on EEX. OMIP lists the same contracts and publishes its reference prices freely, so the curve, the vols and the hedge are all marked there. A desk would use EEX settlements, with peak contracts.
- **Spark-spread gas:** an indicative press quote; replace it with an ICE TTF settlement for the valuation date. Carbon is EEX's auction price. Gas vol and the gas correlations rest on about 22 monthly changes.
- **No peak quotes:** the peak/base ratio comes from the historical shape. The solver honours peak quotes when they are given.
- **Balancing:** fleet day-ahead forecast error × single-site factor × an assumed imbalance spread, because reBAP needs registration.
- **15-minute day-ahead products (since Oct 2025):** averaged to hourly.
- **Price levels:** one factor moves all calendar years together.
- **Hedge liquidity:** the hedge sells every calendar year at today's curve at no cost. Beyond about three years a desk would stack and roll, and a one-factor curve cannot price the risk of that.
- **Hedge ratio:** the simulation moves the whole positive shape with the price level, so the 2027 solar hedge is 57% of the plant's P50 MW. The fundamental model has a solar capture price taking 36% of baseload's move when gas drives it and 66% when carbon does. The right hedge depends on what moves the level, and in a gas-driven rally this one is over-hedged.
- **Cannibalisation risk:** the slope's standard error and the year-ahead error are simulated. The capacity path, load growth, the log form and the 36-month window are taken as given, and the window alone moves the solar price by more than the risk premium. 2036 is 0.76 log units of penetration beyond the window's mean, against ±0.3 inside it.
- **Cannibalisation outside the curve:** the capture-rate change is applied to the plant's revenue, not to the hourly curve. The curve for 2036 keeps today's shape, so the battery value and the negative hours in the paths do not see the build-out.
- **Commercial terms:** bid-offer, margin, default rate, recovery and the value of guarantees of origin are indicative inputs, not quotes. Variation margin is not costed: a short hedge in a rally needs cash long before the PPA pays it back, as in 2022. Credit exposure follows the price level alone and ignores collateral, guarantees and step-in rights. Change of law, curtailment by the grid operator and availability guarantees are not modelled.
- **Fundamental model fuel prices:** gas, coal and oil are IMF monthly averages (free daily TTF and API2 are licensed), and coal is Newcastle rather than the Rotterdam API2 that German plants pay. Within-month swings, as in August 2022, are missed.
- **Fair value inputs:** fuel enters at the month's spot price, held flat, because free gas and coal forward curves do not exist. When the curve is in steep backwardation or contango, as in 2021–23, the gap mostly measures that rather than mispricing. Capacity growth is left out until the model can grow flexibility with it.
- **Fundamental model scope:** one German node with a fitted trade function, not a European dispatch. Intertemporal constraints (start-up costs, ramping) show up only as must-run and bid noise.
- **Negative hours:** the paid share and the paid-hours capture rate follow their own regressions on penetration, but the simulated paths keep today's negative hours. The timing of the new ones is not simulated, and neither is the error in the unpaid share's slope, which is as large as the slope. Wind's regressions use wind penetration alone, although solar drives many of its negative hours.

## Layout

```
inputs/market.toml   market snapshot and assumptions
ppa/                 sources → dataset → hpfc, generation, capture → simulate → valuation, options → build
tests/               invariants: exact repricing, unbiased simulation, decomposition sums, option bounds
site/                static front end: index.html, style.css, app.js, charts.js, data.json (generated)
MEMO.md              generated one-page answer
```
