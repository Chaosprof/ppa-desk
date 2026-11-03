# German PPA pricing — 2026-10-30

## 10-year solar PPA, South, 100 MW from 2027-01-01

**Fair pay-as-produced price: €60.66/MWh** against a volume-weighted baseload forward of €96.40. The shape and capture discount is €50.66, the risk premium €1.65 and balancing €2.49; the negative-price clause adds €+19.07.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.40 |
| Profile | -35.21 |
| Price–volume correlation | -4.42 |
| Cannibalisation | -11.03 |
| Risk premium (P90) | -1.65 |
| Balancing | -2.49 |
| Negative-price clause | +19.07 |
| **Fair price** | **60.66** |

**Hedge:** sell 10.0 MW of Cal-27 baseload, stepping to 10.0 MW by Cal-36. The hedge cuts lifetime P&L volatility from €38.0m to €2.6m; what remains is shape (€1.6m) and volume (€0.4m).

**Volume:** P50 1432 full-load hours, P90 1326 over one year and 1339 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 16.3 MW, leaving the generator a realised €47.27–73.77/MWh (P10–P90).

## 10-year wind PPA, North, 100 MW from 2027-01-01

**Fair pay-as-produced price: €84.49/MWh** against a volume-weighted baseload forward of €96.40. The shape and capture discount is €13.78, the risk premium €3.09 and balancing €2.82; the negative-price clause adds €+7.77.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.40 |
| Profile | +2.34 |
| Price–volume correlation | -14.76 |
| Cannibalisation | -1.36 |
| Risk premium (P90) | -3.09 |
| Balancing | -2.82 |
| Negative-price clause | +7.77 |
| **Fair price** | **84.49** |

**Hedge:** sell 35.4 MW of Cal-27 baseload, stepping to 34.0 MW by Cal-36. The hedge cuts lifetime P&L volatility from €130.3m to €13.1m; what remains is shape (€15.5m) and volume (€3.4m).

**Volume:** P50 3490 full-load hours, P90 3063 over one year and 3125 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 39.8 MW, leaving the generator a realised €83.23–92.69/MWh (P10–P90).

## Assumptions

- Curve: OMIP DE base reference prices, 2026-10-30; flat beyond the last listed calendar year.
- Shape: day-ahead prices over the 36 months before the valuation month.
- 800 joint price–weather paths; forward vol, imbalance spread and capacity build-out as set in `inputs/market.toml`.

Built 2026-11-03T09:51:01+00:00 from public data. Interactive version: `python -m ppa serve`.
