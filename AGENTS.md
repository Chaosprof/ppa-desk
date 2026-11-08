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
`hpfc.py`, `generation.py`, `capture.py`, `forwards.py`, `fundamental.py` → `fairvalue.py`, `simulate.py` → `valuation.py`, `options.py`,
`quality.py` →
`build.py` writes `site/data.json` (the only contract with the front end) and `MEMO.md`.

## Conventions

- Time series are indexed by UTC hour start. Calendar logic (month, day type, peak, holidays) always goes through
  `hours.features`, which works in Europe/Berlin local time. DST days have 23 or 25 hours.
- Prices are EUR/MWh; volumes MWh; capacity MW; P&L EUR (the site shows €m).
- `Shape` is sign-split: `level * up - down`. Positive prices scale with the level, negative ones do not.
- Simulated prices must stay unbiased against the curve (`E[P_h] = F_h`). Decomposition steps must sum to the price.
- Assumptions belong in `inputs/market.toml`, not in code.
- `fundamental.py` caches its backtest in `data/` keyed on its config and `VERSION`; bump `VERSION` when the model
  changes. The backtest then re-runs, which takes about 10 minutes.

## Invariants guarded by tests

- The HPFC reprices every quote. Independent quotes reprice to 1e-8, implied ones to within rounding.
- The daily correction has no steps or kinks, and the prior has no steps at month or year boundaries.
- The simulation is unbiased, analogue days are equally weighted, and volume matches the full-load-hours target.
- The risk premium makes the hedged P10 exactly zero.
- The valuation decomposition adds up, and the hedge cuts risk.
- Kirk matches Monte Carlo. LSMC lies between intrinsic and perfect foresight.
- OMIP pages parse to dashboard contract names; realised vol recovers a known vol; re-published holidays are dropped.
- The merit order clears supply to demand, supply rises with price, and more demand never lowers the price. Storage
  never exceeds its power, loses exactly its round-trip efficiency, and idles when the spread does not cover losses. Wind and
  solar curtail only below zero and never beyond the forecast, and the surplus backstop keeps a surplus off the floor. Committed
  thermal output is flat within each day, stays within the fleet, and widens the daily price range.
  The trade fit recovers a known response. Fair values use only data published before their date, replay
  the twelve months before it, and drop nuclear once closed; a contract's next move never crosses its expiry.
  EEX carbon reports parse whatever their yearly layout.

## Front end

`site/` is plain ES modules with no build step. `app.js` holds state in the URL hash
(`#price?tech=solar&site=south&tenor=10`) and renders one view per sidebar item. `charts.js` draws SVG. Adding a
number to the site means adding it to `build.py` output first. Keep copy minimal: titles and numbers only.

Design rules: three colours (ink, muted, accent), plus a diverging blue–warm scale for heatmaps; no borders, dividers, containers, legends (a heatmap's colour key aside), grid lines, tags,
italics, letter-spacing or section numbers. Charts are single lines or solid bars with names at the line ends. Sentence-case
headings. If a view needs explanatory text, simplify the view instead.
