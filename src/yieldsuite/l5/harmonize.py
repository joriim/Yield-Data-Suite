"""Harmonise feature sources to Sentinel-2 (the reference).

For each other source and index, a linear fit  ref = a + b * x  is made on the
cells observed by both within ``max_days`` of each other. Sources whose fit is
weak (R² below ``min_r2``) or that share too few observations with the
reference are dropped from the model and reported, not silently mixed in.
Without the reference, sources are used as they are and the report says so.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REFERENCE = "sentinel2"


def ensure_source(df: pd.DataFrame, default: str = REFERENCE) -> pd.DataFrame:
    if "source" not in df.columns:
        df = df.assign(source=default)
    return df


def harmonize(
    features: pd.DataFrame,
    reference: str = REFERENCE,
    max_days: int = 2,
    min_pairs: int = 30,
    min_r2: float = 0.5,
) -> tuple[pd.DataFrame, dict]:
    df = ensure_source(features).copy()
    df["day"] = pd.to_datetime(df["date"]).dt.normalize()
    sources = sorted(df["source"].unique())
    report: dict = {"reference": reference, "sources": {}}
    if reference not in sources:
        report["note"] = f"no {reference} data: sources used without harmonisation"
        return df.drop(columns="day"), report
    ref = df[df["source"] == reference]
    out = [ref]
    for src in sources:
        if src == reference:
            continue
        sub = df[df["source"] == src]
        rep = {"indices": {}, "used": False}
        kept = []
        for index, part in sub.groupby("index"):
            r = ref[ref["index"] == index]
            if r.empty:
                rep["indices"][index] = {"used": False, "reason": "reference lacks this index"}
                continue
            pairs = part.merge(r, on=["cell", "index"], suffixes=("", "_ref"))
            pairs = pairs[(pairs["day"] - pairs["day_ref"]).abs().dt.days <= max_days]
            # one pair per observation: the closest reference date
            pairs["gap"] = (pairs["day"] - pairs["day_ref"]).abs()
            pairs = pairs.sort_values("gap").drop_duplicates(["cell", "date"])
            if len(pairs) < min_pairs:
                rep["indices"][index] = {
                    "used": False,
                    "reason": f"{len(pairs)} co-located observations "
                    f"within {max_days} days (< {min_pairs})",
                }
                continue
            x, y = pairs["value"].to_numpy(), pairs["value_ref"].to_numpy()
            b, a = np.polyfit(x, y, 1)
            r2 = float(np.corrcoef(x, y)[0, 1] ** 2)
            info = {
                "pairs": int(len(pairs)),
                "slope": float(b),
                "offset": float(a),
                "r2": r2,
                "used": r2 >= min_r2,
            }
            if r2 < min_r2:
                info["reason"] = f"weak fit to {reference} (R² {r2:.2f} < {min_r2})"
            else:
                kept.append(part.assign(value=a + b * part["value"]))
            rep["indices"][index] = info
        if kept:
            rep["used"] = True
            out.append(pd.concat(kept, ignore_index=True))
        report["sources"][src] = rep
    return pd.concat(out, ignore_index=True).drop(columns="day"), report


def collapse(features: pd.DataFrame) -> pd.DataFrame:
    """One value per cell, date and index (mean over sources observing the same day)."""
    return features.groupby(["cell", "date", "index"], as_index=False)["value"].mean()[
        ["cell", "date", "index", "value"]
    ]
