"""Public data sources, each cached as parquet under data/.

Energy-Charts (Fraunhofer ISE) for day-ahead prices, generation by fuel, cross-border trade, forecasts
and installed capacity; SMARD (Bundesnetzagentur) as an independent cross-check; Open-Meteo ERA5 for
site weather; ECB for the euro discount curve; OMIP for daily German futures reference prices; EEX for
EU carbon auction prices; FRED (IMF) for monthly gas, coal and oil prices; Open Power System Data for
the German plant list with efficiencies. No API keys are needed.
"""

from __future__ import annotations

import datetime as dt
import html
import io
import re
import time
import warnings
import zipfile
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
OMIP = "https://www.omip.pt/en/dados-mercado"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
EEX_ARCHIVE = ("https://www.eex.com/fileadmin/EEX/Downloads/Markets/Environmentals/"
               "EUA_Emission_Spot_Primary_Market_Auction_Report/Archive_Reports/"
               "emission-spot-primary-market-auction-report-2012-2025-data.zip")
EEX_YEAR = "https://public.eex-group.com/eex/eua-auction-report/emission-spot-primary-market-auction-report-{}-data.xlsx"
OPSD = "https://data.open-power-system-data.org/conventional_power_plants/2020-10-01/conventional_power_plants_DE.csv"

DE_LU_START = dt.date(2018, 10, 1)  # DE-AT-LU split into DE-LU and AT
GENERATION = {"Solar": "solar", "Wind onshore": "wind_onshore", "Wind offshore": "wind_offshore", "Load": "load"}
CAPACITY = {"Solar AC": "solar", "Wind onshore": "wind_onshore", "Wind offshore": "wind_offshore"}
MIX = {
    "Nuclear": "nuclear", "Fossil brown coal / lignite": "lignite", "Fossil hard coal": "coal", "Fossil gas": "gas",
    "Fossil oil": "oil", "Fossil coal-derived gas": "coal_gas", "Biomass": "biomass", "Hydro Run-of-River": "ror",
    "Hydro water reservoir": "reservoir", "Hydro pumped storage": "pumped", "Hydro pumped storage consumption":
    "pumping", "Geothermal": "geothermal", "Waste": "waste", "Others": "others", "Wind offshore": "wind_offshore",
    "Wind onshore": "wind_onshore", "Solar": "solar", "Load": "load", "Cross border electricity trading": "trade",
}
INSTALLED = {
    "Nuclear": "nuclear", "Fossil brown coal / lignite": "lignite", "Fossil hard coal": "coal", "Fossil gas": "gas",
    "Fossil oil": "oil", "Hydro pumped storage": "pumped", "Battery storage (power)": "battery",
    "Battery storage (capacity)": "battery_gwh", "Biomass": "biomass", "Solar AC": "solar",
    "Wind onshore": "wind_onshore", "Wind offshore": "wind_offshore",
}


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


def _ec_mix(span: tuple[dt.date, dt.date]) -> pd.DataFrame:
    lo, hi = span

    def build():
        params = {"country": "de", "start": lo.isoformat(), "end": hi.isoformat()}
        d = _get(f"{ENERGY_CHARTS}/public_power", params).json()
        idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
        cols = {MIX[p["name"]]: p["data"] for p in d["production_types"] if p["name"] in MIX}
        return pd.DataFrame(cols, index=idx, dtype=float)

    return _cached(f"chunks/mix_{lo}_{hi}", build)


NEIGHBOURS = ["fr", "nl", "be", "at", "ch", "cz", "pl", "dk", "se", "no"]
CLEAN = ["Nuclear", "Hydro Run-of-River", "Hydro water reservoir", "Biomass", "Geothermal", "Wind onshore",
         "Wind offshore", "Solar"]


def _ec_residual(args) -> pd.Series:
    country, (lo, hi) = args

    def build():
        params = {"country": country, "start": lo.isoformat(), "end": hi.isoformat()}
        d = _get(f"{ENERGY_CHARTS}/public_power", params).json()
        idx = pd.to_datetime(d["unix_seconds"], unit="s", utc=True)
        kinds = {p["name"]: pd.Series(p["data"], index=idx, dtype=float) for p in d["production_types"]}
        clean = sum(kinds[k].fillna(0.0) for k in CLEAN if k in kinds)
        return (kinds["Load"] - clean).rename("residual")

    return _cached(f"chunks/residual_{country}_{lo}_{hi}", build)["residual"]


def neighbour_residual(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Hourly residual load of each neighbour, MW: load less nuclear, hydro, biomass, wind and solar.

    What is left has to come from fossil plants or imports, so it measures how much a neighbour needs.
    """

    def build():
        cols = {}
        for c in NEIGHBOURS:
            parts = _parallel(_ec_residual, [(c, q) for q in _years(start, end)])
            cols[c] = _to_hourly(pd.concat(parts).sort_index())
        return pd.DataFrame(cols)

    return _cached(f"neighbours_{start}_{end}", build)


def mix(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Hourly German public net generation by fuel, load and net trade, MW. Exports and pumping are negative."""

    def build():
        frame = pd.concat(_parallel(_ec_mix, _years(start, end))).sort_index()
        return _to_hourly(frame)

    return _cached(f"mix_{start}_{end}", build)


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


def _ec_installed(step: str, fmt: str) -> pd.DataFrame:
    params = {"country": "de", "time_step": step, "installation_decommission": "false"}
    d = _get(f"{ENERGY_CHARTS}/installed_power", params).json()
    idx = pd.to_datetime(d["time"], format=fmt)
    cols = {INSTALLED[p["name"]]: p["data"] for p in d["production_types"] if p["name"] in INSTALLED}
    return pd.DataFrame(cols, index=idx, dtype=float).dropna(how="all")


def installed() -> pd.DataFrame:
    """Installed German capacity by technology at each month start, GW (battery_gwh in GWh).

    Renewables and batteries are published monthly; thermal plants only as year-end values, which are
    interpolated linearly through the year.
    """

    def build():
        monthly = _ec_installed("monthly", "%m.%Y")
        yearly = _ec_installed("yearly", "%Y")
        yearly.index = yearly.index + pd.offsets.YearEnd(0) + pd.Timedelta(days=1)  # value at year end
        thermal = yearly[[c for c in yearly if c not in monthly]].dropna(how="all")
        months = pd.date_range(thermal.index[0], monthly.index[-1], freq="MS")
        thermal = thermal.reindex(thermal.index.union(months)).interpolate("time").reindex(months).ffill()
        return monthly.join(thermal, how="left")

    return _cached("installed", build)


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


# --- Fuels, carbon and plants ---------------------------------------------------------------------


def fred(series: str, end: dt.date) -> pd.Series:
    """A FRED series up to `end` (IMF monthly commodity prices in USD; DEXUSEU in USD per EUR)."""

    def build():
        text = _get(FRED, {"id": series}).text
        frame = pd.read_csv(io.StringIO(text), index_col=0, parse_dates=True, na_values=".")
        return frame.iloc[:, 0].loc[: pd.Timestamp(end)].dropna().rename("value")

    return _cached(f"fred_{series}_{end}", build)["value"]


def _eua_sheet(content: bytes) -> pd.Series:
    """Auction clearing prices from one EEX yearly report, whatever its layout that year."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sheet = pd.read_excel(io.BytesIO(content), header=None)
    text = sheet.astype(str).where(sheet.notna(), "")
    row = text.index[text.apply(lambda r: r.str.strip().eq("Date").any(), axis=1)][0]
    names = text.iloc[row].str.strip().tolist()
    price = next(i for i, n in enumerate(names) if n.startswith("Auction Price"))
    body = sheet.iloc[row + 1:]
    s = pd.Series(pd.to_numeric(body.iloc[:, price], errors="coerce").to_numpy(),
                  index=pd.to_datetime(body.iloc[:, names.index("Date")], errors="coerce").dt.normalize())
    return s[s.index.notna()].dropna()


def eua(end: dt.date) -> pd.Series:
    """Daily EU carbon price, EUR/t: the mean clearing price of that day's EEX primary auctions."""

    def build():
        with zipfile.ZipFile(io.BytesIO(_get(EEX_ARCHIVE).content)) as z:
            parts = [_eua_sheet(z.read(n)) for n in z.namelist() if n.endswith((".xls", ".xlsx"))]
        parts += [_eua_sheet(_get(EEX_YEAR.format(y)).content) for y in range(2026, end.year + 1)]
        s = pd.concat(parts)
        return s[s.index <= pd.Timestamp(end)].groupby(level=0).mean().rename("eua")

    return _cached(f"eua_{end}", build)["eua"]


def plants() -> pd.DataFrame:
    """German conventional plant list (BNetzA/UBA via Open Power System Data, 2020) with efficiencies."""

    def build():
        frame = pd.read_csv(io.StringIO(_get(OPSD).text), low_memory=False)
        keep = ["id", "name_bnetza", "capacity_net_bnetza", "energy_source", "technology", "chp", "commissioned",
                "status", "efficiency_data", "efficiency_estimate"]
        return frame[keep]

    return _cached("opsd_plants", build)


# --- OMIP ----------------------------------------------------------------------------------------

_OMIP_ROW = re.compile(r"FDB (M \w{3}-\d\d|Q[1-4]-\d\d|YR-\d\d)</strong></td>(.*?)</tr>", re.S)
_OMIP_CELL = re.compile(r'<td width="">([^<]*)</td>')


def parse_omip(page: str) -> dict[str, float]:
    """Reference price per contract from an OMIP German base futures page, as 'Nov-25', 'Q1-26', 'Cal-26'."""
    out = {}
    for name, row in _OMIP_ROW.findall(page):
        cells = [html.unescape(c).strip() for c in _OMIP_CELL.findall(row)]
        if len(cells) < 10 or cells[9] in ("", "n.a."):
            continue
        kind, _, rest = name.partition(" ")
        contract = rest if kind == "M" else name.replace("YR-", "Cal-")
        out[contract] = float(cells[9])  # column D: the reference price for the requested date
    return out


def _omip_day(day: dt.date) -> dict[str, float]:
    params = {"date": day.isoformat(), "product": "EL", "zone": "DE", "instrument": "FDB"}
    page = _get(OMIP, params).text
    if f"date={day.isoformat()}" not in page:  # the page answered for another date
        return {}
    time.sleep(1.0)  # one request a second: this is a public website, not an API
    return parse_omip(page)


def _omip_month(first: dt.date, last: dt.date) -> pd.DataFrame:
    def build():
        rows = []
        for day in pd.bdate_range(first, last).date:
            for contract, price in _omip_day(day).items():
                rows.append({"date": pd.Timestamp(day), "contract": contract, "price": price})
        return pd.DataFrame(rows, columns=["date", "contract", "price"])

    return _cached(f"chunks/omip_DE_{first:%Y-%m}", build)


def omip_history(start: dt.date, end: dt.date, log=lambda m: None) -> pd.DataFrame:
    """Daily OMIP German base futures reference prices: rows of date, contract, price."""
    months = pd.period_range(start, end, freq="M")
    parts = []
    for m in months:
        first = max(start, m.start_time.date())
        last = min(end, m.end_time.date())
        parts.append(_omip_month(first, last))
        log(f"omip {m}")
    frame = pd.concat(parts, ignore_index=True)
    return frame.drop_duplicates(["date", "contract"]).sort_values(["date", "contract"]).reset_index(drop=True)
