# German PPA pricing — 2026-10-30

## 10-year solar PPA, South, 100 MW from 2027-01-01

**Fair pay-as-produced price: €60.81/MWh** against a volume-weighted baseload forward of €96.40. The shape and capture discount is €50.85, the risk premium €1.38 and balancing €2.49; the negative-price clause adds €+19.14.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.40 |
| Profile | -35.39 |
| Price–volume correlation | -4.43 |
| Cannibalisation | -11.03 |
| Risk premium (P90) | -1.38 |
| Balancing | -2.49 |
| Negative-price clause | +19.14 |
| **Fair price** | **60.81** |

**Hedge:** sell 10.0 MW of Cal-27 baseload, stepping to 10.0 MW by Cal-36. The hedge cuts lifetime P&L volatility from €25.3m to €1.7m; what remains is shape (€1.1m) and volume (€0.4m). In history (2017–2025), the same rule on a one-year solar PPA bought each October cut year-to-year P&L volatility from €8.7m to €1.3m, against €1.7m for a volume hedge.

**Volume:** P50 1432 full-load hours, P90 1326 over one year and 1339 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 16.3 MW, leaving the generator a realised €51.69–70.73/MWh (P10–P90).

## 10-year wind PPA, North, 100 MW from 2027-01-01

**Fair pay-as-produced price: €85.94/MWh** against a volume-weighted baseload forward of €96.40. The shape and capture discount is €13.74, the risk premium €2.28 and balancing €2.82; the negative-price clause adds €+8.38.

| Component | €/MWh |
|---|---:|
| Baseload forward | +96.40 |
| Profile | +2.34 |
| Price–volume correlation | -14.72 |
| Cannibalisation | -1.36 |
| Risk premium (P90) | -2.28 |
| Balancing | -2.82 |
| Negative-price clause | +8.38 |
| **Fair price** | **85.94** |

**Hedge:** sell 35.5 MW of Cal-27 baseload, stepping to 34.2 MW by Cal-36. The hedge cuts lifetime P&L volatility from €86.8m to €8.4m; what remains is shape (€9.9m) and volume (€3.2m). In history (2017–2025), the same rule on a one-year wind PPA bought each October cut year-to-year P&L volatility from €14.4m to €1.8m, against €5.4m for a volume hedge.

**Volume:** P50 3490 full-load hours, P90 3063 over one year and 3125 over ten (ERA5 2005–2025).

**Baseload-shaped alternative:** €96.40/MWh on 39.8 MW, leaving the generator a realised €85.16–91.65/MWh (P10–P90).

## Assumptions

- Curve: OMIP DE base reference prices, 2026-10-30; flat beyond the last listed calendar year.
- Shape: day-ahead prices over the 36 months before the valuation month.
- Forward vol 21%: realised on calendar contracts over the last 24 months of OMIP settlements (history since 2016-06-01).
- 800 joint price–weather paths; imbalance spread and capacity build-out as set in `inputs/market.toml`.

Built 2026-11-05T06:08:03+00:00 from public data. Interactive version: `python -m ppa serve`.
