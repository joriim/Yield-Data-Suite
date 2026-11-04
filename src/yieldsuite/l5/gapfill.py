"""Gap filling by regression kriging.

    Y_hat(x0) = f(V(x0)) + sum_i lambda_i r(x_i),
    sigma^2(x0) = sigma_f^2 + sigma_OK^2(x0)

Cells with good coverage keep their measured yield. Cells with low coverage
(or only flagged records) get the prediction, are labelled as filled, and are
never used to train f.
"""

from __future__ import annotations

import warnings

import numpy as np

SOURCE_MEASURED = 1
SOURCE_FILLED = 2
SOURCE_NONE = 0


def _krige_residuals(xy_train, r, xy_target, max_dist, resolution, max_points, seed):
    try:
        import gstools as gs
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("residual kriging needs gstools: pip install yieldsuite[fusion]") from exc

    rng = np.random.default_rng(seed)
    if len(r) > max_points:
        pick = rng.choice(len(r), max_points, replace=False)
        xy_train, r = xy_train[pick], r[pick]
    x, y = xy_train[:, 0], xy_train[:, 1]
    sample_var = float(np.var(r)) or 1e-12
    model = gs.Exponential(dim=2, var=sample_var, len_scale=max_dist / 4)
    try:
        edges = np.linspace(0, max_dist / 2, 16)
        centres, gamma = gs.vario_estimate((x, y), r, edges)
        ok = np.isfinite(gamma)
        model.set_arg_bounds(
            var=[1e-12, 5 * sample_var],
            len_scale=[resolution, max_dist],
            nugget=[0, 5 * sample_var],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model.fit_variogram(centres[ok], gamma[ok], nugget=True)
    except Exception:  # noqa: BLE001 - keep the fixed fallback variogram
        model = gs.Exponential(dim=2, var=sample_var, len_scale=max_dist / 4)
    krig = gs.krige.Ordinary(model, cond_pos=(x, y), cond_val=r)
    field, var = krig((xy_target[:, 0], xy_target[:, 1]), mesh_type="unstructured", return_var=True)
    variogram = {
        "model": "exponential",
        "var": float(model.var),
        "len_scale_m": float(model.len_scale),
        "nugget": float(model.nugget),
    }
    return np.asarray(field), np.maximum(np.asarray(var), 0.0), variogram


def gap_fill(
    yield_obs,
    prediction,
    train_mask,
    xy,
    sigma_f: float,
    resolution: float,
    residual: str = "kriging",
    max_points: int = 2000,
    seed: int = 0,
) -> dict:
    """Combine measured yield and model prediction for every cell.

    All arrays are flat (one value per cell). ``xy`` holds cell centres (n, 2).
    Returns flat arrays ``yield``, ``source``, ``sigma``, ``residual`` and a
    ``variogram`` dict (or None).
    """
    y = np.asarray(yield_obs, dtype=float)
    pred = np.asarray(prediction, dtype=float)
    train = np.asarray(train_mask, dtype=bool) & np.isfinite(y) & np.isfinite(pred)
    target = ~train & np.isfinite(pred)
    xy = np.asarray(xy, dtype=float)

    out_y = np.where(train, y, np.nan)
    sigma = np.full(y.shape, np.nan)
    resid_field = np.zeros(y.shape)
    variogram = None

    if target.any():
        if residual == "kriging" and train.sum() >= 10:
            extent = xy[train].max(axis=0) - xy[train].min(axis=0)
            max_dist = float(max(np.hypot(*extent), 3 * resolution))
            rk, rvar, variogram = _krige_residuals(
                xy[train],
                y[train] - pred[train],
                xy[target],
                max_dist,
                resolution,
                max_points,
                seed,
            )
            resid_field[target] = rk
            sigma[target] = np.sqrt(sigma_f**2 + rvar)
        elif residual in ("kriging", "none"):
            sigma[target] = sigma_f
        else:
            raise ValueError(f"unknown residual method {residual!r}")
        out_y[target] = pred[target] + resid_field[target]

    source = np.full(y.shape, SOURCE_NONE, dtype=np.int8)
    source[train] = SOURCE_MEASURED
    source[target] = SOURCE_FILLED
    return {
        "yield": out_y,
        "source": source,
        "sigma": sigma,
        "residual": resid_field,
        "variogram": variogram,
    }
