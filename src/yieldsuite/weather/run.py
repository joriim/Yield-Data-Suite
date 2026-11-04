"""Fetch (or reuse) field weather and compute the agroclimate summary and RUE."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import FieldYearConfig
from ..l4.grid import Grid
from . import openmeteo
from .agro import AgroSettings, daily_indices, season_summary
from .rue import field_rue, rue_map


def field_location(config: FieldYearConfig, grid: Grid | None = None) -> tuple[float, float]:
    """(lat, lon) of the field: from the config, else the centre of the yield grid."""
    if config.location:
        return config.location
    if grid is None:
        raise ValueError("no location: set location: {lat, lon} or run the chain to L4 first")
    from pyproj import Transformer

    x = grid.xmin + grid.nx * grid.resolution / 2
    y = grid.ymax - grid.ny * grid.resolution / 2
    lon, lat = Transformer.from_crs(config.crs, "EPSG:4326", always_xy=True).transform(x, y)
    return float(lat), float(lon)


def agro_settings(config: FieldYearConfig, harvest_fallback: date | None = None) -> AgroSettings:
    c = config.crop
    if c is None:
        return AgroSettings(harvest_date=harvest_fallback)
    return AgroSettings(
        crop=c.name,
        sowing_date=date.fromisoformat(c.sowing_date) if c.sowing_date else None,
        harvest_date=date.fromisoformat(c.harvest_date) if c.harvest_date else harvest_fallback,
        t_base_c=c.t_base_c,
        t_cap_c=c.t_cap_c,
        par_fraction=c.par_fraction,
        harvest_index=c.harvest_index,
        stages_gdd=dict(c.stages_gdd),
        ptq_window=tuple(c.ptq_window) if c.ptq_window else None,
    )


def weather_path(config: FieldYearConfig) -> Path:
    """Public-data cache next to the outputs: <output_dir>/../public/era5_daily.csv."""
    return Path(config.output_dir).parent / "public" / "era5_daily.csv"


def get_weather(
    config: FieldYearConfig,
    grid: Grid | None = None,
    refresh: bool = False,
    fetch=None,
) -> tuple[pd.DataFrame, dict, tuple[float, float]]:
    path = weather_path(config)
    lat, lon = field_location(config, grid)
    if path.exists() and not refresh:
        df, prov = openmeteo.load(path)
        return df, prov, (lat, lon)
    req = openmeteo.WeatherRequest(
        lat=lat,
        lon=lon,
        start=f"{config.year}-03-01",
        end=f"{config.year}-10-31",
        model=config.weather.model,
        hourly=tuple(config.weather.hourly),
        round_deg=config.weather.round_deg,
    )
    today = date.today().isoformat()
    if req.end > today:
        req.end = today
    df, prov = (fetch or openmeteo.fetch_daily)(req)
    openmeteo.save(df, prov, path)
    return df, prov, (lat, lon)


def run_agroclimate(
    config: FieldYearConfig,
    grid: Grid | None = None,
    harvest_fallback: date | None = None,
    refresh: bool = False,
    l5_bands: dict | None = None,
    features_long: pd.DataFrame | None = None,
    fetch=None,
) -> dict:
    """Season summary (+ field RUE and a per-cell RUE map when yield and NDVI exist)."""
    weather, prov, (lat, lon) = get_weather(config, grid, refresh, fetch)
    s = agro_settings(config, harvest_fallback)
    summary = season_summary(weather, lat, s)
    summary["location_used"] = {
        "lat": prov.get("request", {}).get("lat", round(lat, 2)),
        "lon": prov.get("request", {}).get("lon", round(lon, 2)),
    }
    summary["weather_source"] = prov.get("source")
    summary["crop"] = {
        "name": s.crop,
        "sowing_date": str(s.sowing_date),
        "harvest_date": str(s.harvest_date),
        "harvest_index": s.harvest_index,
    }
    out = {"summary": summary, "weather_file": str(weather_path(config))}
    if s.sowing_date is None:
        summary["note"] = "no sowing date: totals cover the whole fetched period"

    if (
        l5_bands is not None
        and features_long is not None
        and s.sowing_date
        and s.harvest_date
        and grid is not None
    ):
        par = daily_indices(weather, lat, s)["par_mj_m2"]
        nd = features_long[features_long["index"] == "NDVI"]
        if not nd.empty:
            wide = nd.pivot_table(index="date", columns="cell", values="value")
            wide = wide.reindex(columns=range(grid.ny * grid.nx))
            y = np.asarray(l5_bands["yield"], float).ravel()
            res = rue_map(
                y,
                wide,
                par,
                s.sowing_date,
                s.harvest_date,
                config.reference_moisture_pct,
                s.harvest_index,
            )
            shape = (grid.ny, grid.nx)
            out["rue_bands"] = {k: v.reshape(shape) for k, v in res.items()}
            fld = field_rue(
                float(np.nanmean(y)),
                wide.mean(axis=1),
                par,
                s.sowing_date,
                s.harvest_date,
                config.reference_moisture_pct,
                s.harvest_index,
            )
            summary["field_rue"] = fld
            r = res["rue_g_mj"]
            summary["rue_map"] = {"p10": _pct(r, 10), "median": _pct(r, 50), "p90": _pct(r, 90)}
    return out


def _pct(a, q):
    a = np.asarray(a, float)
    return float(np.nanpercentile(a, q)) if np.isfinite(a).any() else None


def write_agroclimate(config: FieldYearConfig, result: dict, grid: Grid | None) -> dict:
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"L5_{config.field_id}_{config.year}"
    files = {}
    p = out / f"{stem}_agroclimate.json"
    p.write_text(json.dumps(result["summary"], indent=2, default=str))
    files["agroclimate"] = str(p)
    if "rue_bands" in result and grid is not None:
        try:
            from ..l4.io import write_cog

            names = ("rue_g_mj", "ipar_mj_m2", "biomass_g_m2")
            files["rue"] = str(
                write_cog(
                    result["rue_bands"], grid, config.crs, out / f"{stem}_rue.tif", names=names
                )
            )
        except ImportError:
            pass
    return files
