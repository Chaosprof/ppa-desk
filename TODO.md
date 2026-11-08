# To do

## Market structure breaks

- **Quarter-hour curve and solar valuation.** The day-ahead auction has cleared in 15-minute periods since
  2025-10-01, and a PPA signed now settles on them. Hourly averages overstate fleet solar capture by 0.010
  (€1.1/MWh) over Oct 2025–Oct 2026; wind is unaffected. Learn a within-hour quarter shape from the
  quarter-hour history, add it to the hourly curve, and value solar volumes and the negative-price clause per
  quarter-hour.
- **Recent-data weighting in the fundamental fit.** Negative prices changed regime in mid-2023: per half-year,
  31–212 negative hours and 28–94 hours at zero before, 176–389 and 111–183 since. The 2023 and 2024 backtest
  years are fitted mostly on the old regime and show the largest negative-hour excess (534 against 301,
  693 against 457). Try weighting recent years, a shorter window, or a zero-bid share that trends over
  time, and check whether those counts come into line without hurting hourly error.
- **Trade drift.** One trade fit over 2019–2026 is off by −1.4 GW in 2019 and +1.4 GW in 2023, around flow-based
  coupling (June 2022), the gas crisis and the nuclear exit (April 2023). Test a time-varying intercept against
  the rolling three-year window.
- **Balancing cost from data.** The PPA's balancing cost uses an assumed €20/MWh spread between reBAP and
  day-ahead. Replace it with the realised spread from reBAP history, from the latest rule change on, at
  quarter-hour resolution.
- **Pre-2018 prices.** Fits for 2019–2021 train partly on the joint DE-AT-LU zone before the October 2018
  split. Minor; note it, or start those windows at the split.

## Fundamental model

- **Fit stability.** The same setup gave 569 and 645 negative hours for 2023 in two runs. Refit from several
  starting points, report the spread of parameters and backtest metrics, and keep the best.
- **Evening peak.** Still about €10 short in 2025 and €20 short in 2026 at 19:00.
- **Negative hours.** Still 1.2–1.8× the market's count in 2023, 2024 and 2026.
- **Fuel prices.** Gas, coal and oil are monthly, and coal is Newcastle, not API2. Swap in daily TTF and API2
  if a feed becomes available.
- **2027–2036 projection.** Run the model on forward fuel and carbon, demand growth, and capacity paths for
  thermal plants, batteries and the neighbours, and fade the curve into it beyond Cal-33.

## Dashboard

- **Fair value with forward fuel.** The fair value view prices fuel at the month's spot, held flat. With TTF and
  API2 forward curves the gap would measure mispricing rather than the fuel curve's slope; until then it is not
  a signal (t 0.5 over 86 months).
- **Fair value with growth.** Scaling older weather to today's wind and solar gives 1,000–1,700 negative hours a
  year, because flexibility (batteries, exports) is buried in observed demand. Model batteries and flexible
  demand explicitly, then add the capacity path to the fair value.
- **What moved.** Daily change in the forward curve since the previous settlement, split into gas, carbon and
  residual using the model's sensitivities.
- **Scenarios.** Precompute the model's fair value on a grid of gas, carbon and solar build, and let the view
  move along it.
