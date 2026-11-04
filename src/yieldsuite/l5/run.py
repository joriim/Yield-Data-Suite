"""Run the Sentinel-2 L5 step for a field-year config: fetch (or reuse) features, fuse, write."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..config import FieldYearConfig
from ..l4.grid import Grid
from .fuse import FusionResult, fuse
from .indices import DEFAULT_INDICES
from .raster_features import raster_features
from .sentinel2 import S2Request, fetch_features, load_features, save_features


@dataclass
class L5Outputs:
    result: FusionResult
    raster: Path | None
    model_card: Path
    features: Path


def s2_request(config: FieldYearConfig) -> S2Request:
    src = config.sentinel2
    if src is None:
        raise ValueError("config has no imagery entry with kind: sentinel2")
    return S2Request(
        start=src.start or f"{config.year}-04-15",
        end=src.end or f"{config.year}-09-15",
        indices=tuple(src.indices or DEFAULT_INDICES),
        max_cloud_cover=src.max_cloud_cover,
        backend=src.backend,
    )


def run_sentinel2_fusion(
    config: FieldYearConfig,
    l4: dict,
    grid: Grid,
    features_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    method: str = "linear",
    residual: str = "kriging",
    batch: bool = False,
    connection=None,
    write_raster: bool = True,
    records=None,
    polygons=None,
) -> L5Outputs:
    request = s2_request(config) if config.sentinel2 is not None else None
    out = Path(out_dir or config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"L5_{config.field_id}_{config.year}"

    parts = []
    if request is not None:
        features_path = Path(features_path) if features_path else out / f"{stem}_s2_features.csv"
        if features_path.exists():
            s2 = load_features(features_path)
        else:
            s2 = fetch_features(
                grid, config.crs, request, connection=connection, workdir=out, batch=batch
            )
            save_features(s2, features_path)
        parts.append(s2.assign(source="sentinel2"))
    local_reports = []
    for img in config.imagery:
        if img.kind not in ("drone", "planetscope"):
            continue
        df, rep = raster_features(
            img.path,
            grid,
            config.crs,
            img.date,
            source=img.kind,
            sensor=img.sensor or ("mavic3m" if img.kind == "drone" else "superdove"),
            bands=img.bands or None,
            scale=img.scale,
            mask_path=img.mask,
            mask_clear_band=img.mask_clear_band,
        )
        parts.append(df)
        local_reports.append(rep)
    if not parts:
        raise ValueError("no imagery configured (sentinel2, drone or planetscope)")
    features = pd.concat(parts, ignore_index=True)
    min_valid_share = config.sentinel2.min_valid_share if config.sentinel2 else 0.8

    corrections = _record_corrections(
        l4, grid, features, records, polygons, method, min_valid_share
    )
    if corrections.get("l4") is not None:
        l4 = corrections.pop("l4")
    else:
        corrections.pop("l4", None)

    result = fuse(
        l4,
        grid,
        features,
        method=method,
        residual=residual,
        min_valid_share=min_valid_share,
        request=request,
    )
    result.model_card.update(
        field_id=config.field_id,
        year=config.year,
        crs=config.crs,
        product_class=config.product_class.value,
    )
    result.model_card["record_corrections"] = corrections
    result.model_card["local_imagery"] = local_reports
    card_path = out / f"{stem}_model_card.json"
    card_path.write_text(json.dumps(result.model_card, indent=2, default=str))

    # Agroclimate and RUE: only when the crop calendar is known; a weather
    # failure (e.g. no network) is reported, not fatal for L5.
    if config.crop is not None and config.crop.sowing_date:
        try:
            from ..weather.run import run_agroclimate, write_agroclimate

            agro = run_agroclimate(config, grid, l5_bands=result.bands, features_long=features)
            result.model_card["agroclimate"] = agro["summary"]
            result.model_card["agroclimate_files"] = write_agroclimate(config, agro, grid)
        except Exception as exc:  # noqa: BLE001
            result.model_card["agroclimate_error"] = str(exc)
        card_path.write_text(json.dumps(result.model_card, indent=2, default=str))

    raster = None
    if write_raster:
        from ..l4.io import write_cog
        from ..schema import L5_BANDS

        raster = write_cog(result.bands, grid, config.crs, out / f"{stem}.tif", names=L5_BANDS)
    return L5Outputs(result, raster, card_path, features_path)


def _record_corrections(l4, grid, features, records, polygons, method, min_valid_share) -> dict:
    """Corrections 1-2 of the design document, when L3 records are available."""
    if records is None or polygons is None:
        return {
            "applied": False,
            "reason": "L3 records not available (L5 run from an L4 raster only)",
        }
    import numpy as np

    from ..l4.grid import apportion_mass, check_mass_conservation
    from .records import RecordView
    from .shift import residual_shift
    from .stripes import level_pass_stripes

    # Image prediction f(V) as the spatial reference: fitted on the L4 grid,
    # without kriging (the residual field would carry the stripes back in).
    prelim = fuse(
        l4, grid, features, method=method, residual="none", min_valid_share=min_valid_share
    )
    reference = prelim.bands["prediction"]
    view = RecordView.from_records(records)
    out: dict = {"applied": True, "residual_shift": residual_shift(view, grid, reference)}
    corrected, stripes = level_pass_stripes(view, grid, reference)
    out["pass_stripes"] = stripes
    if stripes.get("applied"):
        use = view.area > 0
        bands = apportion_mass(
            np.asarray(polygons, dtype=object)[use],
            corrected[use],
            grid,
            flags=view.flags[use],
            pass_ids=view.pass_key[use],
        )
        clean_total = float(corrected[use & (view.flags == 0)].sum())
        stripes["mass_conserved"] = check_mass_conservation(bands["mass"], clean_total)
        stripes["field_total_kg"] = {
            "before": float(view.mass.sum()),
            "after": float(corrected.sum()),
        }
        out["l4"] = bands
    return out
