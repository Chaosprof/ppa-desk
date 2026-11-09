# German PPA pricing — 2026-10-30

## 10-year solar PPA, South, 100 MW from 2027-01-01

**Fair pay-as-produced price: €36.69/MWh** against a volume-weighted baseload forward of €96.40. The shape and capture discount is €50.86, the risk premium €6.35 and balancing €2.49. Every MWh produced is paid.

**With a negative-price clause: €55.96/MWh**, paid only in hours at or above zero, which is 80% of the output. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth €3.98.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.40 |
| Profile | -35.39 |
| Price–volume correlation | -4.43 |
| Cannibalisation | -11.04 |
| Risk premium (P90) | -6.35 |
| Balancing | -2.49 |
| **Fair price** | **36.69** |

**Hedge:** sell 9.2 MW of Cal-27 baseload, stepping to 7.3 MW by Cal-36 as the capture rate falls. The hedge cuts lifetime P&L volatility from €20.8m to €6.0m; what remains is shape and cannibalisation (€5.9m) and volume (€0.8m). In history (2017–2025), the same rule on a one-year solar PPA bought each October cut year-to-year P&L volatility from €8.7m to €1.3m, against €1.7m for a volume hedge.

**Volume:** P50 1432 full-load hours, P90 1326 over one year and 1339 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 16.3 MW, leaving the generator a realised €18.65–62.14/MWh (P10–P90).

## 10-year wind PPA, North, 100 MW from 2027-01-01

**Fair pay-as-produced price: €75.39/MWh** against a volume-weighted baseload forward of €96.40. The shape and capture discount is €13.74, the risk premium €4.45 and balancing €2.82. Every MWh produced is paid.

**With a negative-price clause: €83.77/MWh**, paid only in hours at or above zero, which is 92% of the output. The price is per paid MWh, so it is higher on less volume; the puts themselves are worth €1.04.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.40 |
| Profile | +2.34 |
| Price–volume correlation | -14.72 |
| Cannibalisation | -1.36 |
| Risk premium (P90) | -4.45 |
| Balancing | -2.82 |
| **Fair price** | **75.39** |

**Hedge:** sell 35.3 MW of Cal-27 baseload, stepping to 33.4 MW by Cal-36 as the capture rate falls. The hedge cuts lifetime P&L volatility from €85.9m to €12.0m; what remains is shape and cannibalisation (€12.9m) and volume (€5.1m). In history (2017–2025), the same rule on a one-year wind PPA bought each October cut year-to-year P&L volatility from €14.4m to €1.8m, against €5.4m for a volume hedge.

**Volume:** P50 3490 full-load hours, P90 3063 over one year and 3125 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 39.8 MW, leaving the generator a realised €71.47–86.34/MWh (P10–P90).

## Assumptions

- Curve: OMIP DE base reference prices, 2026-10-30; flat beyond the last listed calendar year.
- Shape: day-ahead prices over the 36 months before the valuation month.
- Forward vol 21%: realised on calendar contracts over the last 24 months of OMIP settlements (history since 2016-06-01).
- Cannibalisation slope −0.22 ± 0.09 for solar and −0.04 ± 0.08 for wind, in capture rate per log share of load; the standard error is simulated.
- 800 joint price–weather paths; imbalance spread and capacity build-out as set in `inputs/market.toml`.

## Fundamental model

An hourly merit order of the German market, built only from public data and fitted on the 3 years before each test year. In 2025 it averaged €88/MWh against €89 actual, missed hourly prices by €19 on average (knowing each month's actual average would miss by €35), and put the solar capture rate at 0.51 against 0.52.

## Fair value

On 2026-10-30 the model priced Cal-27 at €130.32/MWh against €164.27 on the exchange, from the last twelve months replayed at that day's fuel and carbon. Traded month by month since 2019, the gap called the next move 61% of the time over 86 months (t = 0.5): not yet a signal.

Built 2026-11-09T21:40:22+00:00 from public data. Interactive version: `python -m ppa serve`.
