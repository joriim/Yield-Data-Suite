"""L4 -> L5 with Sentinel-2: yield-image model + regression-kriging gap fill."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import __version__
from ..l4.grid import Grid
from .features import complete_rows, feature_matrix
from .gapfill import SOURCE_FILLED, SOURCE_MEASURED, gap_fill
from .harmonize import collapse, ensure_source, harmonize
from .model import fit_yield_image_model
from .sentinel2 import S2Request


@dataclass
class FusionResult:
    bands: dict  # 2-D arrays named as yieldsuite.schema.L5_BANDS
    model_card: dict


def cell_centres(grid: Grid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Flat row, col and (x, y) centre of every cell."""
    rows, cols = np.divmod(np.arange(grid.ny * grid.nx), grid.nx)
    x = grid.xmin + (cols + 0.5) * grid.resolution
    y = grid.ymax - (rows + 0.5) * grid.resolution
    return rows, cols, np.c_[x, y]


def fuse(
    l4: dict,
    grid: Grid,
    features_long: pd.DataFrame,
    method: str = "linear",
    residual: str = "kriging",
    min_coverage: float = 0.5,
    max_flagged_share: float = 0.5,
    min_valid_share: float = 0.8,
    per_date: bool | None = None,
    request: S2Request | None = None,
) -> FusionResult:
    """Fit Y = f(V) on reliable L4 cells and fill the rest.

    Reliable cells: finite yield, coverage >= ``min_coverage`` and (if the band
    exists) flagged_share <= ``max_flagged_share``.
    """
    n = grid.ny * grid.nx
    y = np.asarray(l4["yield"], dtype=float).ravel()
    coverage = np.asarray(l4.get("coverage", np.isfinite(l4["yield"])), dtype=float).ravel()
    reliable = np.isfinite(y) & (coverage >= min_coverage)
    if "flagged_share" in l4:
        fs = np.asarray(l4["flagged_share"], dtype=float).ravel()
        reliable &= ~(fs > max_flagged_share)

    if per_date is None:
        per_date = method == "rf"
    features_long = ensure_source(features_long)
    sources_report = None
    if features_long["source"].nunique() > 1:
        features_long, sources_report = harmonize(features_long)
    V, finfo = feature_matrix(
        collapse(features_long), n, min_valid_share=min_valid_share, per_date=per_date
    )
    if not finfo.dates_used:
        raise ValueError("no image date passed the cloud filter; widen the window")
    has_features = complete_rows(V)

    rows, cols, xy = cell_centres(grid)
    model = fit_yield_image_model(
        y, V, reliable & has_features, rows=rows, cols=cols, method=method
    )
    prediction = model.predict(V)
    ablation = _ablation(
        features_long,
        y,
        reliable,
        rows,
        cols,
        n,
        method,
        min_valid_share,
        per_date,
        model.stats.get("cv_rmse"),
    )
    sigma_f = model.stats.get("cv_rmse", float("nan"))
    if not np.isfinite(sigma_f):
        sigma_f = model.stats["rmse"]

    filled = gap_fill(
        y, prediction, reliable & has_features, xy, sigma_f, grid.resolution, residual=residual
    )
    out_y = filled["yield"]
    # Measured cells without image features keep their yield as measured.
    keep = reliable & ~has_features
    out_y[keep] = y[keep]
    filled["source"][keep] = SOURCE_MEASURED

    with np.errstate(invalid="ignore"):
        mean_y = np.nanmean(out_y) if np.isfinite(out_y).any() else np.nan
        relative = 100.0 * out_y / mean_y

    shape = (grid.ny, grid.nx)
    bands = {
        "yield": out_y.reshape(shape),
        "source": filled["source"].reshape(shape).astype(float),
        "sigma": filled["sigma"].reshape(shape),
        "prediction": prediction.reshape(shape),
        "relative_yield": relative.reshape(shape),
        "coverage": coverage.reshape(shape),
    }
    n_out = int(np.isfinite(out_y).sum())
    n_filled = int((filled["source"] == SOURCE_FILLED).sum())
    card = {
        "level": "L5",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "code_version": __version__,
        "method": method,
        "residual": residual,
        "features": model.feature_names,
        "fit": model.stats,
        "sources": {
            "used": sorted(features_long["source"].unique()),
            "harmonisation": sources_report,
            "ablation_cv_rmse": ablation,
        },
        "sentinel2": {
            **(asdict(request) if request else {}),
            "dates_used": finfo.dates_used,
            "dates_dropped": finfo.dates_dropped,
        },
        "training": {
            "min_coverage": min_coverage,
            "max_flagged_share": max_flagged_share,
            "rule": "filled cells are never used as training data",
        },
        "cells": {
            "with_output": n_out,
            "measured": int((filled["source"] == SOURCE_MEASURED).sum()),
            "filled": n_filled,
            "filled_share": n_filled / n_out if n_out else 0.0,
        },
        "variogram": filled["variogram"],
        "source_codes": {"0": "no value", "1": "measured (L4)", "2": "filled from imagery"},
    }
    return FusionResult(bands, card)


def _ablation(
    features_long, y, reliable, rows, cols, n, method, min_valid_share, per_date, cv_all
) -> dict:
    """Spatial-CV RMSE of the model without each non-reference source.

    Positive ``gain`` means the source made the model better.
    """
    out = {}
    sources = sorted(features_long["source"].unique())
    if len(sources) < 2 or cv_all is None:
        return out
    for src in sources:
        rest = features_long[features_long["source"] != src]
        try:
            V, info = feature_matrix(
                collapse(rest), n, min_valid_share=min_valid_share, per_date=per_date
            )
            if not info.dates_used:
                raise ValueError("no dates")
            m = fit_yield_image_model(
                y, V, reliable & complete_rows(V), rows=rows, cols=cols, method=method
            )
            cv = m.stats.get("cv_rmse")
            out[src] = {"cv_rmse_without": cv, "gain": None if cv is None else cv - cv_all}
        except ValueError as exc:
            out[src] = {"cv_rmse_without": None, "note": f"model without it failed: {exc}"}
    return out
