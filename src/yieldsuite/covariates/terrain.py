"""Terrain covariates from a digital elevation model (e.g. the NLS 2 m DEM).

Bands on the suite grid:

- ``elevation_m``: area-weighted mean elevation
- ``slope_deg``: slope computed at the DEM's own resolution, then averaged
- ``tpi_m``: topographic position, elevation minus the mean within ``tpi_radius_m``
  (negative = hollow, positive = knoll)
- ``twi``: topographic wetness index ln(a / tan beta) with D8 flow accumulation;
  computed on a grid buffered by ``buffer_m`` so water arriving from outside the
  field counts (the DEM must cover the buffer, otherwise edge cells are low)

The DEM must be in a projected CRS with metre units.
"""

from __future__ import annotations

import math

import numpy as np

from ..l4.grid import Grid

TERRAIN_BANDS = ("elevation_m", "slope_deg", "tpi_m", "twi")


def _box_mean(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, ignoring NaN (cumulative sums)."""
    valid = np.isfinite(a)
    z = np.where(valid, a, 0.0)
    pad = ((r + 1, r), (r + 1, r))
    cs = np.pad(z, pad).cumsum(0).cumsum(1)
    cn = np.pad(valid.astype(float), pad).cumsum(0).cumsum(1)
    k = 2 * r + 1

    def win(c):
        return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]

    with np.errstate(invalid="ignore", divide="ignore"):
        return win(cs) / win(cn)


def d8_accumulation(elev: np.ndarray, cell: float) -> np.ndarray:
    """Upslope area [m2] per cell with D8 (steepest descent) routing."""
    ny, nx = elev.shape
    acc = np.where(np.isfinite(elev), cell * cell, 0.0)
    order = np.argsort(np.where(np.isfinite(elev), elev, -np.inf), axis=None)[::-1]
    offs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    dist = [math.hypot(dr, dc) * cell for dr, dc in offs]
    for flat in order:
        r, c = divmod(int(flat), nx)
        h = elev[r, c]
        if not np.isfinite(h):
            continue
        best, target = 0.0, None
        for (dr, dc), d in zip(offs, dist, strict=True):
            rr, cc = r + dr, c + dc
            if 0 <= rr < ny and 0 <= cc < nx and np.isfinite(elev[rr, cc]):
                drop = (h - elev[rr, cc]) / d
                if drop > best:
                    best, target = drop, (rr, cc)
        if target is not None:
            acc[target] += acc[r, c]
    return acc


def terrain_covariates(
    dem_path, grid: Grid, crs: str, buffer_m: float = 200.0, tpi_radius_m: float = 100.0
) -> tuple[dict, dict]:
    """Terrain bands (ny, nx) on ``grid`` and a report."""
    try:
        import rasterio
        from rasterio.transform import Affine
        from rasterio.warp import Resampling, reproject
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("terrain needs the geo extra: pip install yieldsuite[geo]") from exc

    r = grid.resolution
    nb = int(math.ceil(buffer_m / r))
    big = Grid(grid.xmin - nb * r, grid.ymax + nb * r, r, grid.nx + 2 * nb, grid.ny + 2 * nb)

    with rasterio.open(dem_path) as src:
        if src.crs is None or src.crs.is_geographic:
            raise ValueError("the DEM must be in a projected CRS with metre units")
        dem = src.read(1, masked=True).astype("float64").filled(np.nan)
        px, py = abs(src.transform.a), abs(src.transform.e)
        src_t, src_crs = src.transform, src.crs
    gy, gx = np.gradient(dem, py, px)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))

    def to_grid(arr, g):
        out = np.full((g.ny, g.nx), np.nan)
        reproject(
            arr.astype("float64"),
            out,
            src_transform=src_t,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=Affine(*g.transform),
            dst_crs=crs,
            dst_nodata=np.nan,
            resampling=Resampling.average,
        )
        return out

    elev_big = to_grid(dem, big)
    crop = (slice(nb, nb + grid.ny), slice(nb, nb + grid.nx))
    elevation = elev_big[crop]
    slope_grid = to_grid(slope, grid)
    tpi = (elev_big - _box_mean(elev_big, max(1, int(round(tpi_radius_m / r)))))[crop]

    acc = d8_accumulation(elev_big, r)
    gyb, gxb = np.gradient(elev_big, r, r)
    tanb = np.maximum(np.hypot(gxb, gyb), 1e-3)
    with np.errstate(invalid="ignore", divide="ignore"):
        twi = np.log((acc / r) / tanb)
    twi = np.where(np.isfinite(elev_big), twi, np.nan)[crop]

    covered = float(np.isfinite(elev_big).mean())
    report = {
        "dem": str(dem_path),
        "dem_pixel_m": [px, py],
        "buffer_m": nb * r,
        "buffer_covered_share": covered,
        "elevation_range_m": [float(np.nanmin(elevation)), float(np.nanmax(elevation))],
        "warnings": [],
    }
    if covered < 0.95:
        report["warnings"].append(
            f"DEM covers {covered:.0%} of the buffered area: TWI near the edges misses "
            f"upslope area; use a DEM extending ~{nb * r:.0f} m beyond the field"
        )
    bands = {"elevation_m": elevation, "slope_deg": slope_grid, "tpi_m": tpi, "twi": twi}
    return bands, report
