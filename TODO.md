# To do

## Market structure breaks

- **Quarter-hour curve and solar valuation.** The day-ahead auction has cleared in 15-minute periods since
  2025-10-01, and a PPA signed now settles on them. Hourly averages overstate fleet solar capture by 0.010
  (€1.1/MWh) over Oct 2025–Oct 2026; wind is unaffected. Learn a within-hour quarter shape from the
  quarter-hour history, add it to the hourly curve, and value solar volumes and the negative-price clause per
  quarter-hour.
- **Recent-data weighting in the fundamental fit.** Negative prices changed regime in mid-2023: per half-year,
  31–212 negative hours and 28–94 hours at zero before, 176–389 and 111–183 since. The 2023 and 2024 backtest
  years are fitted mostly on the old regime and show the largest negative-hour excess (562 against 301,
  861 against 457). Try weighting recent years, a shorter window, or a zero-bid share that trends over
  time, and check whether those counts come into line without hurting hourly error.
- **Trade drift.** One trade fit over 2019–2026 is off by −1.4 GW in 2019 and +1.4 GW in 2023, around flow-based
  coupling (June 2022), the gas crisis and the nuclear exit (April 2023). Test a time-varying intercept against
  the rolling three-year window.
- **Balancing cost from data.** The PPA's balancing cost uses an assumed €20/MWh spread between reBAP and
  day-ahead. Replace it with the realised spread from reBAP history, from the latest rule change on, at
  quarter-hour resolution.
- **Pre-2018 prices.** Fits for 2019–2021 train partly on the joint DE-AT-LU zone before the October 2018
  split. Minor; note it, or start those windows at the split.

## Valuation

- **Cannibalisation from the fundamental model.** The slope runs from +0.01 on 24 months to −0.47 on 60, and
  the 10-year solar price from €45 to €28 with it; the model with nothing else built says −0.98. Replace the
  regression with the model's capture rate along the capacity path, with storage and flexible demand on
  paths of their own (see the 2027–2036 projection below).
- **Cannibalisation in the curve.** The capture-rate change is applied to the plant's revenue. Tilt the hourly
  curve by it instead, so the Curve view, the battery and the negative hours in the paths see the build-out.
- **Hedge liquidity.** Every calendar year is sold at today's curve at no cost. Add a second curve factor and
  price a stack-and-roll hedge beyond the liquid three years.
- **Negative hours inside the paths.** The clause's paid share now follows the build-out, but the paths keep
  today's negative hours. Resample more of them as capacity grows, so the clause's risk sees their timing, and
  simulate the error in the unpaid share's slope.
- **Wind's negative hours.** They follow wind penetration alone; add solar penetration as a second driver.
- **Variation margin.** Initial margin is funded; the cash a short hedge needs in a rally is not. Simulate the
  margin account along the level paths and cost its peak.
- **Credit terms.** Exposure follows the price level alone. Add collateral thresholds and a parent guarantee,
  and let the two capture errors move the exposure.
- **Daily gas.** Gas vol and its correlations come from monthly averages. Swap in daily TTF settlements if a
  feed becomes available.
- **A second market.** Spain trades on OMIP itself and its solar capture fell earlier; the same pipeline with
  REE data would test what is German in the method.

## Fundamental model

- **Fit stability.** The same setup gave 569 and 645 negative hours for 2023 in two runs, and restating gas on a
  net basis moved 2019 from 307 to 592 and 2024 from 693 to 861 while hourly error moved by €1 at most. Refit
  from several starting points, report the spread of parameters and backtest metrics, and keep the best.
- **Evening peak.** Still about €8 short in 2025 and €17 short in 2026 at 19:00.
- **Negative hours.** Still 1.4–1.9× the market's count in 2023, 2024 and 2026, and 2.8× in 2019.
- **Fuel prices.** Gas, coal and oil are monthly, and coal is Newcastle, not API2. Swap in daily TTF and API2
  if a feed becomes available.
- **2027–2036 projection.** Run the model on forward fuel and carbon, demand growth, and capacity paths for
  thermal plants, batteries and the neighbours, and fade the curve into it beyond Cal-33.

## Dashboard

- **Fair value with forward fuel.** The fair value view prices fuel at the month's spot, held flat. With TTF and
  API2 forward curves the gap would measure mispricing rather than the fuel curve's slope; until then it is not
  a signal (t 1.1 over 86 months).
- **Fair value with growth.** Scaling older weather to today's wind and solar gives 1,000–1,700 negative hours a
  year, because flexibility (batteries, exports) is buried in observed demand. Model batteries and flexible
  demand explicitly, then add the capacity path to the fair value.
- **What moved.** Daily change in the forward curve since the previous settlement, split into gas, carbon and
  residual using the model's sensitivities.
- **Scenarios.** Precompute the model's fair value on a grid of gas, carbon and solar build, and let the view
  move along it.
