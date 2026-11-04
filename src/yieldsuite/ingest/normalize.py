"""Turn any recognised yield/machine export into the suite's record schema.

Two input families are handled:

* **mass-flow logs** (ISOXML, Ag Leader advanced, most raw CSV logs): mass per
  time, entry level L1B (delay not yet applied);
* **yield-point exports** (Operations Center, FieldView, SMS and similar
  shapefile/CSV exports): yield per area with distance and swath, usually
  already delay-corrected by the vendor, entry level L2. Mass is rebuilt as
  yield x distance x swath so the chain can reconcile it to scale tickets.

Every decision (column match, unit and where it came from, derived values)
goes into a report so a person can check it before trusting the data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..timeutil import epoch_seconds
from .mapping import match_columns
from .units import FACTORS, QUANTITY, bushel_weight, convert, unit_from_header, unit_from_values

OUTPUT_COLUMNS = [
    "t",
    "machine_id",
    "x",
    "y",
    "mass_flow_wet",
    "moisture",
    "swath",
    "speed",
    "heading",
    "header_down",
    "gnss_quality",
    "entry_level",
]
_TRUE = {
    "1",
    "true",
    "yes",
    "on",
    "down",
    "harvesting",
    "cutting",
    "kylla",
    "kyllä",
    "ja",
    "work",
    "working",
    "active",
    "logging",
    "recording",
}
_GNSS_TEXT = {
    "rtkfixed": 4,
    "rtkfix": 4,
    "rtk": 4,
    "rtkfloat": 5,
    "float": 5,
    "dgps": 2,
    "dgnss": 2,
    "sbas": 2,
    "waas": 2,
    "egnos": 2,
    "gps": 1,
    "gnss": 1,
    "autonomous": 1,
    "nofix": 0,
    "invalid": 0,
}


@dataclass
class IngestReport:
    source: str
    format: str
    rows_in: int = 0
    rows_out: int = 0
    mapping: dict[str, str] = field(default_factory=dict)
    units: dict[str, dict] = field(default_factory=dict)
    entry_level: str = ""
    input_family: str = ""
    derived: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmapped_columns: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class IngestOptions:
    machine_id: str = "machine-1"
    columns: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    entry_level: str | None = None
    position_crs: str | None = None
    crop: str | None = None
    bushel_weight_lb: float | None = None
    vendor_reference_moisture_pct: float | None = None
    default_swath_m: float | None = None


def _project(a, b, src: str, dst: str):
    try:
        from pyproj import Transformer
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("projecting positions needs pyproj: pip install yieldsuite[geo]") from exc
    tr = Transformer.from_crs(src, dst, always_xy=True)
    return tr.transform(np.asarray(a, float), np.asarray(b, float))


def _num(series: pd.Series) -> np.ndarray:
    if pd.api.types.is_numeric_dtype(series):
        return series.to_numpy(dtype=float)
    s = series.astype(str).str.strip().str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)


def _unit_from_reference(values, canonical: str, reference: float | None):
    """Pick the unit whose conversion makes the values match a reference in SI units
    (e.g. distance vs. the step between successive positions)."""
    if reference is None or not np.isfinite(reference) or reference <= 0:
        return None
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0:
        return None
    ratio = float(np.median(v)) / reference  # raw units per SI unit
    best, err = None, np.inf
    for unit, factor in FACTORS[QUANTITY[canonical]].items():
        e = abs(np.log(ratio * factor))
        if e < err:
            best, err = unit, e
    return best if err < np.log(1.2) else None


def _unit(
    canonical: str,
    header: str,
    values,
    opts: IngestOptions,
    preset: dict,
    report: IngestReport,
    default: str | None = None,
    reference: float | None = None,
) -> str | None:
    if canonical in opts.units:
        unit, source = opts.units[canonical], "config"
    elif canonical in preset:
        unit, source = preset[canonical], "format"
    elif (u := unit_from_header(header, canonical)) is not None:
        unit, source = u, "header"
    elif (u := _unit_from_reference(values, canonical, reference)) is not None:
        unit, source = u, "consistency with GNSS positions"
    else:
        u, why = unit_from_values(values, canonical)
        if u is not None:
            unit, source = u, f"value range ({why})"
            report.warnings.append(
                f"{canonical}: unit guessed from values: {u} ({why}); "
                f"set units.{canonical} to confirm"
            )
        elif default is not None:
            unit, source = default, "default"
            report.warnings.append(
                f"{canonical}: unit unknown, assumed {default}; set units.{canonical} to confirm"
            )
        else:
            raise ValueError(
                f"{canonical} ({header!r}): cannot tell the unit ({why}); "
                f"set units.{canonical} in the config"
            )
    report.units[canonical] = {"unit": unit, "source": source, "column": header}
    return unit


def _times(df: pd.DataFrame, m: dict, report: IngestReport) -> np.ndarray:
    if "time" in m:
        col = df[m["time"]]
        if pd.api.types.is_numeric_dtype(col):
            v = col.to_numpy(dtype=float)
            med = np.nanmedian(v)
            if med > 1e12:
                return v / 1000.0
            if med > 1e9:
                return v
            if "date" in m:
                day = pd.to_datetime(df[m["date"]], dayfirst=True, utc=True)
                return epoch_seconds(day) + v
            report.warnings.append("time is relative seconds without a date")
            return v
        text = col.astype(str).str.strip()
        if "date" in m and text.str.fullmatch(r"\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?").all():
            text = df[m["date"]].astype(str).str.strip() + " " + text
        dayfirst = bool(text.str.match(r"^\d{1,2}[./]\d{1,2}[./]\d{2,4}").any())
        parsed = pd.to_datetime(text, dayfirst=dayfirst, utc=True, errors="coerce", format="mixed")
        if parsed.isna().mean() > 0.05:
            raise ValueError(f"could not parse time column {m['time']!r}")
        return epoch_seconds(parsed)
    if "duration" in m:
        d = _num(df[m["duration"]])
        report.derived.append("time from cumulative duration")
        return np.nancumsum(np.nan_to_num(d, nan=np.nanmedian(d)))
    report.warnings.append("no time column: assumed one record per second")
    return np.arange(len(df), dtype=float)


def normalize(
    df: pd.DataFrame, meta: dict, opts: IngestOptions, field_crs: str, source: str = ""
) -> tuple[pd.DataFrame, IngestReport]:
    report = IngestReport(source=source, format=meta.get("format", "?"), rows_in=len(df))
    explicit = {**meta.get("mapping", {}), **opts.columns}
    m = match_columns(list(df.columns), explicit)
    report.mapping = {k: v for k, v in m.items() if not v.startswith("__")}
    report.unmapped_columns = [
        c for c in df.columns if c not in m.values() and not c.startswith("__")
    ]
    preset = meta.get("units", {})
    bw = bushel_weight(opts.crop, opts.bushel_weight_lb)

    t = _times(df, m, report)

    # Positions
    pos_crs = opts.position_crs or meta.get("crs")
    if "x" in m and "y" in m:
        x, y = _num(df[m["x"]]), _num(df[m["y"]])
        src = pos_crs or field_crs
        looks_geographic = np.nanmax(np.abs(x)) <= 180 and np.nanmax(np.abs(y)) <= 90
        if looks_geographic and pos_crs is None:
            src = "EPSG:4326"
            report.warnings.append("x/y look like degrees: read as WGS84 lon/lat")
        if src.upper() != field_crs.upper():
            x, y = _project(x, y, src, field_crs)
            report.derived.append(f"positions projected {src} -> {field_crs}")
    elif "lon" in m and "lat" in m:
        x, y = _project(_num(df[m["lon"]]), _num(df[m["lat"]]), "EPSG:4326", field_crs)
        report.derived.append(f"positions projected EPSG:4326 -> {field_crs}")
    else:
        raise ValueError("no position columns found (x/y, easting/northing or lon/lat)")
    x, y = np.asarray(x, float), np.asarray(y, float)

    def col(name, default=np.nan):
        return _num(df[m[name]]) if name in m else np.full(len(df), default)

    def conv(name, default_unit=None, reference=None):
        v = col(name)
        if name not in m:
            return v
        u = _unit(name, m[name], v, opts, preset, report, default_unit, reference)
        return convert(v, name, u, bw)

    # Step between successive positions and times: a reference for distance/speed units.
    step = np.hypot(np.diff(x), np.diff(y))
    tstep = np.diff(t)
    ok = (tstep > 0) & (tstep < 10) & (step > 0.05)
    ref_step = float(np.median(step[ok])) if ok.any() else None
    ref_speed = float(np.median(step[ok] / tstep[ok])) if ok.any() else None

    swath = conv("swath")
    if "swath" not in m:
        if opts.default_swath_m:
            swath = np.full(len(df), float(opts.default_swath_m))
            report.warnings.append(f"no swath column: used header width {opts.default_swath_m} m")
        else:
            raise ValueError("no swath/width column; set default_swath_m (header width)")
    moisture = conv("moisture")
    speed = conv("speed", default_unit="km/h", reference=ref_speed)
    heading = conv("heading")
    distance = conv("distance", reference=ref_step)
    duration = conv("duration")

    dt = np.r_[np.nan, np.diff(t)]
    if np.isnan(duration).all():
        med = np.nanmedian(dt[dt > 0]) if (dt > 0).any() else 1.0
        duration = np.where((dt > 0) & (dt <= 5 * med), dt, med)

    # Mass
    vendor_mc = opts.vendor_reference_moisture_pct
    if "mass_flow" in m:
        report.input_family = "mass_flow"
        mass_flow = conv("mass_flow")
        entry = "L1B"
    elif "dry_mass_flow" in m:
        report.input_family = "dry_mass_flow"
        mass_flow = conv("dry_mass_flow")
        moisture = np.full(len(df), vendor_mc if vendor_mc is not None else np.nan)
        report.derived.append("dry mass flow: already at the vendor's reference moisture")
        entry = "L1B"
    else:
        ycol = next((c for c in ("yield_wet", "yield_dry", "yield_volume") if c in m), None)
        if ycol is None:
            raise ValueError("no mass flow or yield column found")
        report.input_family = f"yield_point ({ycol})"
        y_t_ha = conv(ycol)
        if np.isnan(distance).all():
            distance = np.r_[np.nan, np.hypot(np.diff(x), np.diff(y))]
            distance = np.where(np.isfinite(distance), distance, np.nanmedian(distance))
            report.derived.append("distance from successive positions")
        mass_kg = 0.1 * y_t_ha * distance * swath
        mass_flow = mass_kg / np.where(duration > 0, duration, np.nan)
        report.derived.append("mass rebuilt as yield x distance x swath")
        if ycol != "yield_wet":
            moisture = np.full(len(df), vendor_mc if vendor_mc is not None else np.nan)
            report.derived.append(
                "dry/standard yield: moisture set to the vendor reference "
                + (f"({vendor_mc} %)" if vendor_mc is not None else "(unknown: taken as is)")
            )
        entry = "L2"
    entry = opts.entry_level or meta.get("entry_level") or entry
    report.entry_level = entry

    # Header / work state
    if "header" in m:
        raw = df[m["header"]]
        if pd.api.types.is_numeric_dtype(raw):
            header = raw.to_numpy(dtype=float) > 0
        else:
            header = raw.astype(str).str.strip().str.lower().isin(_TRUE).to_numpy()
    else:
        header = np.isfinite(mass_flow) & (mass_flow > 0)
        report.derived.append("harvesting state from mass flow > 0 (no header column)")

    # GNSS quality
    if "gnss_quality" in m:
        raw = df[m["gnss_quality"]]
        if pd.api.types.is_numeric_dtype(raw):
            gq = raw.to_numpy(dtype=float)
        else:
            key = raw.astype(str).str.lower().str.replace(r"[^a-z]", "", regex=True)
            gq = key.map(_GNSS_TEXT).astype(float).to_numpy()
    else:
        gq = np.full(len(df), np.nan)

    machine = (
        df[m["machine"]].astype(str).str.strip().to_numpy()
        if "machine" in m
        else np.full(len(df), opts.machine_id, dtype=object)
    )

    out = pd.DataFrame(
        {
            "t": t,
            "machine_id": machine,
            "x": x,
            "y": y,
            "mass_flow_wet": mass_flow,
            "moisture": moisture,
            "swath": swath,
            "speed": speed,
            "heading": heading,
            "header_down": header & (swath > 0),
            "gnss_quality": gq,
            "entry_level": entry,
        }
    )
    keep = np.isfinite(out["x"]) & np.isfinite(out["y"]) & np.isfinite(out["t"])
    if (~keep).any():
        report.warnings.append(f"dropped {int((~keep).sum())} rows without time or position")
    out = out[keep].sort_values(["machine_id", "t"], kind="stable")
    dup = out.duplicated(["machine_id", "t"])
    if dup.any():
        report.warnings.append(f"dropped {int(dup.sum())} duplicate timestamps")
        out = out[~dup]
    out = out.reset_index(drop=True)
    report.rows_out = len(out)

    on = out["header_down"].to_numpy()
    yld = 10.0 * out["mass_flow_wet"] / (out["swath"] * out["speed"])
    report.summary = {
        "machines": sorted(map(str, out["machine_id"].unique())),
        "time_span_h": float((out["t"].max() - out["t"].min()) / 3600) if len(out) else 0.0,
        "harvesting_share": float(on.mean()) if len(out) else 0.0,
        "median_mass_flow_kg_s": _med(out["mass_flow_wet"][on]),
        "median_swath_m": _med(out["swath"][on]),
        "median_speed_m_s": _med(out["speed"][on]),
        "median_moisture_pct": _med(out["moisture"][on]),
        "median_yield_t_ha_wet": _med(yld[on]),
    }
    _plausibility(report)
    return out[OUTPUT_COLUMNS], report


def _med(s) -> float | None:
    v = pd.to_numeric(pd.Series(s), errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(v.median()) if len(v) else None


def _plausibility(report: IngestReport) -> None:
    s = report.summary
    checks = [
        ("median_swath_m", 1.5, 20, "swath"),
        ("median_speed_m_s", 0.2, 5, "speed"),
        ("median_moisture_pct", 5, 45, "moisture"),
        ("median_yield_t_ha_wet", 0.3, 25, "yield"),
    ]
    for key, lo, hi, what in checks:
        v = s.get(key)
        if v is not None and not lo <= v <= hi:
            report.warnings.append(
                f"implausible {what}: median {v:.3g} outside {lo}-{hi}; check the unit"
            )
