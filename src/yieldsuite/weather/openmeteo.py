"""ERA5 / ERA5-Land daily weather for one field from the Open-Meteo archive API.

No account is needed. Only a rounded field location (default 0.05°, ~5 km)
is sent, which is finer than the ERA5 grid itself, so the field's exact
position never leaves the farm.

Daily variables (defaults): temperature max/min/mean, shortwave radiation sum
(all-sky, i.e. clouds included), precipitation, FAO-56 reference ET,
sunshine and daylight duration. Hourly variables (e.g. soil moisture, vapour
pressure deficit) can be added and are averaged per day.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DEFAULT_DAILY = (
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "shortwave_radiation_sum",  # MJ/m2/day, all-sky
    "precipitation_sum",  # mm
    "et0_fao_evapotranspiration",  # mm
    "sunshine_duration",  # s
    "daylight_duration",  # s
)


@dataclass
class WeatherRequest:
    lat: float
    lon: float
    start: str  # ISO date, inclusive
    end: str  # ISO date, inclusive
    model: str = "era5_seamless"  # era5 | era5_land | era5_seamless | best_match
    daily: tuple[str, ...] = DEFAULT_DAILY
    hourly: tuple[str, ...] = ()  # e.g. soil_moisture_0_to_7cm, vapour_pressure_deficit
    round_deg: float = 0.05

    def rounded(self) -> tuple[float, float]:
        r = self.round_deg
        return (round(round(self.lat / r) * r, 4), round(round(self.lon / r) * r, 4))

    def url(self) -> str:
        lat, lon = self.rounded()
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": self.start,
            "end_date": self.end,
            "daily": ",".join(self.daily),
            "timezone": "auto",
            "models": self.model,
        }
        if self.hourly:
            params["hourly"] = ",".join(self.hourly)
        return f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}"


def parse_response(payload: dict) -> pd.DataFrame:
    """Daily DataFrame indexed by date; hourly variables are averaged per day."""
    if payload.get("error"):
        raise ValueError(f"Open-Meteo: {payload.get('reason', 'error')}")
    daily = payload.get("daily") or {}
    if "time" not in daily:
        raise ValueError("Open-Meteo response has no daily data")
    df = pd.DataFrame(daily)
    df["date"] = pd.to_datetime(df.pop("time")).dt.date
    df = df.set_index("date")
    hourly = payload.get("hourly")
    if hourly and "time" in hourly:
        h = pd.DataFrame(hourly)
        h["date"] = pd.to_datetime(h.pop("time")).dt.date
        df = df.join(h.groupby("date").mean().add_suffix("_daily_mean"), how="left")
    return df


def fetch_daily(request: WeatherRequest, timeout: float = 30.0) -> tuple[pd.DataFrame, dict]:
    """Fetch and parse. Returns (daily frame, provenance)."""
    url = request.url()
    req = urllib.request.Request(url, headers={"User-Agent": "yieldsuite"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed host
        payload = json.loads(resp.read().decode())
    df = parse_response(payload)
    provenance = {
        "source": "Open-Meteo historical weather API (ECMWF ERA5 / ERA5-Land)",
        "url": url,
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request": asdict(request) | {"lat": request.rounded()[0], "lon": request.rounded()[1]},
        "grid_point": {k: payload.get(k) for k in ("latitude", "longitude", "elevation")},
        "units": payload.get("daily_units", {}),
        "licence": "ERA5: Copernicus Climate Change Service; Open-Meteo data CC BY 4.0",
    }
    return df, provenance


def save(df: pd.DataFrame, provenance: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    path.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2))
    return path


def load(path: str | Path) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    df = pd.read_csv(path, index_col="date", parse_dates=["date"])
    df.index = df.index.date
    df.index.name = "date"
    prov_path = path.with_suffix(".provenance.json")
    prov = json.loads(prov_path.read_text()) if prov_path.exists() else {}
    return df, prov


@dataclass
class WeatherSettings:
    source: str = "open-meteo"
    model: str = "era5_seamless"
    hourly: list[str] = field(default_factory=list)
    round_deg: float = 0.05
