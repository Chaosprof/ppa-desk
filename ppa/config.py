"""Load inputs/market.toml and derive the date windows every module agrees on."""

from __future__ import annotations

import datetime as dt
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "inputs" / "market.toml"

HISTORY_START = dt.date(2015, 1, 1)  # first year of Energy-Charts day-ahead and fleet data used
WEATHER_START = dt.date(2005, 1, 1)  # ERA5 years for P50/P90


@dataclass(frozen=True)
class Config:
    raw: dict

    def __getitem__(self, key):
        return self.raw[key]

    @property
    def valuation_date(self) -> dt.date:
        return self.raw["valuation_date"]

    @property
    def ppa_start(self) -> dt.date:
        return self.raw["ppa_start"]

    @property
    def years(self) -> list[int]:
        """Delivery years covered by the longest tenor."""
        first = self.ppa_start.year
        return list(range(first, first + self.raw["max_tenor_years"]))

    @property
    def history_end(self) -> dt.date:
        """Last day of data known on the valuation date."""
        return self.valuation_date - dt.timedelta(days=1)

    @property
    def window(self) -> tuple[dt.date, dt.date]:
        """Whole months of recent history that define today's shape: [first, last] dates."""
        v = self.valuation_date
        last = dt.date(v.year, v.month, 1) - dt.timedelta(days=1)
        months = self.raw["shape"]["history_months"]
        m = last.year * 12 + last.month - months  # zero-based month index of the first month
        return dt.date(m // 12, m % 12 + 1, 1), last

    @property
    def sites(self) -> dict[str, dict]:
        return self.raw["sites"]


def load(path: Path = MARKET) -> Config:
    with open(path, "rb") as f:
        return Config(tomllib.load(f))
