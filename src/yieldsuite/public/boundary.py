"""Field boundary: load, choose parcels, and the edge mask for the FIELD_EDGE flag."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely


def _gpd():
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("field boundaries need geopandas: pip install yieldsuite[geo]") from exc
    return gpd


def load_boundary(path: str | Path, crs: str):
    """Union of all polygons in a vector file, in the field CRS (shapely geometry)."""
    gdf = _gpd().read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path}: boundary has no CRS")
    geom = shapely.union_all(gdf.to_crs(crs).geometry.to_numpy())
    if geom.is_empty or geom.area <= 0:
        raise ValueError(f"{path}: boundary has no area")
    return geom


def select_parcels(gdf, crs: str, parcel_ids=None, id_column: str | None = None, near=None):
    """Pick parcels by id, or those overlapping ``near`` (geometry in ``crs``).

    Returns a GeoDataFrame in ``crs``.
    """
    g = gdf.to_crs(crs)
    if parcel_ids:
        if not id_column or id_column not in g.columns:
            raise ValueError(f"id_column {id_column!r} not in {list(g.columns)}")
        from .parcels import _safe_id

        wanted = {_safe_id(p) or str(p) for p in parcel_ids}
        keys = g[id_column].map(lambda v: _safe_id(v) or str(v))
        sel = g[keys.isin(wanted)]
        missing = wanted - set(keys[keys.isin(wanted)])
        if missing:
            raise ValueError(f"parcels not found: {sorted(missing)}")
        return sel
    if near is not None:
        inter = g.geometry.intersection(near).area
        sel = g[inter > 0.2 * np.minimum(g.geometry.area, near.area)]
        if sel.empty:
            raise ValueError("no parcel overlaps the harvested area")
        return sel
    if len(g) == 1:
        return g
    raise ValueError(f"{len(g)} parcels: give parcel ids or harvest data to choose from")


def edge_mask(boundary, x, y, buffer_m: float) -> np.ndarray:
    """True for points outside the boundary shrunk by ``buffer_m``."""
    inner = boundary.buffer(-buffer_m) if buffer_m > 0 else boundary
    if inner.is_empty:
        return np.ones(np.shape(x), bool)
    return ~shapely.contains_xy(inner, np.asarray(x, float), np.asarray(y, float))
