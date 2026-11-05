"""Every observed input the model uses, assembled from the cached public sources."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import pandas as pd

from . import sources
from .config import HISTORY_START, WEATHER_START, Config


@dataclass
class Dataset:
    cfg: Config

    @cached_property
    def prices(self) -> pd.Series:
        return sources.prices(HISTORY_START, self.cfg.history_end)

    @cached_property
    def generation(self) -> pd.DataFrame:
        return sources.generation(HISTORY_START, self.cfg.history_end)

    @cached_property
    def capacity(self) -> pd.DataFrame:
        return sources.capacity()

    @cached_property
    def forecasts(self) -> pd.DataFrame:
        return sources.forecasts(*self.cfg.window)

    @cached_property
    def smard_prices(self) -> pd.Series:
        return sources.smard(4169, "DE-LU", *self.cfg.window)

    @cached_property
    def smard_solar(self) -> pd.Series:
        return sources.smard(4068, "DE", *self.cfg.window)

    @cached_property
    def omip_history(self) -> pd.DataFrame:
        return sources.omip_history(self.cfg["forwards"]["history_start"], self.cfg.valuation_date)

    @cached_property
    def mix(self) -> pd.DataFrame:
        return sources.mix(HISTORY_START, self.cfg.history_end)

    @cached_property
    def da_forecasts(self) -> pd.DataFrame:
        return sources.forecasts(HISTORY_START, self.cfg.history_end)

    @cached_property
    def installed(self) -> pd.DataFrame:
        return sources.installed()

    @cached_property
    def eua(self) -> pd.Series:
        return sources.eua(self.cfg.history_end)

    @cached_property
    def fuels(self) -> dict[str, pd.Series]:
        """Monthly IMF prices via FRED: TTF gas USD/MMBtu, Newcastle coal USD/t, Brent USD/bbl; daily USD per EUR."""
        ids = {"gas": "PNGASEUUSDM", "coal": "PCOALAUUSDM", "oil": "POILBREUSDM", "usd": "DEXUSEU"}
        return {k: sources.fred(v, self.cfg.history_end) for k, v in ids.items()}

    @cached_property
    def neighbours(self) -> pd.DataFrame:
        return sources.neighbour_residual(HISTORY_START, self.cfg.history_end)

    @cached_property
    def plants(self) -> pd.DataFrame:
        return sources.plants()

    @cached_property
    def discount_curve(self) -> pd.Series:
        return sources.discount_curve(self.cfg.valuation_date)

    def weather(self, site: str) -> pd.DataFrame:
        s = self.cfg.sites[site]
        return sources.weather(site, s["lat"], s["lon"], WEATHER_START, self.cfg.history_end)

    def fetch_all(self, log=print) -> None:
        for name in ["prices", "generation", "capacity", "forecasts", "smard_prices", "smard_solar", "discount_curve",
                     "omip_history", "mix", "da_forecasts", "installed", "eua", "fuels", "neighbours", "plants"]:
            log(f"fetch {name}")
            getattr(self, name)
        for site in self.cfg.sites:
            log(f"fetch weather {site}")
            self.weather(site)
