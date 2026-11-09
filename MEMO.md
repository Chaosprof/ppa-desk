# German PPA pricing — 2026-10-30

## 10-year solar PPA, South, 100 MW from 2027-01-01

**Fair pay-as-produced price: €36.93/MWh** against a volume-weighted baseload forward of €96.38. The shape and capture discount is €50.80, the risk premium €6.15 and balancing €2.49. Every MWh produced is paid.

**With a negative-price clause: €62.34/MWh**, paid only in hours at or above zero: 78% of the output in 2027, falling to 73% by 2036 as negative hours grow with the build-out. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth €4.96.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.38 |
| Profile | -35.49 |
| Price–volume correlation | -4.43 |
| Cannibalisation | -10.88 |
| Risk premium (P90) | -6.15 |
| Balancing | -2.49 |
| **Fair price** | **36.93** |

**Hedge:** sell 9.2 MW of Cal-27 baseload, stepping to 7.2 MW by Cal-36 as the capture rate falls. The hedge cuts lifetime P&L volatility from €18.3m to €6.1m. Taken alone, shape and cannibalisation leave €6.0m and volume €0.7m. In history (2017–2025), the same rule on a one-year solar PPA bought each October cut year-to-year P&L volatility from €8.7m to €1.3m, against €1.7m for a volume hedge.

**Volume:** P50 1432 full-load hours, P90 1326 over one year and 1339 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 16.3 MW, leaving the generator a realised €22.49–59.62/MWh (P10–P90).

## 10-year wind PPA, North, 100 MW from 2027-01-01

**Fair pay-as-produced price: €75.62/MWh** against a volume-weighted baseload forward of €96.41. The shape and capture discount is €13.58, the risk premium €4.40 and balancing €2.82. Every MWh produced is paid.

**With a negative-price clause: €86.42/MWh**, paid only in hours at or above zero: 91% of the output in 2027, falling to 88% by 2036 as negative hours grow with the build-out. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth €1.32.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.41 |
| Profile | +2.81 |
| Price–volume correlation | -15.02 |
| Cannibalisation | -1.37 |
| Risk premium (P90) | -4.40 |
| Balancing | -2.82 |
| **Fair price** | **75.62** |

**Hedge:** sell 35.0 MW of Cal-27 baseload, stepping to 33.4 MW by Cal-36 as the capture rate falls. The hedge cuts lifetime P&L volatility from €73.9m to €11.2m. Taken alone, shape and cannibalisation leave €8.8m and volume €5.0m. In history (2017–2025), the same rule on a one-year wind PPA bought each October cut year-to-year P&L volatility from €14.4m to €1.8m, against €5.4m for a volume hedge.

**Volume:** P50 3490 full-load hours, P90 3063 over one year and 3125 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 40.0 MW, leaving the generator a realised €72.67–86.08/MWh (P10–P90).

## Assumptions

- Curve: OMIP DE base reference prices, 2026-10-30; flat beyond the last listed calendar year.
- Shape: day-ahead prices over the 36 months before the valuation month.
- Forward vol 27% in the year before delivery, falling to 12% beyond three years: realised on calendar contracts over the last 24 months of OMIP settlements (history since 2016-06-01).
- Cannibalisation slope −0.22 ± 0.09 for solar and −0.04 ± 0.08 for wind, in capture rate per log share of load; the standard error is simulated.
- The slope is fitted on 36 months and depends on that choice more than its standard error shows: on 24, 48 and 60 months the solar slope is +0.01, −0.33 and −0.47 and the solar price €44.27, €32.12 and €25.63.
- 800 joint price–weather paths; imbalance spread and capacity build-out as set in `inputs/market.toml`.

## Fundamental model

An hourly merit order of the German market, built only from public data and fitted on the 3 years before each test year. In 2025 it averaged €89/MWh against €89 actual, missed hourly prices by €19 on average (knowing each month's actual average would miss by €35), and put the solar capture rate at 0.52 against 0.52.

## Fair value

On 2026-10-30 the model priced Cal-27 at €133.08/MWh against €164.27 on the exchange, from the last twelve months replayed at that day's fuel and carbon. Traded month by month since 2019, the gap called the next move 64% of the time over 86 months (t = 1.1): not yet a signal.

€1/MWh on gas moves that fair value by €0.60 and a solar capture price by €0.21; €1/t on carbon moves them by €0.66 and €0.44. The model's gas is the last monthly average, €75.61; at the latest quote of €88.41 the fair value would be about €8 higher.

Built 2026-11-09T22:24:43+00:00 from public data. Interactive version: `python -m ppa serve`.
