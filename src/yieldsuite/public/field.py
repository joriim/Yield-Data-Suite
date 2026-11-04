"""Where the field is, in WGS84, for public-data searches."""

from __future__ import annotations

from ..config import FieldYearConfig
from ..l4.grid import Grid


def field_bbox_wgs84(
    config: FieldYearConfig, grid: Grid | None = None, pad_deg: float = 0.002
) -> tuple[float, float, float, float]:
    """(west, south, east, north) from the boundary, else the L4 grid, else the location."""
    from pyproj import Transformer

    tr = Transformer.from_crs(config.crs, "EPSG:4326", always_xy=True)
    if config.boundary is not None and config.boundary.exists():
        from .boundary import load_boundary

        x0, y0, x1, y1 = load_boundary(config.boundary, config.crs).bounds
    elif grid is not None:
        x0, y1 = grid.xmin, grid.ymax
        x1, y0 = x0 + grid.nx * grid.resolution, y1 - grid.ny * grid.resolution
    elif config.location:
        lat, lon = config.location
        return (lon - 0.01, lat - 0.01, lon + 0.01, lat + 0.01)
    else:
        raise ValueError("field position unknown: add a boundary or location, or run to L4")
    lons, lats = tr.transform([x0, x1, x1, x0], [y0, y0, y1, y1])
    return (min(lons) - pad_deg, min(lats) - pad_deg, max(lons) + pad_deg, max(lats) + pad_deg)
