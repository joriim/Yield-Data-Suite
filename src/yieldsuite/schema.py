"""Record schema, quality flags and L4 raster bands."""

from __future__ import annotations

from enum import IntFlag

import numpy as np


class Flag(IntFlag):
    """Quality flags per record. Records are flagged, never deleted."""

    NONE = 0
    HEADER_UP = 1 << 0
    FILL_FINISH = 1 << 1
    SPEED = 1 << 2
    SWATH = 1 << 3
    MOISTURE = 1 << 4
    GNSS = 1 << 5
    FIELD_EDGE = 1 << 6
    GLOBAL_OUTLIER = 1 << 7
    LOCAL_OUTLIER = 1 << 8


def set_flag(flags: np.ndarray, mask: np.ndarray, flag: Flag) -> np.ndarray:
    """Return ``flags`` with ``flag`` set where ``mask`` is true."""
    flags = np.asarray(flags, dtype=np.int32).copy()
    flags[np.asarray(mask, dtype=bool)] |= int(flag)
    return flags


def is_clean(flags: np.ndarray) -> np.ndarray:
    """True where a record carries no flag."""
    return np.asarray(flags, dtype=np.int32) == 0


# Record columns (L1A-L3, one row per sample). Units in brackets.
RECORD_COLUMNS: dict[str, str] = {
    "time": "UTC timestamp, unified machine clock",
    "machine_id": "machine registry id",
    "pass_id": "pass number within the harvest session",
    "x": "easting [m], metric CRS of the field",
    "y": "northing [m], metric CRS of the field",
    "gnss_quality": "receiver fix quality (4 = RTK fix)",
    "speed": "ground speed [m/s]",
    "heading": "heading [rad], clockwise from grid north",
    "swath": "actual cut width [m]",
    "force": "impact-plate force [N] (true L0 only)",
    "elevator_speed": "elevator shaft speed [rad/s] (true L0 only)",
    "moisture": "grain moisture [% wet basis]",
    "mass_flow_wet": "wet mass flow [kg/s]",
    "mass_flow_ref": "mass flow at reference moisture [kg/s]",
    "applied_delay": "flow delay applied [s]",
    "entry_level": "level at which the record entered the suite",
    "effective_area": "harvested area not overlapping earlier records [m2]",
    "yield": "yield at reference moisture [t/ha]",
    "flags": "Flag bitmask",
}

# Bands of the L4 Cloud-Optimised GeoTIFF, in order.
L4_BANDS: tuple[str, ...] = (
    "yield",  # t/ha at reference moisture
    "coverage",  # share of cell area covered by clean records
    "n_records",  # number of contributing records
    "spread",  # area-weighted SD of record yields in the cell
    "flagged_share",  # share of harvested area from flagged records
    "passes",  # number of distinct passes
)

# Bands of the L5 GeoTIFF, in order.
L5_BANDS: tuple[str, ...] = (
    "yield",  # t/ha: measured where reliable, filled from imagery elsewhere
    "source",  # 0 no value, 1 measured (L4), 2 filled from imagery
    "sigma",  # standard error of filled cells [t/ha]
    "prediction",  # image-model prediction f(V) for every cell
    "relative_yield",  # yield as % of the field mean
    "coverage",  # L4 coverage, carried over
)
