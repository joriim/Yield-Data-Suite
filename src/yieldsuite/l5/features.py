"""Turn the long Sentinel-2 table into a feature matrix V_g (one row per grid cell)."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class FeatureInfo:
    dates_used: list[str] = field(default_factory=list)
    dates_dropped: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


def feature_matrix(
    long_df: pd.DataFrame,
    n_cells: int,
    min_valid_share: float = 0.8,
    per_date: bool = False,
) -> tuple[pd.DataFrame, FeatureInfo]:
    """Feature matrix indexed by flat cell id 0..n_cells-1.

    Dates where fewer than ``min_valid_share`` of the aggregated cells have a
    value (clouds, partial tiles) are dropped. Features are, per index, the
    season mean and max over the kept dates; with ``per_date`` the individual
    dates are added as ``<index>_<YYYYMMDD>`` columns (useful for random forest).
    Cells without a value on some kept date get NaN and are left out of fitting.
    """
    info = FeatureInfo()
    if long_df.empty:
        return pd.DataFrame(index=pd.RangeIndex(n_cells, name="cell")), info

    n_aggregated = long_df["cell"].nunique()
    valid = long_df.groupby("date")["cell"].nunique() / max(n_aggregated, 1)
    keep = sorted(valid[valid >= min_valid_share].index)
    info.dates_used = keep
    info.dates_dropped = sorted(set(valid.index) - set(keep))
    df = long_df[long_df["date"].isin(keep)]

    wide = df.pivot_table(index="cell", columns=["index", "date"], values="value", aggfunc="mean")
    wide = wide.reindex(pd.RangeIndex(n_cells, name="cell"))

    parts = {}
    for name in sorted(df["index"].unique()):
        block = wide[name]
        complete = block.notna().all(axis=1)
        parts[f"{name}_mean"] = block.mean(axis=1).where(complete)
        parts[f"{name}_max"] = block.max(axis=1).where(complete)
        if per_date:
            for date in block.columns:
                parts[f"{name}_{date.replace('-', '')}"] = block[date]
    V = pd.DataFrame(parts, index=wide.index)
    info.columns = list(V.columns)
    return V, info


def complete_rows(V: pd.DataFrame) -> np.ndarray:
    """True for cells with every feature present."""
    if V.shape[1] == 0:
        return np.zeros(len(V), dtype=bool)
    return V.notna().all(axis=1).to_numpy()
