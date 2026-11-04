"""Correction 1 (check): residual along-track shift between yield and imagery.

    delta_hat = argmax_delta corr( Y(x), V(x + delta) ),  delta along each record's heading

Records are moved back along their own heading by delta (so passes driven in
opposite directions move in opposite world directions) and re-binned to the
grid; the shift with the best correlation to the image prediction is
reported as an extra delay delta / speed. Following the design document,
a clear non-zero optimum is a recommendation to correct tau_d (or the antenna
offset) in the machine profile and re-run, not an edit of the map.
"""

from __future__ import annotations

import numpy as np

from ..l4.grid import Grid
from .records import RecordView, cell_index


def residual_shift(
    view: RecordView,
    grid: Grid,
    reference: np.ndarray,
    candidates_m=None,
    min_coverage: float = 0.5,
    min_gain: float = 0.01,
) -> dict:
    if candidates_m is None:
        candidates_m = np.arange(-24.0, 24.1, 1.5)
    cands = [float(c) for c in candidates_m]
    ref = np.asarray(reference, float).ravel()
    m = view.clean
    fx, fy = np.sin(view.heading[m]), np.cos(view.heading[m])
    n = grid.nx * grid.ny
    corr = []
    for d in cands:
        cell = cell_index(grid, view.cx[m] - fx * d, view.cy[m] - fy * d)
        ok = cell >= 0
        mass = np.bincount(cell[ok], view.mass[m][ok], minlength=n)
        area = np.bincount(cell[ok], view.area[m][ok], minlength=n)
        use = (area >= min_coverage * grid.cell_area) & np.isfinite(ref)
        if use.sum() < 10:
            corr.append(np.nan)
            continue
        y = 10.0 * mass[use] / area[use]
        corr.append(float(np.corrcoef(y, ref[use])[0, 1]))
    corr = np.asarray(corr)
    if np.isnan(corr).all():
        return {"significant": False, "reason": "too few covered cells", "curve": {}}
    k = int(np.nanargmax(corr))
    zero = cands.index(0.0) if 0.0 in cands else int(np.argmin(np.abs(cands)))
    gain = float(corr[k] - corr[zero])
    at_edge = k in (0, len(cands) - 1)
    speed = float(np.nanmedian(view.speed[m])) if m.any() else np.nan
    best = cands[k]
    significant = best != 0.0 and gain >= min_gain and not at_edge
    return {
        "best_shift_m": best,
        "suggested_extra_delay_s": round(best / speed, 1) if speed > 0 else None,
        "corr_at_zero": float(corr[zero]),
        "corr_at_best": float(corr[k]),
        "gain": gain,
        "significant": bool(significant),
        "reason": ""
        if significant
        else (
            "best shift at the edge of the search range"
            if at_edge
            else "no clear improvement over zero shift"
        ),
        "curve": {
            str(c): (None if not np.isfinite(v) else round(float(v), 4))
            for c, v in zip(cands, corr, strict=True)
        },
    }
