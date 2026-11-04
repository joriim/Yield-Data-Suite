"""Read and write multi-band Cloud-Optimised GeoTIFFs (needs ``yieldsuite[geo]``)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ..schema import L4_BANDS
from .grid import Grid


def _rasterio():
    try:
        import rasterio
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("GeoTIFF I/O needs the geo extra: pip install yieldsuite[geo]") from exc
    return rasterio


def write_cog(
    bands: dict, grid: Grid, crs: str, path: str | Path, names: Sequence[str] = L4_BANDS
) -> Path:
    rasterio = _rasterio()
    from rasterio.transform import Affine

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stack = np.stack([np.asarray(bands[name], dtype="float32") for name in names])
    profile = {
        "driver": "COG",
        "dtype": "float32",
        "count": len(names),
        "width": grid.nx,
        "height": grid.ny,
        "crs": crs,
        "transform": Affine(*grid.transform),
        "nodata": np.nan,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(stack)
        for i, name in enumerate(names, start=1):
            dst.set_band_description(i, name)
    return path


def read_bands(path: str | Path, require_yield: bool = True) -> tuple[dict, Grid, str]:
    """Read a yield raster. Bands are named by their descriptions.

    A single-band raster without descriptions is read as ``yield``; when there
    is no ``coverage`` band, every cell with a finite yield counts as covered.
    """
    rasterio = _rasterio()
    with rasterio.open(path) as src:
        t = src.transform
        if t.b != 0 or t.d != 0 or abs(t.a + t.e) > 1e-9 * abs(t.a):
            raise ValueError(f"{path}: needs a north-up raster with square cells")
        grid = Grid(xmin=t.c, ymax=t.f, resolution=t.a, nx=src.width, ny=src.height)
        names = [d or (f"band{i}" if i > 1 else "yield") for i, d in enumerate(src.descriptions, 1)]
        data = src.read(masked=True).astype("float64").filled(np.nan)
        crs = src.crs.to_string() if src.crs else ""
    bands = dict(zip(names, data, strict=True))
    if "yield" not in bands:
        if require_yield:
            raise ValueError(f"{path}: no band described as 'yield' (found {names})")
        return bands, grid, crs
    bands.setdefault("coverage", np.isfinite(bands["yield"]).astype(float))
    return bands, grid, crs
