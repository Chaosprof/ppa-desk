"""Public data sources, each cached as parquet under data/.

Energy-Charts (Fraunhofer ISE) for day-ahead prices, generation, forecasts and installed capacity;
SMARD (Bundesnetzagentur) as an independent cross-check; Open-Meteo ERA5 for site weather;
ECB for the euro discount curve. No API keys are needed.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).resolve().parents[1] / "data"
ENERGY_CHARTS = "https://api.energy-charts.info"
SMARD = "https://www.smard.de/app/chart_data"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
ECB = "https://data-api.ecb.europa.eu/service/data/YC"

DE_LU_START = dt.date(2018, 10, 1)  # DE-AT-LU split into DE-LU and AT
GENERATION = {"Solar": "solar", "Wind onshore": "wind_onshore", "Wind offshore": "wind_offshore", "Load": "load"}
CAPACITY = {"Solar AC": "solar", "Wind onshore": "wind_onshore", "Wind offshore": "wind_offshore"}


def _get(url: str, params: dict | None = None) -> requests.Response:
    status = None
    for attempt in range(12):
        try:
            r = requests.get(url, params=params, timeout=120)
            status = r.status_code
            if status == 200:
                return r
            if status not in (429, 500, 502, 503, 504):
                r.raise_for_status()
            wait = float(r.headers.get("Retry-After", 2**attempt))
        except (requests.ConnectionError, requests.Timeout) as e:
            status, wait = type(e).__name__, 2**attempt
        time.sleep(min(wait + 1, 90))
    raise RuntimeError(f"GET {url} {params} failed after retries (last: {status})")


def _cached(name: str, build: Callable[[], pd.DataFrame | pd.Series]) -> pd.DataFrame:
    path = DATA / f"{name}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    frame = build()
    frame = frame.to_frame() if isinstance(frame, pd.Series) else frame
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)
    return frame


def _years(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.date]]:
    """Inclusive (first, last) date pairs covering [start, end] in calendar years."""
    return [(max(start, dt.date(y, 1, 1)), min(end, dt.date(y, 12, 31))) for y in range(start.year, end.year + 1)]


def _parallel(fn, items, workers: int = 1) -> list:
    """Energy-Charts rate-limits per client, so the default is sequential."""
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(fn, items))


def _to_hourly(frame: pd.DataFrame | pd.Series):
    """Energy-Charts mixes hourly and 15-minute resolution; average to hour starts in UTC."""
    frame = frame[~frame.index.duplicated()]
    return frame.resample("h").mean()


# --- Energy-Charts -------------------------------------------------------------------------------


def _ec_prices(span: tuple[dt.date, dt.date]) -> pd.Series:
    lo, hi = span
    zone = "DE-LU" if lo >= DE_LU_START else "DE-AT-LU"

    def build():
        d = _get(f"{ENERGY_CHARTS}/price", {"bzn": zone, "start": lo.isoformat(), "end": hi.isoformat()}).json()
        idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
        return pd.Series(d["price"], index=idx, dtype=float, name="price")

    return _cached(f"chunks/price_{zone}_{lo}_{hi}", build)["price"]


def prices(start: dt.date, end: dt.date) -> pd.Series:
    """Hourly day-ahead price for the German zone, EUR/MWh, local days [start, end]."""

    def build():
        spans = [s for q in _years(start, end) for s in _split_at(q, DE_LU_START)]
        series = pd.concat(_parallel(_ec_prices, spans)).sort_index()
        return _to_hourly(series).rename("price")

    return _cached(f"prices_{start}_{end}", build)["price"]


def _split_at(span, cut):
    lo, hi = span
    if lo < cut <= hi:
        return [(lo, cut - dt.timedelta(days=1)), (cut, hi)]
    return [span]


def _ec_power(span: tuple[dt.date, dt.date]) -> pd.DataFrame:
    lo, hi = span

    def build():
        params = {"country": "de", "start": lo.isoformat(), "end": hi.isoformat()}
        d = _get(f"{ENERGY_CHARTS}/public_power", params).json()
        idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
        cols = {GENERATION[p["name"]]: p["data"] for p in d["production_types"] if p["name"] in GENERATION}
        return pd.DataFrame(cols, index=idx, dtype=float)

    return _cached(f"chunks/power_{lo}_{hi}", build)


def generation(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Hourly German fleet generation and load, MW: solar, wind_onshore, wind_offshore, load."""

    def build():
        frame = pd.concat(_parallel(_ec_power, _years(start, end))).sort_index()
        return _to_hourly(frame)

    return _cached(f"generation_{start}_{end}", build)


def _ec_forecast(args) -> pd.Series:
    kind, (lo, hi) = args

    def build():
        params = {"country": "de", "production_type": kind, "forecast_type": "day-ahead",
                  "start": lo.isoformat(), "end": hi.isoformat()}
        d = _get(f"{ENERGY_CHARTS}/public_power_forecast", params).json()
        idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
        return pd.Series(d["forecast_values"], index=idx, dtype=float, name=kind)

    return _cached(f"chunks/forecast_{kind}_{lo}_{hi}", build)[kind]


def forecasts(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Hourly day-ahead fleet forecasts, MW: solar, wind_onshore, wind_offshore."""

    def build():
        kinds = ["solar", "wind_onshore", "wind_offshore"]
        cols = {}
        for kind in kinds:
            parts = _parallel(_ec_forecast, [(kind, q) for q in _years(start, end)])
            cols[kind] = _to_hourly(pd.concat(parts).sort_index())
        return pd.DataFrame(cols)

    return _cached(f"forecasts_{start}_{end}", build)


def capacity() -> pd.DataFrame:
    """Monthly installed German capacity, GW, indexed by month start."""

    def build():
        params = {"country": "de", "time_step": "monthly", "installation_decommission": "false"}
        d = _get(f"{ENERGY_CHARTS}/installed_power", params).json()
        idx = pd.to_datetime(d["time"], format="%m.%Y")
        cols = {CAPACITY[p["name"]]: p["data"] for p in d["production_types"] if p["name"] in CAPACITY}
        return pd.DataFrame(cols, index=idx, dtype=float).dropna(how="all")

    return _cached("capacity", build)


# --- SMARD ---------------------------------------------------------------------------------------


def _smard_week(args) -> pd.Series:
    code, region, ts = args
    d = _get(f"{SMARD}/{code}/{region}/{code}_{region}_hour_{ts}.json").json()
    s = pd.DataFrame(d["series"], columns=["t", "v"]).dropna()
    return pd.Series(s["v"].to_numpy(float), index=pd.to_datetime(s["t"], unit="ms", utc=True))


def smard(code: int, region: str, start: dt.date, end: dt.date) -> pd.Series:
    """Hourly SMARD series (4169 = DE-LU day-ahead price, 4068 = solar generation)."""

    def build():
        index = _get(f"{SMARD}/{code}/{region}/index_hour.json").json()["timestamps"]
        lo = pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=7)
        hi = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
        weeks = [t for t in index if lo.value // 10**6 <= t < hi.value // 10**6]
        series = pd.concat(_parallel(_smard_week, [(code, region, t) for t in weeks], workers=4)).sort_index()
        series = series[~series.index.duplicated()]
        local = series.index.tz_convert("Europe/Berlin").date
        return series[(local >= start) & (local <= end)].rename("value")

    return _cached(f"smard_{code}_{region}_{start}_{end}", build)["value"]


# --- Open-Meteo ERA5 -----------------------------------------------------------------------------

WEATHER = ["shortwave_radiation", "direct_normal_irradiance", "diffuse_radiation",
           "temperature_2m", "wind_speed_10m", "wind_speed_100m"]


def _weather_chunk(args) -> pd.DataFrame:
    lat, lon, lo, hi = args
    params = {"latitude": lat, "longitude": lon, "start_date": lo.isoformat(), "end_date": hi.isoformat(),
              "hourly": ",".join(WEATHER), "models": "era5", "timezone": "UTC", "wind_speed_unit": "ms"}
    d = _get(OPEN_METEO, params).json()["hourly"]
    idx = pd.to_datetime(d.pop("time"), utc=True)
    return pd.DataFrame(d, index=idx, dtype=float)


def weather(site: str, lat: float, lon: float, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Hourly ERA5 weather at a site. Radiation is the mean over the hour *ending* at the stamp."""

    def build():
        chunks, lo = [], start
        while lo <= end:
            hi = min(dt.date(lo.year + 3, 12, 31), end)
            chunks.append((lat, lon, lo, hi))
            lo = hi + dt.timedelta(days=1)
        parts = []
        for c in chunks:  # Open-Meteo rate-limits by volume; stay sequential.
            parts.append(_weather_chunk(c))
        return pd.concat(parts).sort_index()

    return _cached(f"weather_{site}_{start}_{end}", build)


# --- ECB -----------------------------------------------------------------------------------------


def discount_curve(on: dt.date) -> pd.Series:
    """Euro area AAA spot yields (decimal) by maturity in years, last fixing on or before `on`."""

    def build():
        out = {}
        for years in range(1, 16):
            key = f"B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{years}Y"
            params = {"endPeriod": on.isoformat(), "lastNObservations": 1, "format": "csvdata"}
            rows = _get(f"{ECB}/{key}", params).text.strip().splitlines()
            header, last = rows[0].split(","), rows[-1].split(",")
            out[years] = float(last[header.index("OBS_VALUE")]) / 100
        return pd.Series(out, name="rate")

    return _cached(f"ecb_{on}", build)["rate"]
