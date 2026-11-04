"""OGC WFS 2.0 GetFeature as GeoJSON, for public vector data (e.g. field parcels)."""

from __future__ import annotations

import urllib.parse

from . import http


def get_features(
    url: str,
    typename: str,
    bbox: tuple[float, float, float, float],
    bbox_crs: str = "EPSG:4326",
    count: int = 500,
    get=None,
) -> dict:
    """GeoJSON FeatureCollection of ``typename`` intersecting ``bbox``.

    ``bbox`` is (minx, miny, maxx, maxy) in ``bbox_crs`` axis order x/y; the
    request asks for the output in EPSG:4326 lon/lat.
    """
    get = get or http.get_json
    srs = bbox_crs.replace("EPSG:", "urn:ogc:def:crs:EPSG::")
    if bbox_crs.upper() == "EPSG:4326":  # urn form uses lat/lon axis order
        b = (bbox[1], bbox[0], bbox[3], bbox[2])
    else:
        b = bbox
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": typename,
        "count": count,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        "bbox": ",".join(str(v) for v in b) + f",{srs}",
    }
    sep = "&" if "?" in url else "?"
    fc = get(url + sep + urllib.parse.urlencode(params))
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"WFS did not return GeoJSON (got {str(fc)[:120]})")
    return fc
