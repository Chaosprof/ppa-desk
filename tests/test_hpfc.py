import datetime as dt

import numpy as np
import pytest

from ppa import hpfc
from ppa.hours import features, hours


def test_every_quoted_future_is_repriced(curve):
    for q in curve.quotes:
        assert curve.reprice(q) == pytest.approx(q.price, abs=0.006), q.contract


def test_independent_quotes_are_repriced_to_machine_precision(curve):
    implied = {k.split()[0] for k in curve.implied}
    for q in curve.quotes:
        if q.contract not in implied:
            assert abs(curve.reprice(q) - q.price) < 1e-8, q.contract


def test_redundant_quotes_are_detected_and_consistent(curve):
    # OMIP quotes months, quarters and calendars; Q1-27 and Cal-27 are implied by finer contracts.
    assert "Q1-27" in curve.implied and "Cal-27" in curve.implied
    for name, implied in curve.implied.items():
        quote = next(q.price for q in curve.quotes if q.contract == name)
        assert implied == pytest.approx(quote, abs=0.006)


def test_curve_covers_every_delivery_hour(curve, cfg):
    idx = hours(cfg.ppa_start, dt.date(cfg.years[-1] + 1, 1, 1))
    assert curve.hourly.reindex(idx).notna().all()


def test_no_jumps_at_contract_boundaries(curve):
    """The correction to the seasonal prior is smooth across delivery-period boundaries."""
    a = curve.correction.to_numpy()
    assert np.abs(np.diff(a)).max() < 2.0  # EUR/MWh per day: no steps
    assert np.abs(np.diff(a, 2)).max() < 0.1  # EUR/MWh per day²: no kinks


def test_extrapolated_years_are_flat_nominal(curve, cfg):
    last = max((q for q in curve.quotes if q.contract.startswith("Cal") and not q.extrapolated),
               key=lambda q: q.period)
    for q in curve.quotes:
        if q.extrapolated:
            assert q.price == last.price
            assert curve.reprice(q) == pytest.approx(last.price, abs=1e-8)


def test_shape_is_normalised(shape):
    assert shape.month.mean() == pytest.approx(1, abs=0.02)
    level = 100.0
    price = level * shape.up - shape.down  # expected price by month x day type x hour
    weekly = np.average(price.mean(axis=2), axis=1, weights=[5, 1, 1])  # weekdays, Saturday, Sunday
    assert weekly == pytest.approx(np.full(12, level), rel=0.03)


def test_negative_prices_do_not_scale_with_level(shape):
    f = features(hours(dt.date(2027, 6, 6), dt.date(2027, 6, 7)))  # a Sunday
    low, high = shape(f, np.full(len(f), 50.0)), shape(f, np.full(len(f), 200.0))
    negative_part = shape.down[5, 2]
    assert np.allclose(high - low, 150.0 * shape.up[5, 2, f["hour"].to_numpy()])
    assert np.all(negative_part >= 0)


def test_shape_recovers_the_duck_curve(shape):
    june_weekday = 100 * shape.up[5, 0] - shape.down[5, 0]
    assert june_weekday.argmin() in (12, 13, 14)
    assert june_weekday.argmax() in (18, 19, 20)


def test_peak_quotes_are_honoured(cfg, shape):
    quotes = hpfc.quotes_from(cfg["futures"], cfg.years[:3])
    quotes.append(hpfc.Quote("Cal-27", 150.0, peak=True))
    c = hpfc.build(quotes, shape)
    assert c.reprice(quotes[-1]) == pytest.approx(150.0, abs=1e-8)
    assert c.average(*hpfc.delivery("Cal-27")) == pytest.approx(164.27, abs=0.006)  # implied by quarters


def test_inconsistent_redundant_quote_is_flagged(cfg, shape):
    quotes = [q for q in hpfc.quotes_from(cfg["futures"], cfg.years[:2]) if q.contract != "Cal-27"]
    quotes.append(hpfc.Quote("Cal-27", 170.0))
    c = hpfc.build(quotes, shape)
    assert abs(c.implied["Cal-27"] - 170.0) > 5


def test_peak_mask_matches_phelix_definition():
    idx = hours(dt.date(2027, 1, 4), dt.date(2027, 1, 11))  # Monday to Monday
    f = features(idx)
    assert f["peak"].sum() == 5 * 12
