"""Sentinel-2 L2A features per grid cell via openEO (Copernicus Data Space).

Only the field's grid cells (as polygons) are sent to the backend, never yield
data. The backend loads Sentinel-2 L2A, masks clouds with the scene
classification layer (SCL), computes indices per pixel, and averages them per
grid cell and date. What comes back is a small table:

    cell, date, index, value

Needs ``yieldsuite[geo,fusion]`` (pyproj and the openeo client) and a free
Copernicus Data Space account for ``authenticate_oidc``.

Alignment note: with the UTM zone of the field's Sentinel-2 tile (Finland spans
EPSG:32634-32636; e.g. tile 34VEQ near Seinajoki is zone 34), 20 m cells and
origin (0, 0), grid cells coincide with Sentinel-2 20 m pixels. With EPSG:3067
the cells are averaged from the overlapping pixels instead, which smooths slightly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..l4.grid import Grid
from .indices import DEFAULT_INDICES, get_indices, required_bands

DEFAULT_BACKEND = "openeo.dataspace.copernicus.eu"
COLLECTION = "SENTINEL2_L2A"
# SCL classes treated as invalid: no data, saturated, cloud shadow,
# cloud medium / high probability, thin cirrus, snow.
SCL_INVALID = (0, 1, 3, 8, 9, 10, 11)
LONG_COLUMNS = ["cell", "date", "index", "value"]


@dataclass
class S2Request:
    start: str  # ISO date, inclusive
    end: str  # ISO date, exclusive (openEO convention)
    indices: tuple[str, ...] = DEFAULT_INDICES
    max_cloud_cover: float = 80.0  # scene-level pre-filter; pixels are masked with SCL
    collection: str = COLLECTION
    backend: str = DEFAULT_BACKEND
    extra: dict = field(default_factory=dict)


def _transformer(crs: str):
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Sentinel-2 retrieval needs pyproj: pip install yieldsuite[geo]") from exc
    return Transformer.from_crs(crs, "EPSG:4326", always_xy=True)


def cell_ids(grid: Grid, cell_mask=None) -> np.ndarray:
    """Flat cell ids (row * nx + col) of the cells to aggregate."""
    ids = np.arange(grid.ny * grid.nx)
    if cell_mask is not None:
        ids = ids[np.asarray(cell_mask, dtype=bool).ravel()]
    return ids


def grid_cells_geojson(grid: Grid, crs: str, cell_mask=None) -> dict:
    """Grid cells as a GeoJSON FeatureCollection in EPSG:4326, property ``cell`` = flat id."""
    tr = _transformer(crs)
    ids = cell_ids(grid, cell_mask)
    rows, cols = np.divmod(ids, grid.nx)
    r = grid.resolution
    x0 = grid.xmin + cols * r
    y1 = grid.ymax - rows * r
    xs = np.stack([x0, x0 + r, x0 + r, x0, x0], axis=1)
    ys = np.stack([y1 - r, y1 - r, y1, y1, y1 - r], axis=1)
    lon, lat = tr.transform(xs, ys)
    features = [
        {
            "type": "Feature",
            "properties": {"cell": int(cid)},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[float(a), float(b)] for a, b in zip(lo, la, strict=True)]],
            },
        }
        for cid, lo, la in zip(ids, lon, lat, strict=True)
    ]
    return {"type": "FeatureCollection", "features": features}


def bbox_wgs84(grid: Grid, crs: str, buffer_m: float = 60.0) -> dict:
    tr = _transformer(crs)
    xmin = grid.xmin - buffer_m
    xmax = grid.xmin + grid.nx * grid.resolution + buffer_m
    ymin = grid.ymax - grid.ny * grid.resolution - buffer_m
    ymax = grid.ymax + buffer_m
    lon, lat = tr.transform([xmin, xmax, xmax, xmin], [ymin, ymin, ymax, ymax])
    return {"west": min(lon), "south": min(lat), "east": max(lon), "north": max(lat)}


def build_process_graph(connection, cells_fc: dict, extent: dict, request: S2Request):
    """openEO graph: load -> SCL mask -> indices per pixel -> mean per cell and date.

    ``connection`` may be None to build the graph offline (tests, inspection).
    """
    try:
        from openeo import DataCube
        from openeo.processes import array_create
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError(
            "Sentinel-2 retrieval needs openeo: pip install yieldsuite[fusion]"
        ) from exc

    indices = get_indices(request.indices)
    bands = required_bands(request.indices)
    cube = DataCube.load_collection(
        request.collection,
        connection=connection,
        spatial_extent=extent,
        temporal_extent=[request.start, request.end],
        bands=[*bands, "SCL"],
        max_cloud_cover=request.max_cloud_cover,
        fetch_metadata=connection is not None,
    )
    scl = cube.band("SCL")
    invalid = None
    for cls in SCL_INVALID:
        term = scl == cls
        invalid = term if invalid is None else invalid | term
    reflectance = cube.filter_bands(bands).mask(invalid)

    def compute(x):
        def get(name):
            return x.array_element(label=name)

        return array_create([idx.formula(get) for idx in indices])

    index_cube = reflectance.apply_dimension(dimension="bands", process=compute).rename_labels(
        "bands", target=[idx.name for idx in indices]
    )
    return index_cube.aggregate_spatial(geometries=cells_fc, reducer="mean")


def parse_timeseries_json(ts: dict, ids, index_names) -> pd.DataFrame:
    """Parse the openEO aggregate_spatial timeseries JSON.

    Shape: ``{date: [[v_index0, v_index1, ...] per geometry, in request order]}``;
    a geometry with no valid pixels is ``[]`` or holds nulls.
    """
    ids = np.asarray(ids)
    names = list(index_names)
    records = []
    for date, per_geom in ts.items():
        day = str(date)[:10]
        if len(per_geom) != len(ids):
            raise ValueError(f"{date}: {len(per_geom)} geometries returned, expected {len(ids)}")
        for cid, values in zip(ids, per_geom, strict=True):
            if not values:
                continue
            for name, v in zip(names, values, strict=False):
                if v is not None:
                    records.append((int(cid), day, name, float(v)))
    out = pd.DataFrame.from_records(records, columns=LONG_COLUMNS)
    return out.sort_values(["date", "cell", "index"], ignore_index=True)


def connect(backend: str = DEFAULT_BACKEND):  # pragma: no cover - needs network and account
    import openeo

    return openeo.connect(backend).authenticate_oidc()


def fetch_features(
    grid: Grid,
    crs: str,
    request: S2Request,
    connection=None,
    cell_mask=None,
    workdir: str | Path = ".",
    batch: bool = False,
) -> pd.DataFrame:  # pragma: no cover - exercised against a live backend only
    """Run the graph on the backend and return the long feature table."""
    connection = connection or connect(request.backend)
    ids = cell_ids(grid, cell_mask)
    graph = build_process_graph(
        connection, grid_cells_geojson(grid, crs, cell_mask), bbox_wgs84(grid, crs), request
    )
    out = Path(workdir) / "s2_timeseries.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if batch:
        graph.execute_batch(outputfile=out, out_format="JSON", title="yieldsuite S2 features")
    else:
        graph.download(out, format="JSON")
    return parse_timeseries_json(json.loads(out.read_text()), ids, request.indices)


def save_features(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = LONG_COLUMNS + (["source"] if "source" in df.columns else [])
    df[cols].to_csv(path, index=False)
    return path


def load_features(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"cell": int, "date": str, "index": str, "value": float})
    missing = set(LONG_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    if "source" not in df.columns:
        df["source"] = "sentinel2"
    return df[LONG_COLUMNS + ["source"]]
