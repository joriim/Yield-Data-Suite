"""Correction 2: pass stripes as a random multiplicative pass effect.

    log Y_i = log f(V_g(i)) + b_p(i) + e_i,   b_p ~ N(0, sigma_b^2),   Y*_i = Y_i exp(-b_p)

f(V) is the image prediction (the spatial reference that does not know the
passes). Pass effects below ``min_sigma_b`` (default 3 %) are reported but not
corrected: a 20 m image reference cannot separate them from within-cell yield
gradients. b_p is estimated per pass as a shrunken mean residual (best linear
unbiased predictor with method-of-moments variances), so short passes are
corrected less. The gains are renormalised so the field total is unchanged:
reconciliation to scale tickets stays intact.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .records import RecordView, cell_index, sample_bilinear


def pass_effects(
    resid: np.ndarray,
    pass_key: np.ndarray,
    unit: np.ndarray,
    min_units: int = 3,
    min_sigma_b: float = 0.03,
) -> tuple[dict, dict]:
    """Shrunken pass means of log residuals.

    Residuals are first averaged per (pass, unit), where a unit is a grid cell:
    the reference is only resolved at cell level, so its errors are shared by
    all records of a pass in that cell. Counting records instead of cells would
    mistake reference noise for pass effects.
    """
    df = pd.DataFrame({"r": resid, "p": pass_key, "u": unit}).dropna()
    df = df[df["u"] >= 0]
    units = df.groupby(["p", "u"])["r"].mean().reset_index()
    g = units.groupby("p")["r"]
    stats = g.agg(["mean", "var", "count"])
    stats = stats[stats["count"] >= min_units]
    if len(stats) < 3:
        return {}, {"applied": False, "reason": f"only {len(stats)} passes with data"}
    within = float(np.average(stats["var"], weights=stats["count"] - 1))
    between = float(np.var(stats["mean"], ddof=1) - np.mean(within / stats["count"]))
    sigma_b2 = max(between, 0.0)
    if sigma_b2**0.5 < min_sigma_b:
        return {}, {
            "applied": False,
            "reason": (
                f"pass effect sigma_b {sigma_b2**0.5:.3f} below {min_sigma_b}: within "
                "what a grid-resolution image reference can resolve"
            ),
            "sigma_b": sigma_b2**0.5,
            "sigma_within": within**0.5,
            "n_passes": int(len(stats)),
        }
    if sigma_b2 == 0.0:
        return {}, {
            "applied": False,
            "reason": "no pass effect beyond noise",
            "sigma_within": within**0.5,
            "n_passes": int(len(stats)),
        }
    shrink = sigma_b2 / (sigma_b2 + within / stats["count"])
    b = stats["mean"] * shrink
    b = b - np.average(b, weights=stats["count"])  # pass effects relative to the field
    return b.to_dict(), {
        "applied": True,
        "n_passes": int(len(stats)),
        "sigma_b": sigma_b2**0.5,
        "sigma_within": within**0.5,
        "share_of_variance": sigma_b2 / (sigma_b2 + within),
        "max_abs_effect": float(np.abs(b).max()),
    }


def level_pass_stripes(
    view: RecordView,
    grid,
    reference: np.ndarray,
    min_units: int = 3,
    min_sigma_b: float = 0.03,
) -> tuple[np.ndarray, dict]:
    """Return corrected masses (all records) and statistics."""
    ref_i = sample_bilinear(grid, reference, view.cx, view.cy)
    with np.errstate(divide="ignore", invalid="ignore"):
        y = 10.0 * view.mass / view.area
        resid = np.where(view.clean & (ref_i > 0) & (y > 0), np.log(y) - np.log(ref_i), np.nan)
    unit = cell_index(grid, view.cx, view.cy)
    effects, stats = pass_effects(resid, view.pass_key, unit, min_units, min_sigma_b)
    if not stats.get("applied"):
        return view.mass.copy(), stats
    b = np.array([effects.get(p, 0.0) for p in view.pass_key])
    corrected = view.mass * np.exp(-b)
    total, new_total = view.mass.sum(), corrected.sum()
    if new_total > 0:
        corrected *= total / new_total
    stats["largest_effects"] = dict(sorted(effects.items(), key=lambda kv: -abs(kv[1]))[:5])
    return corrected, stats
