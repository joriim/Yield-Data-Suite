"""Sentinel-2 vegetation indices.

Each formula only uses arithmetic, so the same definition runs on numpy arrays
(tests, drone orthomosaics) and on openEO process builders (server side, per
pixel, before aggregation to grid cells).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Index:
    name: str
    bands: tuple[str, ...]
    formula: Callable  # formula(get) where get(band_name) -> array-like
    description: str


def _nd(a: str, b: str):
    return lambda get: (get(a) - get(b)) / (get(a) + get(b))


INDICES: dict[str, Index] = {
    i.name: i
    for i in (
        Index("NDVI", ("B04", "B08"), _nd("B08", "B04"), "normalised difference vegetation index"),
        Index("NDRE", ("B05", "B8A"), _nd("B8A", "B05"), "red-edge NDVI (20 m)"),
        Index(
            "CIre", ("B05", "B07"), lambda g: g("B07") / g("B05") - 1, "red-edge chlorophyll index"
        ),
        Index("NDMI", ("B8A", "B11"), _nd("B8A", "B11"), "moisture index (NIR/SWIR)"),
        Index("GNDVI", ("B03", "B08"), _nd("B08", "B03"), "green NDVI"),
    )
}

DEFAULT_INDICES: tuple[str, ...] = ("NDVI", "NDRE", "CIre", "NDMI")


def get_indices(names) -> list[Index]:
    unknown = [n for n in names if n not in INDICES]
    if unknown:
        raise ValueError(f"unknown indices {unknown}; available: {sorted(INDICES)}")
    return [INDICES[n] for n in names]


def required_bands(names) -> list[str]:
    bands = {b for idx in get_indices(names) for b in idx.bands}
    return sorted(bands)
