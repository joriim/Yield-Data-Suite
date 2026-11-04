"""Position of the cut crop and record polygons.

Coordinates are in a metric CRS (e.g. EPSG:3067). Heading is in radians,
clockwise from grid north, so the forward unit vector is (sin h, cos h).
"""

from __future__ import annotations

import numpy as np
import shapely
from shapely import STRtree


def _unit_vectors(heading):
    h = np.asarray(heading, dtype=float)
    fwd = np.stack([np.sin(h), np.cos(h)], axis=-1)
    right = np.stack([np.cos(h), -np.sin(h)], axis=-1)
    return fwd, right


def header_position(x, y, heading, forward_m: float, right_m: float):
    """Header centre from antenna position and the (forward, right) offset."""
    fwd, right = _unit_vectors(heading)
    px = np.asarray(x, dtype=float) + forward_m * fwd[..., 0] + right_m * right[..., 0]
    py = np.asarray(y, dtype=float) + forward_m * fwd[..., 1] + right_m * right[..., 1]
    return px, py


def delayed_positions(t_s, x, y, tau_d_s: float):
    """Where the grain sensed at time t was cut: antenna position at t - tau_d.

    Samples whose t - tau_d falls before the first fix return NaN.
    """
    t = np.asarray(t_s, dtype=float)
    tq = t - tau_d_s
    px = np.interp(tq, t, np.asarray(x, dtype=float), left=np.nan)
    py = np.interp(tq, t, np.asarray(y, dtype=float), left=np.nan)
    return px, py


def record_polygons(x, y, heading, length_m, width_m):
    """Rectangle harvested by each record: ends at (x, y), extends back by length.

    Returns a numpy array of shapely Polygons.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    length = np.broadcast_to(np.asarray(length_m, dtype=float), x.shape)
    half_w = np.broadcast_to(np.asarray(width_m, dtype=float), x.shape) / 2.0
    fwd, right = _unit_vectors(heading)
    front = np.stack([x, y], axis=-1)
    back = front - fwd * length[:, None]
    r = right * half_w[:, None]
    coords = np.stack([back - r, back + r, front + r, front - r, back - r], axis=1)
    return shapely.polygons(coords)


def effective_polygons(polygons) -> np.ndarray:
    """Each polygon minus the area already covered by earlier (time-ordered) polygons.

    Apportioning mass with these non-overlapping polygons conserves the field
    total at L4.
    """
    polys = np.asarray(polygons, dtype=object)
    tree = STRtree(polys)
    out = np.empty(len(polys), dtype=object)
    for k, poly in enumerate(polys):
        if poly.is_empty or poly.area == 0:
            out[k] = poly
            continue
        hits = tree.query(poly, predicate="intersects")
        earlier = hits[hits < k]
        earlier = earlier[shapely.area(polys[earlier]) > 0] if earlier.size else earlier
        if earlier.size == 0:
            out[k] = poly
        else:
            out[k] = shapely.difference(poly, shapely.union_all(polys[earlier]))
    return out


def effective_area(polygons) -> np.ndarray:
    """Area of each polygon not already covered by earlier (time-ordered) polygons."""
    return shapely.area(effective_polygons(polygons))
