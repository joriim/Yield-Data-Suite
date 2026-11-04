"""Vegetation-index features from local reflectance rasters (drone, PlanetScope).

The raster's bands are mapped to Sentinel-2-equivalent names so the same index
formulas apply (green -> B03, red -> B04, red edge -> B05, NIR -> B08/B8A).
Indices are computed per pixel, then averaged onto the suite grid with
area-weighted resampling (rasterio ``Resampling.average``). Cells less than
``min_coverage`` covered by valid pixels are left out.

Band orders and scales are sensor presets that must be checked against the
actual product (``bands:``/``scale:`` in the config override them):

- ``mavic3m``: WebODM multispectral orthophoto, bands G, R, RE, NIR; reflectance 0-1
- ``superdove``: PlanetScope 8-band SR (coastal blue, blue, green I, green, yellow,
  red, red edge, NIR), scaled by 10 000
- ``dove4``: PlanetScope 4-band SR (blue, green, red, NIR), scaled by 10 000
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..l4.grid import Grid
from .indices import get_indices

SENSORS: dict[str, dict] = {
    "mavic3m": {"bands": {"green": 1, "red": 2, "rededge": 3, "nir": 4}, "scale": None},
    "superdove": {
        "bands": {"blue": 2, "green": 4, "red": 6, "rededge": 7, "nir": 8},
        "scale": 1e-4,
    },
    "dove4": {"bands": {"blue": 1, "green": 2, "red": 3, "nir": 4}, "scale": 1e-4},
}
# sensor band -> Sentinel-2-equivalent names used by the index formulas
S2_NAMES = {
    "blue": ("B02",),
    "green": ("B03",),
    "red": ("B04",),
    "rededge": ("B05",),
    "nir": ("B08", "B8A"),
}
DEFAULT_INDICES = ("NDVI", "NDRE", "GNDVI")


def _rasterio():
    try:
        import rasterio
        from rasterio.warp import Resampling, reproject
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "raster imagery needs the geo extra: pip install yieldsuite[geo]"
        ) from exc
    return rasterio, reproject, Resampling


def raster_features(
    path: str | Path,
    grid: Grid,
    crs: str,
    date: str,
    source: str,
    sensor: str = "mavic3m",
    bands: dict[str, int] | None = None,
    scale: float | None = None,
    indices=DEFAULT_INDICES,
    mask_path: str | Path | None = None,
    mask_clear_band: int = 1,
    min_coverage: float = 0.5,
) -> tuple[pd.DataFrame, dict]:
    """Long feature rows (cell, date, index, value, source) and a report."""
    rasterio, reproject, Resampling = _rasterio()
    preset = SENSORS.get(sensor, {"bands": {}, "scale": None})
    band_map = dict(bands or preset["bands"])
    if not band_map:
        raise ValueError(f"unknown sensor {sensor!r}; give bands: {{green: 1, red: 2, ...}}")
    report: dict = {
        "path": str(path),
        "source": source,
        "sensor": sensor,
        "date": date,
        "bands": band_map,
        "warnings": [],
    }

    with rasterio.open(path) as src:
        missing = [b for b, i in band_map.items() if i > src.count]
        if missing:
            raise ValueError(f"{path}: {src.count} bands, but the map needs {missing}")
        data = {
            name: src.read(i, masked=True).astype("float64").filled(np.nan)
            for name, i in band_map.items()
        }
        valid = np.ones(src.shape, bool)
        for arr in data.values():
            valid &= np.isfinite(arr) & (arr > 0)
        if src.count >= max(band_map.values()) + 1 and sensor == "mavic3m":
            alpha = src.read(src.count)
            if alpha.max() > 0 and np.unique(alpha).size <= 2:
                valid &= alpha > 0  # WebODM alpha band
        src_transform, src_crs = src.transform, src.crs
        report["pixel_size_m"] = float(abs(src_transform.a))

    if mask_path:
        with rasterio.open(mask_path) as m:
            clear = m.read(mask_clear_band)
        if clear.shape != valid.shape:
            raise ValueError("cloud mask and image have different sizes")
        valid &= clear > 0
        report["cloud_mask"] = str(mask_path)

    if scale is None:
        scale = preset["scale"]
        if scale is None:
            med = (
                float(np.nanmedian(np.concatenate([a[valid] for a in data.values()])))
                if valid.any()
                else 0.0
            )
            scale = 1.0 if med <= 1.5 else (1e-4 if med <= 15000 else 1 / 65535)
            if scale != 1.0:
                report["warnings"].append(f"reflectance scale guessed as {scale:g} from values")
    report["scale"] = scale
    refl = {k: np.where(valid, v * scale, np.nan) for k, v in data.items()}

    def get(name):
        for sensor_band, names in S2_NAMES.items():
            if name in names and sensor_band in refl:
                return refl[sensor_band]
        raise KeyError(name)

    usable = []
    for idx in get_indices(indices):
        try:
            for b in idx.bands:
                get(b)
        except KeyError:
            report["warnings"].append(f"{idx.name}: sensor lacks band(s) {idx.bands}")
            continue
        usable.append(idx)
    if any(i.name in ("NDRE", "CIre") for i in usable) and sensor == "mavic3m":
        report["warnings"].append(
            "Mavic 3M red edge is 730 nm (S2 B05 is 705 nm): red-edge "
            "indices rely on harmonisation to Sentinel-2"
        )

    from rasterio.transform import Affine

    dst_transform = Affine(*grid.transform)
    cov = np.zeros((grid.ny, grid.nx))
    reproject(
        valid.astype("float32"),
        cov,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=crs,
        resampling=Resampling.average,
    )
    ok = cov.ravel() >= min_coverage
    rows = []
    for idx in usable:
        with np.errstate(divide="ignore", invalid="ignore"):
            pix = np.asarray(idx.formula(get), dtype="float32")
        out = np.full((grid.ny, grid.nx), np.nan, dtype="float32")
        reproject(
            pix,
            out,
            src_transform=src_transform,
            src_crs=src_crs,
            src_nodata=np.nan,
            dst_transform=dst_transform,
            dst_crs=crs,
            dst_nodata=np.nan,
            resampling=Resampling.average,
        )
        flat = out.ravel()
        cells = np.flatnonzero(ok & np.isfinite(flat))
        rows.append(
            pd.DataFrame(
                {
                    "cell": cells,
                    "date": date,
                    "index": idx.name,
                    "value": flat[cells].astype(float),
                    "source": source,
                }
            )
        )
    report["cells"] = int(ok.sum())
    report["indices"] = [i.name for i in usable]
    df = (
        pd.concat(rows, ignore_index=True)
        if rows
        else pd.DataFrame(columns=["cell", "date", "index", "value", "source"])
    )
    return df, report
