"""File readers: return a raw table plus what the format itself tells us.

Each reader returns ``(frame, meta)`` where ``meta`` may hold ``crs`` (for
vector geometry), ``mapping`` and ``units`` presets, and ``format``.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

import pandas as pd

from .isoxml import is_isoxml, read_isoxml

TABLE_EXT = {".csv", ".txt", ".tsv", ".dat", ".asc"}
EXCEL_EXT = {".xlsx", ".xlsm", ".xls"}
VECTOR_EXT = {".shp", ".gpkg", ".geojson", ".json", ".fgb", ".kml", ".gml"}

# Ag Leader "advanced" text export: no header, fixed column order. Verify against
# a real file before relying on it; override with ``columns`` if it differs.
AGLEADER_ADVANCED = {
    "columns": [
        "lon",
        "lat",
        "mass_flow",
        "time",
        "duration",
        "distance",
        "swath",
        "moisture",
        "header",
        "pass_id",
        "machine",
        "field_ref",
        "load_ref",
        "crop",
        "gnss_quality",
        "pdop",
        "elevation",
    ],
    "units": {
        "mass_flow": "lb/s",
        "duration": "s",
        "distance": "in",
        "swath": "in",
        "moisture": "%",
        "elevation": "ft",
    },
}


def detect_format(path: Path) -> str:
    path = Path(path)
    if is_isoxml(path):
        return "isoxml"
    ext = path.suffix.lower()
    if ext in TABLE_EXT:
        return "table"
    if ext in EXCEL_EXT:
        return "excel"
    if ext in VECTOR_EXT:
        return "vector"
    if ext == ".zip" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            if any(n.lower().endswith(".shp") for n in z.namelist()):
                return "vector"
    raise ValueError(f"cannot tell the format of {path.name}; set format: in the config")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def read_table(
    path: Path, delimiter: str | None = None, decimal: str | None = None, header: bool | None = None
) -> tuple[pd.DataFrame, dict]:
    text = _decode(Path(path).read_bytes())
    sample = text[:65536]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    if decimal is None:
        # "12,5" between separators means decimal comma (Nordic/German exports)
        decimal = "," if delimiter != "," and re.search(r"\d,\d", sample) else "."
    lines = sample.splitlines()
    first = lines[0].split(delimiter) if lines else []
    if header is None:
        numeric = sum(bool(re.fullmatch(r"\s*-?[\d.,]+\s*", c)) for c in first)
        header = numeric < max(1, len(first) // 2)
    df = pd.read_csv(
        io.StringIO(text),
        sep=delimiter,
        decimal=decimal,
        header=0 if header else None,
        skipinitialspace=True,
    )
    if not header:
        df.columns = [f"col{i}" for i in range(df.shape[1])]
    df.columns = [str(c).strip() for c in df.columns]
    return df, {
        "format": "table",
        "delimiter": delimiter,
        "decimal": decimal,
        "has_header": bool(header),
    }


def read_excel(path: Path, sheet=0) -> tuple[pd.DataFrame, dict]:
    try:
        df = pd.read_excel(path, sheet_name=sheet)
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("Excel files need openpyxl: pip install yieldsuite[ingest]") from exc
    df.columns = [str(c).strip() for c in df.columns]
    return df, {"format": "excel", "sheet": sheet}


def read_vector(path: Path, layer: str | None = None) -> tuple[pd.DataFrame, dict]:
    try:
        import geopandas as gpd
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("vector files need geopandas: pip install yieldsuite[geo]") from exc
    target = f"zip://{path}" if Path(path).suffix.lower() == ".zip" else path
    gdf = gpd.read_file(target, layer=layer) if layer else gpd.read_file(target)
    if gdf.crs is None:
        raise ValueError(f"{Path(path).name} has no CRS; set position_crs in the config")
    geom = gdf.geometry
    if not (geom.geom_type == "Point").all():
        geom = geom.centroid  # polygons/lines from some exports: use the centre
    df = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    df["__x"], df["__y"] = geom.x.to_numpy(), geom.y.to_numpy()
    return df, {"format": "vector", "crs": gdf.crs.to_string(), "mapping": {"x": "__x", "y": "__y"}}


def read_any(path: Path, fmt: str = "auto", **opts) -> tuple[pd.DataFrame, dict]:
    path = Path(path)
    if fmt in ("auto", None):
        fmt = detect_format(path)
    if fmt in ("csv", "table"):
        return read_table(path, opts.get("delimiter"), opts.get("decimal"))
    if fmt == "excel":
        return read_excel(path, opts.get("sheet") or 0)
    if fmt == "vector":
        return read_vector(path, opts.get("layer"))
    if fmt == "isoxml":
        df, units = read_isoxml(path, opts.get("ddi_map"))
        mapping = {c: c for c in df.columns}
        return df, {"format": "isoxml", "mapping": mapping, "units": units, "entry_level": "L1B"}
    if fmt == "agleader_advanced":
        df, meta = read_table(path, header=False)
        cols = AGLEADER_ADVANCED["columns"][: df.shape[1]]
        df.columns = cols + [f"col{i}" for i in range(len(cols), df.shape[1])]
        meta.update(
            format="agleader_advanced",
            mapping={c: c for c in cols},
            units=dict(AGLEADER_ADVANCED["units"]),
            entry_level="L1B",
        )
        return df, meta
    raise ValueError(f"unknown format {fmt!r}")
