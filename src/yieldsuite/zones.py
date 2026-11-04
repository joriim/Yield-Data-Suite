"""Management zones from multi-year yield maps (design doc, correction 4: KM-sPC).

1. Each year's map becomes relative yield (% of that year's field mean), so
   years and crops with different levels count equally.
2. Optional covariates (elevation, ECa, soil, image history) are added.
3. Layers are standardised and reduced with MULTISPATI-PCA: components that
   maximise variance x spatial autocorrelation (Moran's I with 8-neighbour,
   row-standardised weights), as in Córdoba et al. (2013, 2016).
4. Fuzzy k-means (m = 1.3) on the spatial components for k = 2..k_max; k is
   chosen where the fuzziness performance index (FPI) and the normalised
   classification entropy (NCE) are both low.

Also returned: temporal stability classes (mean relative yield and its
coefficient of variation across years) and per-zone yield statistics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .l4.grid import Grid

STABILITY = {1: "high and stable", 2: "low and stable", 3: "unstable"}


@dataclass
class ZoneResult:
    zones: np.ndarray  # (ny, nx), 0 = no data, 1..k ordered by mean relative yield
    membership: np.ndarray  # (ny, nx) highest membership
    stability: np.ndarray  # (ny, nx) 0 no data, see STABILITY
    mean_relative: np.ndarray
    cv_relative: np.ndarray
    k: int
    summary: dict = field(default_factory=dict)


# --- grid stacking ---------------------------------------------------------------


def union_grid(grids: list[Grid]) -> Grid:
    r = grids[0].resolution
    if any(abs(g.resolution - r) > 1e-9 for g in grids):
        raise ValueError("layers have different cell sizes; re-run them on one grid")
    for g in grids:
        if (
            abs(((g.xmin - grids[0].xmin) / r) % 1) > 1e-6
            or abs(((g.ymax - grids[0].ymax) / r) % 1) > 1e-6
        ):
            raise ValueError("layers are not on the same grid origin (design rule 5)")
    xmin = min(g.xmin for g in grids)
    ymax = max(g.ymax for g in grids)
    xmax = max(g.xmin + g.nx * r for g in grids)
    ymin = min(g.ymax - g.ny * r for g in grids)
    return Grid(xmin, ymax, r, round((xmax - xmin) / r), round((ymax - ymin) / r))


def place(values: np.ndarray, grid: Grid, target: Grid) -> np.ndarray:
    out = np.full((target.ny, target.nx), np.nan)
    c0 = round((grid.xmin - target.xmin) / target.resolution)
    r0 = round((target.ymax - grid.ymax) / target.resolution)
    out[r0 : r0 + grid.ny, c0 : c0 + grid.nx] = values
    return out


def relative_yield(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, float)
    m = np.nanmean(y)
    return 100.0 * y / m if np.isfinite(m) and m > 0 else np.full_like(y, np.nan)


# --- MULTISPATI-PCA ----------------------------------------------------------------


def _neighbour_mean(X: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Row-standardised 8-neighbour average of X (ny, nx, p) over valid cells."""
    ny, nx, p = X.shape
    acc = np.zeros_like(X)
    cnt = np.zeros((ny, nx))
    Xz = np.where(valid[..., None], X, 0.0)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            src_r = slice(max(dr, 0), ny + min(dr, 0))
            dst_r = slice(max(-dr, 0), ny + min(-dr, 0))
            src_c = slice(max(dc, 0), nx + min(dc, 0))
            dst_c = slice(max(-dc, 0), nx + min(-dc, 0))
            acc[dst_r, dst_c] += Xz[src_r, src_c]
            cnt[dst_r, dst_c] += valid[src_r, src_c]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cnt[..., None] > 0, acc / cnt[..., None], 0.0)


def multispati_pca(X: np.ndarray, valid: np.ndarray, min_share: float = 0.8):
    """Spatial principal components of standardised layers X (ny, nx, p).

    Returns scores (n_valid, q), eigenvalues (all), and q.
    """
    WX = _neighbour_mean(X, valid)
    A = X[valid]
    B = WX[valid]
    n = A.shape[0]
    S = (A.T @ B + B.T @ A) / (2.0 * n)
    vals, vecs = np.linalg.eigh(S)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    pos = vals[vals > 0]
    if pos.size == 0:
        raise ValueError("no spatially structured variation in the layers")
    share = np.cumsum(pos) / pos.sum()
    q = int(np.searchsorted(share, min_share) + 1)
    return A @ vecs[:, :q], vals, q


# --- fuzzy k-means -----------------------------------------------------------------


def fuzzy_kmeans(
    Z: np.ndarray,
    k: int,
    m: float = 1.3,
    n_init: int = 5,
    max_iter: int = 300,
    tol: float = 1e-6,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray, float]:
    rng = np.random.default_rng(seed)
    best = None
    for _ in range(n_init):
        C = Z[rng.choice(len(Z), k, replace=False)]
        for _ in range(max_iter):
            d = np.linalg.norm(Z[:, None, :] - C[None], axis=2) + 1e-12
            u = 1.0 / np.sum((d[:, :, None] / d[:, None, :]) ** (2 / (m - 1)), axis=2)
            um = u**m
            C_new = (um.T @ Z) / um.sum(axis=0)[:, None]
            if np.max(np.abs(C_new - C)) < tol:
                C = C_new
                break
            C = C_new
        d = np.linalg.norm(Z[:, None, :] - C[None], axis=2)
        obj = float(np.sum((u**m) * d**2))
        if best is None or obj < best[2]:
            best = (u, C, obj)
    return best


def fpi_nce(u: np.ndarray) -> tuple[float, float]:
    n, k = u.shape
    F = float(np.sum(u**2) / n)
    H = float(-np.sum(u * np.log(np.clip(u, 1e-12, 1))) / n)
    return 1 - (k * F - 1) / (k - 1), H / np.log(k)


# --- main --------------------------------------------------------------------------


def make_zones(
    yield_layers: list[np.ndarray],
    grid: Grid,
    covariates: list[np.ndarray] = (),
    k_range=range(2, 7),
    m: float = 1.3,
    min_layer_share: float = 0.7,
    stability_cv: float = 15.0,
    names: list[str] | None = None,
    seed: int = 0,
) -> ZoneResult:
    """Zones from yield layers (one per year) and covariates, all on ``grid``."""
    if len(yield_layers) < 2:
        raise ValueError("zones need at least two years of yield maps")
    rel = np.stack([relative_yield(y) for y in yield_layers], axis=-1)
    layers = [rel[..., i] for i in range(rel.shape[-1])] + [
        np.asarray(c, float) for c in covariates
    ]
    X = np.stack(layers, axis=-1)
    finite = np.isfinite(X)
    need_years = max(2, int(np.ceil(min_layer_share * rel.shape[-1])))
    valid = (np.isfinite(rel).sum(axis=-1) >= need_years) & finite[..., rel.shape[-1] :].all(-1)
    if valid.sum() < 20:
        raise ValueError(f"only {int(valid.sum())} cells have enough layers")
    mu = np.nanmean(np.where(valid[..., None], X, np.nan), axis=(0, 1))
    sd = np.nanstd(np.where(valid[..., None], X, np.nan), axis=(0, 1))
    sd = np.where(sd > 0, sd, 1.0)
    Xs = np.where(finite, (X - mu) / sd, 0.0)  # missing years -> layer mean

    scores, eig, q = multispati_pca(Xs, valid)
    scan = []
    for k in k_range:
        if k >= valid.sum():
            break
        u, _, obj = fuzzy_kmeans(scores, k, m, seed=seed)
        fpi, nce = fpi_nce(u)
        scan.append({"k": k, "fpi": fpi, "nce": nce, "u": u})
    # both indices low: smallest sum of ranks, ties -> fewer zones
    fpi_rank = np.argsort(np.argsort([s["fpi"] for s in scan]))
    nce_rank = np.argsort(np.argsort([s["nce"] for s in scan]))
    pick = min(range(len(scan)), key=lambda i: (fpi_rank[i] + nce_rank[i], scan[i]["k"]))
    chosen = scan[pick]
    u = chosen["u"]
    labels = np.argmax(u, axis=1)

    mean_rel = np.nanmean(rel, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cv_rel = 100.0 * np.nanstd(rel, axis=-1) / mean_rel
    # order zones by mean relative yield: zone 1 = lowest
    zone_means = [np.nanmean(mean_rel[valid][labels == j]) for j in range(chosen["k"])]
    order = np.argsort(zone_means)
    relabel = np.empty_like(order)
    relabel[order] = np.arange(1, chosen["k"] + 1)
    zones = np.zeros((grid.ny, grid.nx), dtype=np.int16)
    zones[valid] = relabel[labels]
    memb = np.full((grid.ny, grid.nx), np.nan)
    memb[valid] = u.max(axis=1)

    stab = np.zeros((grid.ny, grid.nx), dtype=np.int16)
    ok = valid & np.isfinite(cv_rel)
    stab[ok & (cv_rel < stability_cv) & (mean_rel >= 100)] = 1
    stab[ok & (cv_rel < stability_cv) & (mean_rel < 100)] = 2
    stab[ok & (cv_rel >= stability_cv)] = 3

    names = names or [f"year{i + 1}" for i in range(rel.shape[-1])]
    per_zone = []
    total_var = np.nanvar(mean_rel[valid])
    within = 0.0
    for z in range(1, chosen["k"] + 1):
        sel = zones == z
        mr = mean_rel[sel]
        within += np.nanvar(mr) * sel.sum()
        per_zone.append(
            {
                "zone": z,
                "cells": int(sel.sum()),
                "area_ha": float(sel.sum() * grid.cell_area / 1e4),
                "mean_relative_yield_pct": float(np.nanmean(mr)),
                "relative_yield_by_layer": {
                    n: float(np.nanmean(rel[..., i][sel])) for i, n in enumerate(names)
                },
                "mean_membership": float(np.nanmean(memb[sel])),
            }
        )
    summary = {
        "method": "MULTISPATI-PCA + fuzzy k-means (KM-sPC)",
        "fuzziness_m": m,
        "layers": names + [f"covariate{i + 1}" for i in range(len(covariates))],
        "spatial_components": q,
        "spatial_eigenvalues": [float(v) for v in eig],
        "k": chosen["k"],
        "k_scan": [
            {"k": s["k"], "fpi": round(s["fpi"], 4), "nce": round(s["nce"], 4)} for s in scan
        ],
        "variance_explained_by_zones": float(1 - within / (total_var * valid.sum()))
        if total_var > 0
        else None,
        "zones": per_zone,
        "stability": {
            "cv_threshold_pct": stability_cv,
            "classes": {STABILITY[c]: int((stab == c).sum()) for c in STABILITY},
        },
        "cells": int(valid.sum()),
    }
    return ZoneResult(zones, memb, stab, mean_rel, cv_rel, chosen["k"], summary)


def write_zones(result: ZoneResult, grid: Grid, crs: str, out_dir, stem: str) -> dict:
    """GeoTIFF (zones, membership, stability, mean and CV of relative yield),
    zone polygons as GeoPackage, summary JSON."""
    import json
    from pathlib import Path

    from .l4.io import write_cog

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = ("zone", "membership", "stability", "mean_relative_yield", "cv_relative_yield")
    bands = {
        "zone": np.where(result.zones > 0, result.zones, np.nan),
        "membership": result.membership,
        "stability": np.where(result.stability > 0, result.stability, np.nan),
        "mean_relative_yield": result.mean_relative,
        "cv_relative_yield": result.cv_relative,
    }
    files = {"raster": str(write_cog(bands, grid, crs, out / f"{stem}.tif", names=names))}
    try:
        import geopandas as gpd
        import rasterio.features
        from rasterio.transform import Affine
        from shapely.geometry import shape

        polys = [
            {"zone": int(v), "geometry": shape(g)}
            for g, v in rasterio.features.shapes(
                result.zones.astype(np.int16),
                mask=result.zones > 0,
                transform=Affine(*grid.transform),
            )
        ]
        gdf = gpd.GeoDataFrame(polys, crs=crs).dissolve(by="zone").reset_index()
        gdf["area_ha"] = gdf.area / 1e4
        p = out / f"{stem}.gpkg"
        gdf.to_file(p, driver="GPKG")
        files["polygons"] = str(p)
    except ImportError:
        pass
    p = out / f"{stem}.json"
    p.write_text(json.dumps(result.summary, indent=2))
    files["summary"] = str(p)
    return files
