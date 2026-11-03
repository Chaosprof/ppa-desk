import datetime as dt

import numpy as np
import pandas as pd
import pytest

from ppa import hpfc
from ppa.config import load
from ppa.hours import features, hours


@pytest.fixture(scope="session")
def cfg():
    return load()


@pytest.fixture(scope="session")
def synthetic_prices():
    """Three years of hourly prices with a duck curve, weekly and seasonal shape, and noise."""
    idx = hours(dt.date(2023, 10, 1), dt.date(2026, 10, 1))
    f = features(idx)
    hour, month = f["hour"].to_numpy(), f["month"].to_numpy()
    rng = np.random.default_rng(0)
    seasonal = 90 + 25 * np.cos(2 * np.pi * (month - 1) / 12)
    duck = 1 - 0.45 * np.exp(-0.5 * ((hour - 13) / 2.5) ** 2) * (1 + 0.6 * np.sin(np.pi * (month - 3) / 6))
    evening = 1 + 0.35 * np.exp(-0.5 * ((hour - 19) / 1.5) ** 2)
    weekend = np.where(f["daytype"].to_numpy() > 0, 0.82, 1.0)
    p = seasonal * duck * evening * weekend + rng.normal(0, 15, len(idx))
    return pd.Series(p, index=idx)


@pytest.fixture(scope="session")
def window():
    return dt.date(2023, 10, 1), dt.date(2026, 9, 30)


@pytest.fixture(scope="session")
def shape(synthetic_prices, window):
    return hpfc.learn_shape(synthetic_prices, window)


@pytest.fixture(scope="session")
def curve(cfg, shape):
    quotes = hpfc.quotes_from(cfg["futures"], cfg.years)
    return hpfc.build(quotes, shape, end=dt.date(cfg.years[-1] + 1, 1, 1))
