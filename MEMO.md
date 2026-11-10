# German PPA pricing — 2026-10-30

## 10-year solar PPA, South, 100 MW from 2027-01-01

**Pay-as-produced bid: €38.30/MWh** against a volume-weighted baseload forward of €96.63. The shape and capture discount is €49.73, the risk premium €6.32 and balancing €2.49. Executing and funding the hedge costs €0.65 and credit €0.14; guarantees of origin add €1.00. Every MWh produced is paid.

**With a negative-price clause: €61.94/MWh**, paid only in hours at or above zero: 78% of the output in 2027, falling to 74% by 2036 as negative hours grow with the build-out. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth €4.84.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.63 |
| Profile | -35.58 |
| Price–volume correlation | -4.44 |
| Cannibalisation | -9.71 |
| Risk premium (P90) | -6.32 |
| Balancing | -2.49 |
| Hedge execution and margin | -0.65 |
| Credit | -0.14 |
| Guarantees of origin | +1.00 |
| **Price** | **38.30** |

**Hedge:** sell 9.4 MW of Cal-27 baseload, stepping to 7.2 MW by Cal-36 as the capture rate falls. The hedge cuts lifetime P&L volatility from €18.7m to €6.5m. Taken alone, the cannibalisation slope leaves €5.2m, the capture rate from year to year €3.4m, the hourly shape €0.8m and volume €0.7m. The premium breaks even at P90; at P75 it is €3.23 and at P95 it is €8.30. In history (2017–2025), the same rule on a one-year solar PPA bought each October cut year-to-year P&L volatility from €8.7m to €1.3m, against €1.7m for a volume hedge.

**Volume:** P50 1432 full-load hours, P90 1326 over one year and 1339 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 16.3 MW, leaving the generator a realised €24.02–60.46/MWh (P10–P90).

## 10-year wind PPA, North, 100 MW from 2027-01-01

**Pay-as-produced bid: €75.42/MWh** against a volume-weighted baseload forward of €96.41. The shape and capture discount is €13.36, the risk premium €4.53 and balancing €2.82. Executing and funding the hedge costs €1.12 and credit €0.16; guarantees of origin add €1.00. Every MWh produced is paid.

**With a negative-price clause: €85.79/MWh**, paid only in hours at or above zero: 91% of the output in 2027, falling to 89% by 2036 as negative hours grow with the build-out. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth €1.27.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.41 |
| Profile | +2.81 |
| Price–volume correlation | -15.02 |
| Cannibalisation | -1.15 |
| Risk premium (P90) | -4.53 |
| Balancing | -2.82 |
| Hedge execution and margin | -1.12 |
| Credit | -0.16 |
| Guarantees of origin | +1.00 |
| **Price** | **75.42** |

**Hedge:** sell 35.1 MW of Cal-27 baseload, stepping to 33.4 MW by Cal-36 as the capture rate falls. The hedge cuts lifetime P&L volatility from €74.3m to €11.4m. Taken alone, the cannibalisation slope leaves €7.4m, the capture rate from year to year €4.8m, the hourly shape €0.8m and volume €5.0m. The premium breaks even at P90; at P75 it is €2.20 and at P95 it is €5.73. In history (2017–2025), the same rule on a one-year wind PPA bought each October cut year-to-year P&L volatility from €14.4m to €1.8m, against €5.4m for a volume hedge.

**Volume:** P50 3490 full-load hours, P90 3063 over one year and 3125 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 40.0 MW, leaving the generator a realised €72.63–86.16/MWh (P10–P90).

## Assumptions

- Curve: OMIP DE base reference prices, 2026-10-30; flat beyond the last listed calendar year.
- Shape: day-ahead prices over the 36 months before the valuation month.
- Forward vol 27% in the year before delivery, falling to 12% beyond three years: realised on calendar contracts over the last 24 months of OMIP settlements (history since 2016-06-01).
- Cannibalisation slope −0.22 ± 0.09 for solar and −0.04 ± 0.08 for wind, in capture rate per log share of load; the standard error is simulated.
- A year ahead, the capture rate has landed 0.08 from the model for solar and 0.03 for wind (root mean square, 2018–2026); each simulated year draws that error.
- Hedge cost, credit and guarantees of origin are indicative terms from `inputs/market.toml`.
- The slope is fitted on 36 months and depends on that choice more than its standard error shows: on 24, 48 and 60 months the solar slope is +0.01, −0.33 and −0.47 and the solar price €44.76, €33.92 and €28.19.
- 800 joint price–weather paths; imbalance spread and capacity build-out as set in `inputs/market.toml`.

## Fundamental model

An hourly merit order of the German market, built only from public data and fitted on the 3 years before each test year. In 2025 it averaged €89/MWh against €89 actual, missed hourly prices by €19 on average (knowing each month's actual average would miss by €35), and put the solar capture rate at 0.52 against 0.52.

## Fair value

On 2026-10-30 the model priced Cal-27 at €133.08/MWh against €164.27 on the exchange, from the last twelve months replayed at that day's fuel and carbon. Traded month by month since 2019, the gap called the next move 64% of the time over 86 months (t = 1.1): not yet a signal.

€1/MWh on gas moves that fair value by €0.60 and a solar capture price by €0.21; €1/t on carbon moves them by €0.66 and €0.44. The model's gas is the last monthly average, €75.61; at the latest quote of €88.41 the fair value would be about €8 higher.

Built 2026-11-09T23:13:28+00:00 from public data. Interactive version: `python -m ppa serve`.
