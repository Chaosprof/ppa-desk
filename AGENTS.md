# Agent guide

## Commands

```bash
.venv/bin/python -m ppa build   # models → site/data.json + MEMO.md (~20 s with data/ cached)
.venv/bin/python -m ppa serve   # static site on http://localhost:8000, caching off
.venv/bin/pytest                # no network needed
.venv/bin/ruff check ppa tests
```

## Flow

`inputs/market.toml` → `config.py` → `sources.py` (HTTP + parquet cache in `data/`) → `dataset.py` →
`hpfc.py`, `generation.py`, `capture.py` → `simulate.py` → `valuation.py`, `options.py`, `quality.py` →
`build.py` writes `site/data.json` (the only contract with the front end) and `MEMO.md`.

## Conventions

- Time series are indexed by UTC hour start. Calendar logic (month, day type, peak, holidays) always goes through
  `hours.features`, which works in Europe/Berlin local time. DST days have 23 or 25 hours.
- Prices are EUR/MWh; volumes MWh; capacity MW; P&L EUR (the site shows €m).
- `Shape` is sign-split: `level * up - down`. Positive prices scale with the level, negative ones do not.
- Simulated prices must stay unbiased against the curve (`E[P_h] = F_h`). Decomposition steps must sum to the price.
- Assumptions belong in `inputs/market.toml`, not in code.

## Invariants guarded by tests

- The HPFC reprices every quote. Independent quotes reprice to 1e-8, implied ones to within rounding.
- The daily correction has no steps or kinks.
- The simulation is unbiased, analogue days are equally weighted, and volume matches the full-load-hours target.
- The risk premium makes the hedged P10 exactly zero.
- The valuation decomposition adds up, and the hedge cuts risk.
- Kirk matches Monte Carlo. LSMC lies between intrinsic and perfect foresight.

## Front end

`site/` is plain ES modules with no build step. `app.js` holds state in the URL hash
(`#price?tech=solar&site=south&tenor=10`) and renders one view per sidebar item. `charts.js` draws SVG. Adding a
number to the site means adding it to `build.py` output first. Keep copy minimal: titles and numbers only.
