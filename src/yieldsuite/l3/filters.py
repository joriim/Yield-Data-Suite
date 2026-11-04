"""Record filters. Each returns a boolean mask (True = flag the record).

Combine them into the bitmask with ``yieldsuite.schema.set_flag``.
"""

from __future__ import annotations

import numpy as np

from ..l1.moisture import valid_moisture


def flag_global_outliers(yield_t_ha, k: float = 3.0):
    """Outside median +/- k * MAD (MAD scaled by 1.4826 to match SD for normal data)."""
    y = np.asarray(yield_t_ha, dtype=float)
    med = np.nanmedian(y)
    mad = 1.4826 * np.nanmedian(np.abs(y - med))
    if mad == 0:
        return ~np.isfinite(y)
    return ~(np.abs(y - med) <= k * mad)


def flag_moisture(moisture_pct, lo: float = 10.0, hi: float = 33.0):
    return ~valid_moisture(moisture_pct, lo, hi)


def flag_speed(
    speed_m_s, t_s=None, vmin: float = 0.3, vmax: float = 4.0, max_accel: float | None = 0.5
):
    """Speed outside machine limits, or |dv/dt| above ``max_accel`` [m/s2]."""
    v = np.asarray(speed_m_s, dtype=float)
    mask = (v < vmin) | (v > vmax) | ~np.isfinite(v)
    if t_s is not None and max_accel is not None and v.size > 1:
        t = np.asarray(t_s, dtype=float)
        accel = np.abs(np.gradient(v, t))
        mask |= accel > max_accel
    return mask


def flag_fill_finish(pass_id, t_s, seconds: float):
    """First and last ``seconds`` of every pass (flow ramps at pass ends).

    Use roughly tau_d + tau_c.
    """
    pid = np.asarray(pass_id)
    t = np.asarray(t_s, dtype=float)
    mask = np.zeros(t.shape, dtype=bool)
    for p in np.unique(pid):
        sel = pid == p
        tp = t[sel]
        mask[sel] = (tp - tp.min() < seconds) | (tp.max() - tp < seconds)
    return mask


def local_morans_i(
    x, y, values, radius_m: float, permutations: int = 199, seed: int = 0, test=None
):
    """Local Moran's I (Anselin 1995) with row-standardised distance-band weights.

    I_i = z_i / m2 * sum_j w_ij z_j. Significance by conditional permutation:
    for each record, its neighbours' values are replaced by random draws from
    all other records. Returns (I, p_negative, neighbour_count, spatial lag of z),
    where p_negative is the one-sided p-value for I being this low. ``test`` (a
    boolean mask) limits the permutation test to those records (p = 1 elsewhere).
    """
    from shapely import STRtree, points

    x = np.asarray(x, float)
    y = np.asarray(y, float)
    v = np.asarray(values, float)
    n = v.size
    z = v - v.mean()
    m2 = float(np.mean(z**2))
    if n < 10 or m2 == 0:
        return np.zeros(n), np.ones(n), np.zeros(n, int), np.zeros(n)
    pts = points(x, y)
    left, right = STRtree(pts).query(pts, predicate="dwithin", distance=radius_m)
    keep = left != right
    left, right = left[keep], right[keep]
    k = np.bincount(left, minlength=n)
    lag = np.bincount(left, weights=z[right], minlength=n) / np.maximum(k, 1)
    local_i = z / m2 * lag

    rng = np.random.default_rng(seed)
    which = np.flatnonzero(np.ones(n, bool) if test is None else np.asarray(test, bool))
    lower = np.zeros(n)
    if which.size and k[which].max() > 0:
        kw = k[which]
        idx = np.arange(int(kw.max()))
        for _ in range(permutations):
            draws = z[rng.integers(0, n, size=(which.size, idx.size))]
            sim_lag = np.where(idx[None, :] < kw[:, None], draws, 0.0).sum(axis=1) / np.maximum(
                kw, 1
            )
            lower[which] += (z[which] / m2 * sim_lag) <= local_i[which]
    else:
        which = np.array([], int)
    tested = np.zeros(n, bool)
    tested[which] = True
    p_neg = np.where(tested, (lower + 1) / (permutations + 1), 1.0)
    return local_i, p_neg, k, lag


def neighbour_median_residual(x, y, values, radius_m: float):
    """Robust standardised difference of each value from its neighbours' median."""
    from shapely import STRtree, points

    v = np.asarray(values, float)
    pts = points(np.asarray(x, float), np.asarray(y, float))
    left, right = STRtree(pts).query(pts, predicate="dwithin", distance=radius_m)
    keep = left != right
    import pandas as pd

    med = pd.Series(v[right[keep]]).groupby(left[keep]).median()
    nb = np.full(v.size, np.nan)
    nb[med.index.to_numpy()] = med.to_numpy()
    resid = v - nb
    ok = np.isfinite(resid)
    scale = 1.4826 * np.median(np.abs(resid[ok] - np.median(resid[ok]))) if ok.any() else 0
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok & (scale > 0), resid / scale, 0.0)


def flag_local_outliers(
    x,
    y,
    values,
    radius_m: float = 20.0,
    alpha: float = 0.01,
    min_diff_sd: float = 2.0,
    min_neighbours: int = 4,
    robust_k: float = 4.0,
    permutations: int = 199,
    seed: int = 0,
    return_parts: bool = False,
):
    """Local spatial outliers, two complementary tests:

    1. significantly negative local Moran's I (a high value among low neighbours
       or the reverse, Anselin 1995 / Vega et al. 2019) that also differs from
       its neighbours' mean by more than ``min_diff_sd`` standard deviations, so
       ordinary noise is not flagged at the permutation test's base rate;
    2. a value more than ``robust_k`` robust SDs from its neighbours' median.
       Local Moran's I misses a hole whose neighbours sit near the field mean
       (their spatial lag is ~0); this catches it.
    """
    v = np.asarray(values, float)
    sd = float(np.std(v))
    z = v - v.mean()
    # Only records that could be flagged need the (costly) permutation test.
    local_i, _, k, lag = local_morans_i(x, y, v, radius_m, permutations=0)
    could = (local_i < 0) & (np.abs(z - lag) > min_diff_sd * sd) & (k >= min_neighbours)
    local_i, p_neg, k, lag = local_morans_i(x, y, v, radius_m, permutations, seed, test=could)
    if sd == 0:
        empty = np.zeros(v.size, bool)
        return (empty, empty, empty) if return_parts else empty
    moran = (
        (local_i < 0)
        & (p_neg < alpha)
        & (k >= min_neighbours)
        & (np.abs(z - lag) > min_diff_sd * sd)
    )
    robust = (np.abs(neighbour_median_residual(x, y, v, radius_m)) > robust_k) & (
        k >= min_neighbours
    )
    both = moran | robust
    return (both, moran, robust) if return_parts else both
