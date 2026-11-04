"""Radiation use efficiency (RUE) from yield, Sentinel-2 fAPAR and ERA5 PAR.

    IPAR = sum_d PAR_d x fAPAR_d                     [MJ/m2]
    biomass = yield x (1 - MC_ref/100) / HI          [g dry matter/m2]
    RUE = biomass / IPAR                             [g/MJ]

fAPAR is estimated from NDVI with the linear relation of Myneni & Williams
(1994), fAPAR = 1.1638 NDVI - 0.1426, clipped to [0, 0.95]. Between
Sentinel-2 dates fAPAR is interpolated linearly; it rises from 0 at sowing to
the first observation and falls to 0 at harvest after the last one.
The harvest index is an assumption (default 0.45) and scales RUE directly,
so compare RUE maps within a crop and year, not across crops.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def fapar_from_ndvi(ndvi):
    return np.clip(1.1638 * np.asarray(ndvi, dtype=float) - 0.1426, 0.0, 0.95)


def _daily_fapar(
    obs_dates: list[date], fapar: np.ndarray, days: pd.DatetimeIndex, sowing: date, harvest: date
) -> np.ndarray:
    """fapar: (n_obs, n_cells) -> (n_days, n_cells)."""
    # Day numbers from sowing: independent of pandas' datetime resolution.
    xs = np.asarray(
        [0.0, *[(d - sowing).days for d in obs_dates], (harvest - sowing).days], dtype=float
    )
    zero = np.zeros((1, fapar.shape[1]))
    ys = np.vstack([zero, fapar, zero])
    t = np.asarray([(d.date() - sowing).days for d in days], dtype=float)
    out = np.empty((len(days), fapar.shape[1]))
    for c in range(fapar.shape[1]):
        col = ys[:, c]
        ok = np.isfinite(col)
        out[:, c] = np.interp(t, xs[ok], col[ok]) if ok.sum() >= 2 else np.nan
    return out


def ipar(par_daily: pd.Series, ndvi: pd.DataFrame, sowing: date, harvest: date) -> np.ndarray:
    """Intercepted PAR per cell. ``ndvi``: rows = observation dates, columns = cells."""
    days = pd.date_range(sowing, harvest, freq="D")
    par = pd.Series(par_daily.values, index=pd.to_datetime(pd.Index(par_daily.index)))
    par = par.reindex(days)
    if par.isna().any():
        raise ValueError(f"PAR missing for {int(par.isna().sum())} days between sowing and harvest")
    obs = sorted(d for d in pd.to_datetime(ndvi.index).date if sowing < d < harvest)
    nd = ndvi.copy()
    nd.index = pd.to_datetime(nd.index).date
    f = fapar_from_ndvi(nd.loc[obs].to_numpy(dtype=float))
    daily = _daily_fapar(obs, f, days, sowing, harvest)
    return (par.to_numpy()[:, None] * daily).sum(axis=0)


def biomass_g_m2(yield_t_ha, reference_moisture_pct: float, harvest_index: float):
    dry = np.asarray(yield_t_ha, float) * (1 - reference_moisture_pct / 100.0)
    return dry * 100.0 / harvest_index  # 1 t/ha = 100 g/m2


def rue_map(
    yield_t_ha,
    ndvi: pd.DataFrame,
    par_daily: pd.Series,
    sowing: date,
    harvest: date,
    reference_moisture_pct: float,
    harvest_index: float,
) -> dict:
    """Per-cell IPAR and RUE. ``yield_t_ha`` is flat, aligned with ``ndvi`` columns."""
    ip = ipar(par_daily, ndvi, sowing, harvest)
    bm = biomass_g_m2(yield_t_ha, reference_moisture_pct, harvest_index)
    with np.errstate(divide="ignore", invalid="ignore"):
        rue = np.where(ip > 1, bm / ip, np.nan)
    return {"ipar_mj_m2": ip, "rue_g_mj": rue, "biomass_g_m2": bm}


def field_rue(
    mean_yield_t_ha: float,
    field_ndvi: pd.Series,
    par_daily: pd.Series,
    sowing: date,
    harvest: date,
    reference_moisture_pct: float,
    harvest_index: float,
) -> dict:
    res = rue_map(
        np.array([mean_yield_t_ha]),
        field_ndvi.to_frame("field"),
        par_daily,
        sowing,
        harvest,
        reference_moisture_pct,
        harvest_index,
    )
    return {k: float(v[0]) for k, v in res.items()}
