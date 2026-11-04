"""Flow delay estimation from the data itself (no extra hardware).

A wrong delay shifts the yield pattern along the track, in opposite directions
on passes driven north and south. The estimator therefore picks the delay that
makes adjacent, oppositely driven passes agree best (design doc, L1 -> L2):

    tau_hat = argmin_tau mean_{(i,j) in N} (log Y_i(tau) - log Y_j(tau))^2

N are pairs of records on adjacent passes with opposite headings, side by side
(lateral offset about one swath, along-track offset under one record length).
Log yields make the criterion insensitive to the calibration level.

The result is reported with its whole curve: a flat curve (field without
along-track pattern, or too few opposite passes) means the delay is not
identifiable from this field and the machine profile value should be used.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import shapely
from shapely import STRtree

from .track import shift_signal


def estimate_delay(
    candidates_s: Iterable[float],
    discrepancy: Callable[[float], float],
) -> tuple[float, np.ndarray]:
    """Grid search for the delay minimising ``discrepancy(tau)``."""
    taus = np.asarray(list(candidates_s), dtype=float)
    if taus.size == 0:
        raise ValueError("no candidate delays")
    values = np.array([discrepancy(float(t)) for t in taus])
    if np.isnan(values).all():
        raise ValueError("discrepancy undefined for every candidate delay")
    return float(taus[int(np.nanargmin(values))]), values


def opposite_pass_pairs(
    x,
    y,
    heading,
    pass_id,
    swath,
    max_along_m: float | None = None,
    lateral_range: tuple[float, float] = (0.5, 1.5),
):
    """Index pairs (i, j) of side-by-side records on adjacent, opposite passes.

    ``lateral_range`` is in multiples of the median swath.
    """
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    h = np.asarray(heading, float)
    pid = np.asarray(pass_id)
    on = (pid >= 0) & np.isfinite(x) & np.isfinite(y) & np.isfinite(h)
    idx = np.flatnonzero(on)
    if idx.size < 2:
        return np.array([], int), np.array([], int)
    w = float(np.nanmedian(np.asarray(swath, float)[idx]))
    if not np.isfinite(w) or w <= 0:
        return np.array([], int), np.array([], int)
    if max_along_m is None:
        step = np.hypot(np.diff(x[idx]), np.diff(y[idx]))
        step = step[(step > 0) & (step < 3 * w)]
        max_along_m = float(np.median(step)) if step.size else w / 2
    pts = shapely.points(x[idx], y[idx])
    left, right = STRtree(pts).query(
        pts, predicate="dwithin", distance=lateral_range[1] * w + max_along_m
    )
    i, j = idx[left], idx[right]
    keep = (i < j) & (pid[i] != pid[j])
    i, j = i[keep], j[keep]
    turn = np.abs(np.angle(np.exp(1j * (h[i] - h[j]))))
    keep = turn > np.deg2rad(150)
    i, j = i[keep], j[keep]
    dx, dy = x[j] - x[i], y[j] - y[i]
    fx, fy = np.sin(h[i]), np.cos(h[i])
    along = np.abs(dx * fx + dy * fy)
    lateral = np.abs(dx * fy - dy * fx)
    keep = (
        (along <= max_along_m)
        & (lateral >= lateral_range[0] * w)
        & (lateral <= lateral_range[1] * w)
    )
    return i[keep], j[keep]


@dataclass
class DelayEstimate:
    tau_d_s: float | None
    identifiable: bool
    n_pairs: int
    candidates_s: list[float] = field(default_factory=list)
    curve: list[float] = field(default_factory=list)
    contrast: float = 0.0  # (median - min) / min of the curve; noise alone gives < 1
    reason: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def estimate_flow_delay(
    t_s,
    mass_flow,
    dt_s,
    area_m2,
    x,
    y,
    heading,
    pass_id,
    swath,
    candidates_s: Iterable[float] = np.arange(0.0, 30.5, 0.5),
    min_pairs: int = 200,
    min_contrast: float = 1.0,
    smooth_records: int = 5,
) -> DelayEstimate:
    """Estimate tau_d for one machine from opposite-pass agreement.

    ``x``/``y`` are header positions (antenna offset applied), ``area_m2`` the
    nominal record area (length x swath), ``mass_flow`` the logged wet flow.
    """
    cands = [float(c) for c in candidates_s]
    i, j = opposite_pass_pairs(x, y, heading, pass_id, swath)
    n = int(i.size)
    if n < min_pairs:
        return DelayEstimate(
            None, False, n, cands, [], 0.0, f"only {n} opposite-pass pairs (need {min_pairs})"
        )
    t = np.asarray(t_s, float)
    m = np.asarray(mass_flow, float)
    dt = np.asarray(dt_s, float)
    area = np.asarray(area_m2, float)

    pid = np.asarray(pass_id)
    groups = pd.Series(pid)

    def discrepancy(tau: float) -> float:
        shifted = shift_signal(t, m, tau) if tau else m
        with np.errstate(divide="ignore", invalid="ignore"):
            yld = shifted * dt / area
        # Running median along each pass: spikes and sensor noise must not decide
        # the delay, the along-track pattern should.
        yld = np.where(np.isfinite(yld) & (yld > 0), yld, np.nan)
        yld = (
            pd.Series(yld)
            .groupby(groups)
            .transform(lambda s: s.rolling(smooth_records, center=True, min_periods=1).median())
            .to_numpy()
        )
        yi, yj = yld[i], yld[j]
        ok = np.isfinite(yi) & np.isfinite(yj) & (yi > 0) & (yj > 0)
        if ok.sum() < min_pairs // 2:
            return np.nan
        return float(np.mean((np.log(yi[ok]) - np.log(yj[ok])) ** 2))

    best, values = estimate_delay(cands, discrepancy)
    k = cands.index(best)
    if 0 < k < len(cands) - 1 and np.isfinite(values[k - 1 : k + 2]).all():
        a, b, c = values[k - 1], values[k], values[k + 1]
        denom = a - 2 * b + c
        if denom > 0:  # parabola vertex, within half a step of the grid minimum
            step = cands[k + 1] - cands[k]
            best = float(best + np.clip(0.5 * (a - c) / denom, -0.5, 0.5) * step)
    finite = values[np.isfinite(values)]
    med = float(np.median(finite))
    lo = float(np.min(finite))
    contrast = (med - lo) / lo if lo > 0 else 0.0
    at_edge = k in (0, len(cands) - 1)
    identifiable = contrast >= min_contrast and not at_edge
    reason = (
        ""
        if identifiable
        else (
            "minimum at the edge of the search range"
            if at_edge
            else f"flat curve (contrast {contrast:.2f} < {min_contrast})"
        )
    )
    return DelayEstimate(
        round(best, 1) if identifiable else None,
        identifiable,
        n,
        cands,
        [None if not np.isfinite(v) else float(v) for v in values],
        float(contrast),
        reason,
    )
