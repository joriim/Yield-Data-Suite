"""Public data searches: Sentinel-2 scenes (STAC API), field parcels (OGC WFS).

Endpoints are configuration, not code: public services move and rename more
often than this suite is released. Defaults point to the Copernicus Data
Space STAC API; the parcel WFS must be configured (e.g. the Finnish Food
Authority's open parcel data), see docs/public-data.md.
"""

from .boundary import edge_mask, load_boundary, select_parcels
from .parcels import ParcelService, normalise_parcel_id
from .stac import search_sentinel2
from .wfs import get_features

__all__ = [
    "ParcelService",
    "edge_mask",
    "get_features",
    "load_boundary",
    "normalise_parcel_id",
    "search_sentinel2",
    "select_parcels",
]
