"""High-level tasks for front ends (QGIS plugin, CLI, MCP).

Each task takes plain values (paths, strings, numbers), runs the core and
returns a dict with output paths and a short human-readable ``message``. No
QGIS, Slack or server imports here, so the tasks are testable on their own and
the QGIS plugin stays a thin wrapper (its results match the CLI exactly).
"""

from __future__ import annotations

from pathlib import Path

from .config import load_config
from .levels import Level


def inspect(path: str, crs: str = "EPSG:3067", **options) -> dict:
    from .ingest.inspect import format_report, inspect_file

    options = {k: v for k, v in options.items() if v not in (None, "", {}, [])}
    result = inspect_file(path, crs=crs, **options)
    return {
        "report": result["report"],
        "config_yaml": result["config_yaml"],
        "message": format_report(result),
    }


def run_chain(config_path: str, to_level: str = "L4") -> dict:
    from .pipeline import run

    cfg = load_config(config_path)
    problems = cfg.validate()
    if problems:
        raise ValueError("; ".join(problems))
    s = run(cfg, Level.parse(to_level))
    lines = [
        f"{cfg.field_id}/{cfg.year}: "
        + ("done " + ", ".join(lv.value for lv in s.completed) if s.ok else f"FAILED: {s.error}")
    ]
    if s.k_factors:
        lines.append("k factors: " + ", ".join(f"{m}={k:.3f}" for m, k in s.k_factors.items()))
    if s.flagged_pct is not None:
        lines.append(f"flagged: {s.flagged_pct:.1f} % of harvesting records")
    if s.mass_ok is not None:
        lines.append("mass check: " + ("OK" if s.mass_ok else "FAIL"))
    lines += s.notes
    return {
        "ok": s.ok,
        "error": s.error,
        "outputs": dict(s.outputs),
        "product_class": s.product_class.value,
        "message": "\n".join(lines),
    }


def estimate_delay(config_path: str) -> dict:
    from .pipeline import estimate_delays

    res = estimate_delays(load_config(config_path))
    lines = []
    for mid, d in res.items():
        lines.append(
            f"{mid}: profile {d['profile_tau_d_s']} s, estimated {d['estimated_tau_d_s']} s, "
            f"used {d['used_tau_d_s']} s ({d['used_source']})"
            + ("" if d["identifiable"] else f" - {d['reason']}")
        )
    return {"machines": res, "message": "\n".join(lines)}


def fuse(
    config_path: str,
    l4_path: str,
    method: str = "linear",
    residual: str = "kriging",
    features_path: str | None = None,
) -> dict:
    from .l4.io import read_bands
    from .l5.run import run_sentinel2_fusion

    cfg = load_config(config_path)
    l4, grid, _ = read_bands(l4_path)
    out = run_sentinel2_fusion(
        cfg, l4, grid, features_path=features_path, method=method, residual=residual
    )
    fit = out.result.model_card["fit"]
    cells = out.result.model_card["cells"]
    msg = (
        f"L5 written: R² {fit['r2']:.2f}, CV RMSE {fit.get('cv_rmse', float('nan')):.2f} "
        f"t/ha, filled {100 * cells['filled_share']:.0f} % of cells"
    )
    return {"outputs": {"L5": str(out.raster), "model_card": str(out.model_card)}, "message": msg}


def zones(
    layer_paths: list[str],
    out_dir: str,
    covariate_paths: list[str] = (),
    k_max: int = 6,
    stem: str = "zones",
    crs: str | None = None,
) -> dict:
    from .l4.io import read_bands
    from .zones import make_zones, place, union_grid, write_zones

    if len(layer_paths) < 2:
        raise ValueError("zones need yield maps of at least two years")
    layers, grids = [], []
    for p in layer_paths:
        b, g, c = read_bands(p)
        layers.append(b["yield"])
        grids.append(g)
        crs = crs or c
    covs = []
    for p in covariate_paths or []:
        b, g, _ = read_bands(p, require_yield=False)
        covs += [(arr, g) for arr in b.values()]  # every band is a covariate
        grids.append(g)
    target = union_grid(grids)
    res = make_zones(
        [place(a, g, target) for a, g in zip(layers, grids, strict=False)],
        target,
        [place(a, g, target) for a, g in covs],
        k_range=range(2, k_max + 1),
        names=[Path(p).stem for p in layer_paths],
    )
    files = write_zones(res, target, crs, out_dir, stem)
    s = res.summary
    lines = [
        f"{s['k']} zones from {len(layers)} layers ({s['spatial_components']} spatial "
        f"components); zones explain {100 * (s['variance_explained_by_zones'] or 0):.0f} % "
        "of the variance of mean relative yield"
    ]
    lines += [
        f"zone {z['zone']}: {z['area_ha']:.2f} ha, {z['mean_relative_yield_pct']:.0f} %"
        for z in s["zones"]
    ]
    return {"outputs": files, "summary": s, "message": "\n".join(lines)}


def weather(config_path: str, l4_path: str | None = None, refresh: bool = False) -> dict:
    import json

    from .l4.io import read_bands
    from .weather.run import run_agroclimate, write_agroclimate

    cfg = load_config(config_path)
    grid = read_bands(l4_path)[1] if l4_path else None
    result = run_agroclimate(cfg, grid, refresh=refresh)
    files = write_agroclimate(cfg, result, grid)
    return {
        "outputs": files,
        "summary": result["summary"],
        "message": json.dumps(result["summary"], indent=2, default=str),
    }


def parcel_boundary(
    parcel_ids: list[str],
    out_path: str,
    year: int | None = None,
    url: str | None = None,
    layer: str | None = None,
    id_column: str | None = None,
) -> dict:
    """Field boundary by parcel number from the parcel-register WFS, written as GeoPackage."""
    import geopandas as gpd

    from .public.parcels import RUOKAVIRASTO_WFS, ParcelService

    service = ParcelService(url or RUOKAVIRASTO_WFS)
    choice = service.choose(year, layer, id_column)
    fc = service.fetch(parcel_ids, choice)
    gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:3067")
    gdf.to_file(out_path, driver="GPKG")
    lines = [f"layer {choice.typename} ({choice.reason}); id attribute {choice.id_column}"]
    lines += [f"{r[choice.id_column]}: {r.geometry.area / 1e4:.2f} ha" for _, r in gdf.iterrows()]
    return {
        "outputs": {"boundary": str(out_path)},
        "layer": choice.typename,
        "id_column": choice.id_column,
        "message": "\n".join(lines),
    }
