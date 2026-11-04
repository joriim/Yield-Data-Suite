"""Inspect a yield/machine data file before using it.

Prints what was detected (format, columns, units and where each unit came
from, entry level, derived values, warnings) and a ``harvest_logs`` entry for
the field-year config with every assumption written out, so a person confirms
it once and later runs are reproducible.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..config import HarvestLog
from . import read_harvest_log


def inspect_file(path: str | Path, crs: str = "EPSG:3067", **log_kwargs) -> dict:
    log = HarvestLog(path=Path(path), **log_kwargs)
    records, report = read_harvest_log(log, crs)
    entry = {"path": str(path), "machine_id": log.machine_id, "format": report.format}
    meta_cols = {"x": "__x", "y": "__y"}
    columns = {k: v for k, v in report.mapping.items() if meta_cols.get(k) != v}
    if report.format not in ("isoxml", "agleader_advanced"):
        entry["columns"] = columns
    entry["units"] = {k: u["unit"] for k, u in report.units.items()}
    entry["entry_level"] = report.entry_level
    for key in (
        "crop",
        "bushel_weight_lb",
        "vendor_reference_moisture_pct",
        "default_swath_m",
        "position_crs",
    ):
        if getattr(log, key) is not None:
            entry[key] = getattr(log, key)
    preview = records.head(5).round(3).to_dict(orient="records")
    return {
        "report": report.as_dict(),
        "config_entry": entry,
        "config_yaml": yaml.safe_dump(
            {"harvest_logs": [entry]}, sort_keys=False, allow_unicode=True
        ),
        "preview": preview,
    }


def format_report(result: dict) -> str:
    r = result["report"]
    lines = [
        f"{Path(r['source']).name}: {r['format']}, {r['rows_in']} rows -> {r['rows_out']} records",
        f"  input: {r['input_family']}, entry level {r['entry_level']}",
        "  columns:",
    ]
    for canonical, col in r["mapping"].items():
        unit = r["units"].get(canonical)
        u = f"  [{unit['unit']} from {unit['source']}]" if unit else ""
        lines.append(f"    {canonical:14} <- {col}{u}")
    if r["unmapped_columns"]:
        lines.append(f"  not used: {', '.join(map(str, r['unmapped_columns']))}")
    for d in r["derived"]:
        lines.append(f"  derived: {d}")
    s = r["summary"]
    lines.append(
        "  medians while harvesting: "
        + ", ".join(
            f"{k.removeprefix('median_')}={v:.3g}"
            for k, v in s.items()
            if k.startswith("median_") and v is not None
        )
    )
    lines.append(f"  machines: {', '.join(s['machines'])}; span {s['time_span_h']:.2f} h")
    for w in r["warnings"]:
        lines.append(f"  WARNING: {w}")
    lines.append("\nconfig entry (check and paste into field.yaml):\n")
    lines.append(result["config_yaml"])
    return "\n".join(lines)
