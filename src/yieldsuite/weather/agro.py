"""Agroclimate indices from daily weather.

- PAR (MJ/m2/d) = par_fraction x all-sky shortwave. ERA5 shortwave is all-sky,
  so clouds are already in it; the clearness index kt = Rs / Ra shows how
  cloudy each day was (Ra = top-of-atmosphere radiation, FAO-56 eq. 21).
- Growing degree days: sum of max(0, min(Tmean, Tcap) - Tbase), Tbase 5 °C by
  default (Finnish "tehoisa lämpösumma").
- Photothermal quotient PTQ = PAR / (Tmean - Tbase) over a critical window,
  related to grain number in cereals.
- Water balance P - ET0, heat days (Tmax > 30 °C), frost days (Tmin < 0 °C).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

GSC = 0.0820  # solar constant, MJ/m2/min


def extraterrestrial_radiation(lat_deg: float, doy) -> np.ndarray:
    """Daily top-of-atmosphere radiation Ra [MJ/m2/d], FAO-56 eq. 21."""
    j = np.asarray(doy, dtype=float)
    phi = np.deg2rad(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi / 365 * j)
    delta = 0.409 * np.sin(2 * np.pi / 365 * j - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(delta), -1.0, 1.0))
    return (
        (24 * 60 / np.pi)
        * GSC
        * dr
        * (ws * np.sin(phi) * np.sin(delta) + np.cos(phi) * np.cos(delta) * np.sin(ws))
    )


def par_from_shortwave(sw_mj, par_fraction: float = 0.48):
    """PAR [MJ/m2/d] from all-sky global shortwave radiation."""
    return np.asarray(sw_mj, dtype=float) * par_fraction


def clearness_index(sw_mj, lat_deg: float, doy):
    ra = extraterrestrial_radiation(lat_deg, doy)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(ra > 0.5, np.asarray(sw_mj, float) / ra, np.nan)


def growing_degree_days(tmean, t_base: float = 5.0, t_cap: float | None = None):
    t = np.asarray(tmean, dtype=float)
    if t_cap is not None:
        t = np.minimum(t, t_cap)
    return np.maximum(t - t_base, 0.0)


@dataclass
class AgroSettings:
    crop: str = "spring_cereal"
    sowing_date: date | None = None
    harvest_date: date | None = None
    t_base_c: float = 5.0
    t_cap_c: float | None = None
    par_fraction: float = 0.48
    harvest_index: float = 0.45  # grain / above-ground dry biomass
    # stage name -> GDD from sowing (°Cd), crop- and variety-specific; set your own
    stages_gdd: dict[str, float] = field(default_factory=dict)
    # PTQ window as (start stage, end stage) names from stages_gdd
    ptq_window: tuple[str, str] | None = None


def daily_indices(weather: pd.DataFrame, lat: float, s: AgroSettings) -> pd.DataFrame:
    """Add par, kt, gdd and cumulative gdd (from sowing) to a daily weather frame."""
    w = weather.copy()
    idx = pd.to_datetime(pd.Index(w.index))
    doy = idx.dayofyear.to_numpy()
    sw = w["shortwave_radiation_sum"].to_numpy(dtype=float)
    if "temperature_2m_mean" in w:
        tmean = w["temperature_2m_mean"].to_numpy(dtype=float)
    else:
        tmean = (w["temperature_2m_max"] + w["temperature_2m_min"]).to_numpy(float) / 2
    w["par_mj_m2"] = par_from_shortwave(sw, s.par_fraction)
    w["ra_mj_m2"] = extraterrestrial_radiation(lat, doy)
    w["clearness_index"] = clearness_index(sw, lat, doy)
    w["gdd"] = growing_degree_days(tmean, s.t_base_c, s.t_cap_c)
    in_season = np.ones(len(w), bool)
    if s.sowing_date:
        in_season &= idx.date >= s.sowing_date
    w["gdd_cum"] = np.cumsum(np.where(in_season, w["gdd"], 0.0))
    return w


def season_summary(weather: pd.DataFrame, lat: float, s: AgroSettings) -> dict:
    """Totals and indicators from sowing to harvest (or the full record)."""
    w = daily_indices(weather, lat, s)
    dates = pd.Index(w.index)
    start = s.sowing_date or dates.min()
    end = s.harvest_date or dates.max()
    sel = (dates >= start) & (dates <= end)
    if not sel.any():
        raise ValueError(f"no weather between {start} and {end}")
    season = w[sel]

    def total(col):
        return float(season[col].sum()) if col in season else None

    kt = season["clearness_index"]
    out = {
        "period": {
            "start": str(start),
            "end": str(end),
            "days": int(sel.sum()),
            "complete": bool(dates.min() <= start and dates.max() >= end),
        },
        "gdd_c_days": float(season["gdd"].sum()),
        "t_base_c": s.t_base_c,
        "par_mj_m2": float(season["par_mj_m2"].sum()),
        "shortwave_mj_m2": total("shortwave_radiation_sum"),
        "mean_clearness_index": float(kt.mean()),
        "cloudy_days": int((kt < 0.3).sum()),  # mostly overcast
        "clear_days": int((kt > 0.6).sum()),
        "precipitation_mm": total("precipitation_sum"),
        "et0_mm": total("et0_fao_evapotranspiration"),
        "heat_days_tmax_gt_30": int((season.get("temperature_2m_max", pd.Series()) > 30).sum()),
        "frost_days_tmin_lt_0": int((season.get("temperature_2m_min", pd.Series()) < 0).sum()),
    }
    if out["precipitation_mm"] is not None and out["et0_mm"] is not None:
        out["water_balance_mm"] = out["precipitation_mm"] - out["et0_mm"]
    if "sunshine_duration" in season:
        out["sunshine_h"] = float(season["sunshine_duration"].sum() / 3600)

    stage_dates = {}
    for name, threshold in s.stages_gdd.items():
        reached = season.index[season["gdd_cum"].to_numpy() >= threshold]
        stage_dates[name] = str(reached[0]) if len(reached) else None
    if stage_dates:
        out["stage_dates"] = stage_dates
    if s.ptq_window and all(stage_dates.get(k) for k in s.ptq_window):
        a, b = (date.fromisoformat(stage_dates[k]) for k in s.ptq_window)
        win = season[(pd.Index(season.index) >= a) & (pd.Index(season.index) <= b)]
        tm = win["gdd"].to_numpy()  # Tmean - Tbase
        with np.errstate(divide="ignore", invalid="ignore"):
            ptq = np.where(tm > 0, win["par_mj_m2"].to_numpy() / tm, np.nan)
        out["ptq_window"] = {
            "from": str(a),
            "to": str(b),
            "ptq_mj_m2_per_c_day": float(np.nanmean(ptq)),
        }
    return out
