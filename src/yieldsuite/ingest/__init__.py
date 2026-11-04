"""Ingest: any recognised yield or machine data file -> the common record schema.

``read_harvest_log`` detects the format (table, Excel, vector, ISOXML, Ag Leader
advanced), recognises columns and units, rebuilds mass from yield points when
needed, and returns the records with an ``IngestReport`` that says what was
assumed. Every adapter records the *entry level* so vendor-applied corrections
are not applied twice (design rule 2).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import HarvestLog
from ..levels import Level
from .normalize import IngestOptions, IngestReport, normalize
from .readers import detect_format, read_any


def _options(log: HarvestLog) -> IngestOptions:
    return IngestOptions(
        machine_id=log.machine_id,
        columns=log.columns,
        units=log.unit_overrides(),
        entry_level=log.entry_level,
        position_crs=log.position_crs,
        crop=log.crop,
        bushel_weight_lb=log.bushel_weight_lb,
        vendor_reference_moisture_pct=log.vendor_reference_moisture_pct,
        default_swath_m=log.default_swath_m,
    )


def read_harvest_log(log: HarvestLog, crs: str) -> tuple[pd.DataFrame, IngestReport]:
    ddi_map = {int(k): tuple(v) for k, v in log.ddi_map.items()} or None
    df, meta = read_any(
        log.path,
        log.format,
        delimiter=log.delimiter,
        decimal=log.decimal,
        layer=log.layer,
        sheet=log.sheet,
        ddi_map=ddi_map,
    )
    return normalize(df, meta, _options(log), crs, source=str(log.path))


def read_logs(logs: list[HarvestLog], crs: str) -> tuple[pd.DataFrame, list[dict]]:
    """Read and concatenate all harvest logs, sorted by time; also return the reports."""
    frames, reports = [], []
    for log in logs:
        if not Path(log.path).exists():
            raise FileNotFoundError(f"harvest log not found: {log.path}")
        df, report = read_harvest_log(log, crs)
        if df.empty:
            raise ValueError(f"{log.path}: no usable records")
        if Level.parse(df["entry_level"].iloc[0]) < Level.L1B:
            raise NotImplementedError("raw force (L0/L1A) logs need the L1 sensor module")
        frames.append(df)
        reports.append(report.as_dict())
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["t", "machine_id"], kind="stable", ignore_index=True), reports


__all__ = ["IngestOptions", "IngestReport", "detect_format", "read_harvest_log", "read_logs"]
