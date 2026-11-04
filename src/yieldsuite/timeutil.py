"""Time conversions that do not depend on pandas' datetime resolution.

pandas 2 parses timestamps as nanoseconds, pandas 3 as microseconds, so
``astype("int64") / 1e9`` is wrong on one of them. Subtracting the epoch and
dividing by a Timedelta works on both.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_EPOCH = pd.Timestamp(0, tz="UTC")


def epoch_seconds(values) -> np.ndarray:
    """Seconds since 1970-01-01 UTC for datetimes (naive values are taken as UTC)."""
    dt = pd.to_datetime(values, utc=True)
    if isinstance(dt, pd.Timestamp):
        return np.asarray([(dt - _EPOCH) / pd.Timedelta(1, "s")])
    delta = (pd.Series(dt) - _EPOCH) / pd.Timedelta(1, "s")
    return delta.to_numpy(dtype=float)
