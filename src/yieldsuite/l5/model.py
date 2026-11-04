"""Yield-image model Y_g = f(V_g) + r_g, fitted per field-year.

Relations differ between years, so a model is never carried across years
without recalibration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class YieldImageModel:
    method: str
    feature_names: list[str]
    stats: dict = field(default_factory=dict)
    _coef: np.ndarray | None = None
    _rf: object | None = None

    def predict(self, V) -> np.ndarray:
        X = np.asarray(V, dtype=float)
        out = np.full(X.shape[0], np.nan)
        ok = np.isfinite(X).all(axis=1)
        if ok.any():
            if self.method == "linear":
                out[ok] = np.c_[np.ones(ok.sum()), X[ok]] @ self._coef
            else:
                out[ok] = self._rf.predict(X[ok])
        return out


def _fit(method: str, X: np.ndarray, y: np.ndarray, names, random_state: int):
    if method == "linear":
        coef, *_ = np.linalg.lstsq(np.c_[np.ones(len(y)), X], y, rcond=None)
        return YieldImageModel("linear", list(names), _coef=coef)
    if method == "rf":
        try:
            from sklearn.ensemble import RandomForestRegressor
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError("random forest needs: pip install yieldsuite[fusion]") from exc
        rf = RandomForestRegressor(
            n_estimators=300, min_samples_leaf=3, random_state=random_state, n_jobs=-1
        )
        rf.fit(X, y)
        return YieldImageModel("rf", list(names), _rf=rf)
    raise ValueError(f"unknown method {method!r}; use 'linear' or 'rf'")


def spatial_block_folds(rows, cols, block_cells: int, k: int, seed: int = 0) -> np.ndarray:
    """Assign cells to k folds by square blocks, not at random (neighbours are correlated)."""
    br = np.asarray(rows) // block_cells
    bc = np.asarray(cols) // block_cells
    blocks = br * 100_003 + bc
    uniq = np.unique(blocks)
    rng = np.random.default_rng(seed)
    fold_of_block = dict(zip(uniq, rng.permutation(len(uniq)) % k, strict=True))
    return np.array([fold_of_block[b] for b in blocks])


def fit_yield_image_model(
    y,
    V,
    train_mask,
    rows=None,
    cols=None,
    method: str = "linear",
    block_cells: int = 5,
    k: int = 5,
    random_state: int = 0,
) -> YieldImageModel:
    """Fit f on training cells and report in-sample and spatial-block CV error.

    ``rows``/``cols`` (cell positions) enable spatial block cross-validation.
    """
    names = list(getattr(V, "columns", [f"v{i}" for i in range(np.shape(V)[1])]))
    X = np.asarray(V, dtype=float)
    y = np.asarray(y, dtype=float)
    train = np.asarray(train_mask, dtype=bool) & np.isfinite(y) & np.isfinite(X).all(axis=1)
    n = int(train.sum())
    if n < max(10, X.shape[1] + 2):
        raise ValueError(f"only {n} training cells for {X.shape[1]} features")

    model = _fit(method, X[train], y[train], names, random_state)
    pred = model.predict(X[train])
    resid = y[train] - pred
    ss_tot = np.sum((y[train] - y[train].mean()) ** 2)
    model.stats = {
        "n_train": n,
        "r2": float(1 - np.sum(resid**2) / ss_tot) if ss_tot > 0 else float("nan"),
        "rmse": float(np.sqrt(np.mean(resid**2))),
    }

    if rows is not None and cols is not None:
        r = np.asarray(rows)[train]
        c = np.asarray(cols)[train]
        folds = spatial_block_folds(r, c, block_cells, k, seed=random_state)
        Xt, yt = X[train], y[train]
        cv_pred = np.full(n, np.nan)
        for f in range(k):
            test = folds == f
            if test.all() or not test.any():
                continue
            m = _fit(method, Xt[~test], yt[~test], names, random_state)
            cv_pred[test] = m.predict(Xt[test])
        ok = np.isfinite(cv_pred)
        err = cv_pred[ok] - yt[ok]
        model.stats.update(
            cv_rmse=float(np.sqrt(np.mean(err**2))) if ok.any() else float("nan"),
            cv_bias=float(np.mean(err)) if ok.any() else float("nan"),
            cv_block_cells=block_cells,
        )
    return model
