"""Stage implementations for ``pipeline.run``. Each stage reads and extends ``state``.

state keys: records (DataFrame), polygons (effective record polygons),
k_factors, flagged_pct, l4, grid, mass_ok, qa, outputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import FieldYearConfig
from .ingest import read_logs
from .ingest.scale import read_scale_tickets
from .l1.moisture import to_reference_moisture, valid_moisture
from .l2.delay import estimate_flow_delay
from .l2.georef import effective_polygons, header_position, record_polygons
from .l2.track import (
    derive_heading,
    new_segments,
    segment_lengths,
    segment_passes,
    shift_signal,
)
from .l2.yield_ import yield_t_ha
from .l3.filters import (
    flag_fill_finish,
    flag_global_outliers,
    flag_local_outliers,
    flag_speed,
)
from .l3.reconcile import reconciliation_factor
from .l4.grid import Grid, apportion_mass, check_mass_conservation
from .levels import Level
from .registry import MachineProfile
from .schema import Flag, set_flag

DEFAULT_TAU_D_S = 12.0


def _profile(config: FieldYearConfig, machine_id: str) -> MachineProfile:
    return config.machines.get(machine_id) or MachineProfile(machine_id)


def _stem(config: FieldYearConfig, level: str) -> str:
    return f"{level}_{config.field_id}_{config.year}"


# --- L1A: ingest -----------------------------------------------------------------


def stage_ingest(config: FieldYearConfig, state: dict) -> None:
    records, reports = read_logs(config.harvest_logs, config.crs)
    state["records"] = records
    warnings = [f"{Path(r['source']).name}: {w}" for r in reports for w in r["warnings"]]
    state["qa"] = {"records": int(len(records)), "ingest": reports, "warnings": warnings}
    for mid in records["machine_id"].unique():
        if mid not in config.machines:
            warnings.append(f"machine {mid!r} has no profile: default delays and offsets used")


# --- L1B: mass flow --------------------------------------------------------------


def stage_mass_flow(config: FieldYearConfig, state: dict) -> None:
    """Vendor exports enter at L1B (mass flow already computed); nothing to convert.

    Moisture standardisation needs moisture aligned to the same grain, so it is
    done in L2 after the delay shift.
    """
    rec = state["records"]
    bad = rec["mass_flow_wet"].isna()
    if bad.all():
        raise ValueError("no mass flow values in the harvest logs")


# --- L2: georeferenced yield -----------------------------------------------------


def stage_georeference(config: FieldYearConfig, state: dict) -> None:
    rec = state["records"]
    parts = []
    delays = {}
    for mid, df in rec.groupby("machine_id", sort=False):
        df = df.sort_values("t").copy()
        prof = _profile(config, mid)
        t = df["t"].to_numpy()
        starts = new_segments(t)
        dt = np.r_[np.nan, np.diff(t)]
        med = np.nanmedian(dt) if len(t) > 1 else 1.0
        dt = np.where(np.isfinite(dt) & (dt > 0) & (dt <= 3 * med), dt, med)

        heading = df["heading"].to_numpy()
        if np.isnan(heading).all():
            heading = derive_heading(df["x"], df["y"], starts)
        fwd, right = prof.antenna_offset_m
        hx, hy = header_position(df["x"], df["y"], heading, fwd, right)

        mass_wet = df["mass_flow_wet"].to_numpy()
        moisture = df["moisture"].to_numpy()
        speed = df["speed"].to_numpy()
        length = segment_lengths(hx, hy, speed, dt, starts)
        if np.isnan(speed).all():
            speed = length / dt
        harvesting = df["header_down"].to_numpy()
        swath = np.where(harvesting, df["swath"].to_numpy(), 0.0)
        pass_id = segment_passes(t, harvesting, heading)

        already = Level.parse(df["entry_level"].iloc[0]) >= Level.L2
        info: dict = {"vendor_applied": already}
        if already:
            tau_d = 0.0
        else:
            # Compare record centres, not the front edge where the header ended:
            # on opposite passes the front edges are offset in opposite directions.
            cx = hx - np.sin(heading) * length / 2
            cy = hy - np.cos(heading) * length / 2
            est = estimate_flow_delay(
                t, mass_wet, dt, length * swath, cx, cy, heading, pass_id, swath
            )
            info["estimate"] = {
                k: v for k, v in est.as_dict().items() if k not in ("candidates_s", "curve")
            }
            info["estimate_curve"] = dict(zip(est.candidates_s, est.curve, strict=False))
            if prof.tau_d_s is not None:
                tau_d, info["source"] = prof.tau_d_s, "machine profile"
                if est.identifiable and abs(est.tau_d_s - prof.tau_d_s) > 3.0:
                    state["qa"]["warnings"].append(
                        f"{mid}: data suggest tau_d {est.tau_d_s:g} s, profile has "
                        f"{prof.tau_d_s:g} s; check in QGIS and update the profile"
                    )
            elif est.identifiable:
                tau_d, info["source"] = est.tau_d_s, "estimated from opposite passes"
                state["qa"]["warnings"].append(
                    f"{mid}: tau_d {tau_d:g} s estimated from the data (no profile value); "
                    "store it in the machine profile once confirmed"
                )
            else:
                tau_d, info["source"] = DEFAULT_TAU_D_S, "default"
                state["qa"]["warnings"].append(
                    f"{mid}: no tau_d_s in profile and not identifiable from the data "
                    f"({est.reason}); used default {DEFAULT_TAU_D_S:g} s"
                )
        tau_m = 0.0 if already else (prof.tau_m_s if prof.tau_m_s is not None else tau_d)
        if tau_d:
            mass_wet = shift_signal(t, mass_wet, tau_d)
        if tau_m and not np.isnan(moisture).all():
            moisture = shift_signal(t, moisture, tau_m)
        delays[mid] = {"tau_d_s": tau_d, "tau_m_s": tau_m, **info}

        has_mc = np.isfinite(moisture)
        mass_ref = np.where(
            has_mc,
            to_reference_moisture(mass_wet, moisture, config.reference_moisture_pct),
            mass_wet,
        )

        df["dt"] = dt
        df["hx"], df["hy"], df["heading"] = hx, hy, heading
        df["speed"] = speed
        df["moisture_aligned"] = moisture
        df["mass_flow_ref"] = mass_ref
        df["mass_kg"] = np.nan_to_num(mass_ref * dt, nan=0.0)
        df["mass_missing"] = ~np.isfinite(mass_ref)
        df["length"] = length
        df["swath_used"] = swath
        df["pass_id"] = pass_id
        df["applied_delay"] = tau_d
        parts.append(df)

    rec = pd.concat(parts).sort_values(["t", "machine_id"], kind="stable", ignore_index=True)
    polys = record_polygons(
        rec["hx"].to_numpy(),
        rec["hy"].to_numpy(),
        rec["heading"].to_numpy(),
        rec["length"].to_numpy(),
        rec["swath_used"].to_numpy(),
    )
    eff = effective_polygons(polys)  # time order across all machines
    rec["nominal_area"] = [p.area for p in polys]
    rec["effective_area"] = [p.area for p in eff]
    rec["yield"] = yield_t_ha(rec["mass_kg"], rec["effective_area"])
    rec["pass_key"] = rec["machine_id"].astype(str) + ":" + rec["pass_id"].astype(str)
    state["records"] = rec
    state["polygons"] = eff
    state["qa"]["delays"] = delays


# --- L3: reconciliation and flags ------------------------------------------------


def stage_clean(config: FieldYearConfig, state: dict) -> None:
    rec = state["records"]
    flags = np.zeros(len(rec), dtype=np.int32)
    harvesting = rec["header_down"].to_numpy() & (rec["effective_area"].to_numpy() > 0)
    flags = set_flag(flags, ~harvesting, Flag.HEADER_UP)
    n_on = int(harvesting.sum())

    for mid, idx in rec.groupby("machine_id").groups.items():
        idx = np.asarray(idx)
        prof = _profile(config, mid)
        sub = rec.loc[idx]
        on = harvesting[idx]
        n_ff = prof.fill_finish_s if prof.fill_finish_s is not None else (prof.tau_c_s or 3.0) + 2.0
        ff = np.zeros(len(idx), bool)
        if on.any():
            ff[on] = flag_fill_finish(sub["pass_id"].to_numpy()[on], sub["t"].to_numpy()[on], n_ff)
        flags[idx] = set_flag(flags[idx], ff | sub["mass_missing"].to_numpy(), Flag.FILL_FINISH)
        vmin, vmax = prof.speed_limits_m_s
        sp = flag_speed(sub["speed"].to_numpy(), sub["t"].to_numpy(), vmin, vmax, max_accel=None)
        flags[idx] = set_flag(flags[idx], sp & on, Flag.SPEED)

    mc = rec["moisture_aligned"].to_numpy()
    flags = set_flag(flags, np.isfinite(mc) & ~valid_moisture(mc) & harvesting, Flag.MOISTURE)
    ratio = rec["effective_area"].to_numpy() / np.maximum(rec["nominal_area"].to_numpy(), 1e-9)
    flags = set_flag(flags, harvesting & (ratio < 0.5), Flag.SWATH)
    gq = rec["gnss_quality"].to_numpy()
    flags = set_flag(flags, harvesting & np.isfinite(gq) & (gq < 4), Flag.GNSS)

    if config.boundary is not None:
        from .public.boundary import edge_mask, load_boundary

        boundary = load_boundary(config.boundary, config.crs)
        buf = config.edge_buffer_m
        if buf is None:
            buf = float(np.nanmedian(rec["swath_used"].to_numpy()[harvesting])) if n_on else 0.0
        h = rec["heading"].to_numpy()
        half = rec["length"].to_numpy() / 2
        cx = rec["hx"].to_numpy() - np.sin(h) * half
        cy = rec["hy"].to_numpy() - np.cos(h) * half
        edge = edge_mask(boundary, cx, cy, buf) & harvesting
        flags = set_flag(flags, edge, Flag.FIELD_EDGE)
        state["qa"]["field_edge"] = {
            "buffer_m": buf,
            "records": int(edge.sum()),
            "boundary_area_ha": boundary.area / 1e4,
        }

    candidates = flags == 0
    if candidates.sum() > 10:
        out = np.zeros(len(rec), bool)
        out[candidates] = flag_global_outliers(rec["yield"].to_numpy()[candidates])
        flags = set_flag(flags, out, Flag.GLOBAL_OUTLIER)
    # Local spatial outliers among the records still clean (Vega et al. 2019).
    candidates = flags == 0
    if candidates.sum() > 30:
        h = rec["heading"].to_numpy()
        half = rec["length"].to_numpy() / 2
        cx = rec["hx"].to_numpy() - np.sin(h) * half
        cy = rec["hy"].to_numpy() - np.cos(h) * half
        radius = config.local_outlier_radius_m
        loc = np.zeros(len(rec), bool)
        both, moran, robust = flag_local_outliers(
            cx[candidates],
            cy[candidates],
            rec["yield"].to_numpy()[candidates],
            radius,
            return_parts=True,
        )
        loc[candidates] = both
        flags = set_flag(flags, loc, Flag.LOCAL_OUTLIER)
        state["qa"]["local_outliers"] = {
            "radius_m": radius,
            "records": int(loc.sum()),
            "by_local_morans_i": int(moran.sum()),
            "by_neighbour_median": int(robust.sum()),
            "method": "negative local Moran's I (199 permutations, p < 0.01, |z - lag| > 2 sd)"
            " or > 4 robust SD from the neighbours' median",
        }
    rec["flags"] = flags

    # Reconciliation uses all harvested mass, flagged records included, because
    # they still carried grain to the tank. Outlier records are the exception:
    # their logged value is a sensor error, so they count with the field median
    # yield over their effective area instead of their logged mass.
    outlier = (flags & int(Flag.GLOBAL_OUTLIER | Flag.LOCAL_OUTLIER)) != 0
    median_y = (
        float(np.nanmedian(rec["yield"].to_numpy()[flags == 0])) if (flags == 0).any() else 0.0
    )
    monitor_mass = np.where(
        outlier, median_y * rec["effective_area"].to_numpy() / 10.0, rec["mass_kg"].to_numpy()
    )
    rec["mass_kg_for_reconciliation"] = monitor_mass
    k_factors: dict[str, float] = {}
    if config.scale_tickets is not None:
        tickets = read_scale_tickets(config.scale_tickets, config.reference_moisture_pct)
        monitor = rec.groupby("machine_id")["mass_kg_for_reconciliation"].sum()
        generic = tickets["machine_id"] == "*"
        if generic.any() and not generic.all():
            raise ValueError(
                "scale tickets mix machine-specific and unassigned rows; assign every "
                "ticket to a machine or none of them"
            )
        if generic.all():
            k = reconciliation_factor(tickets["mass_ref_kg"], monitor.sum())
            k_factors = {m: k for m in monitor.index}
        else:
            for m in monitor.index:
                sel = tickets["machine_id"] == m
                if not sel.any():
                    raise ValueError(f"no scale tickets for machine {m!r}")
                k_factors[m] = reconciliation_factor(tickets.loc[sel, "mass_ref_kg"], monitor[m])
    else:
        for m in rec["machine_id"].unique():
            prof = _profile(config, m)
            k_factors[m] = prof.k_factor if prof.k_factor is not None else 1.0
    k = rec["machine_id"].map(k_factors).to_numpy(dtype=float)
    rec["mass_kg_reconciled"] = monitor_mass * k
    rec["yield"] = rec["yield"].to_numpy() * k

    n_h = int(harvesting.sum())
    flagged = int(((flags != 0) & harvesting).sum())
    state["k_factors"] = k_factors
    state["flagged_pct"] = 100.0 * flagged / n_h if n_h else 0.0
    state["qa"]["flags"] = {
        f.name: int(((flags & int(f)) != 0).sum()) for f in Flag if f is not Flag.NONE
    }
    state["qa"]["harvesting_records"] = n_h
    state["qa"]["k_factors"] = k_factors
    state["qa"]["reconciled_mass_kg"] = float(rec["mass_kg_reconciled"].sum())


# --- L4: grid --------------------------------------------------------------------


def stage_grid(config: FieldYearConfig, state: dict) -> None:
    import shapely

    rec = state["records"]
    polys = state["polygons"]
    areas = rec["effective_area"].to_numpy()
    use = areas > 0
    grid = Grid.from_bounds(
        shapely.total_bounds(polys[use]), config.grid_resolution_m, config.grid_origin
    )
    flags = rec["flags"].to_numpy()
    mass = rec["mass_kg_reconciled"].to_numpy()
    bands = apportion_mass(
        polys[use], mass[use], grid, flags=flags[use], pass_ids=rec["pass_key"].to_numpy()[use]
    )
    clean_mass = float(mass[use & (flags == 0)].sum())
    mass_ok = check_mass_conservation(bands["mass"], clean_mass, rtol=1e-6)
    state["l4"], state["grid"], state["mass_ok"] = bands, grid, mass_ok
    state["qa"].update(
        grid={
            "xmin": grid.xmin,
            "ymax": grid.ymax,
            "resolution": grid.resolution,
            "nx": grid.nx,
            "ny": grid.ny,
        },
        mass_conservation={
            "ok": mass_ok,
            "clean_mass_kg": clean_mass,
            "cell_mass_kg": float(np.nansum(bands["mass"])),
        },
        field_mean_yield_t_ha=float(np.nanmean(bands["yield"])),
    )
    _write_outputs(config, state)
    if config.quality is not None and config.quality.sources:
        from .quality.pipeline import run_quality

        q = run_quality(config, state)
        state.setdefault("notes", []).append(
            f"quality: protein {q['field_mean_protein_pct_dm']:.1f} % DM "
            f"({'lab-calibrated' if q['lab_calibration'].get('applied') else 'uncalibrated'})"
            if q["field_mean_protein_pct_dm"] is not None
            else "quality: no matched measurements"
        )


def _write_outputs(config: FieldYearConfig, state: dict) -> None:
    out = Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rec = state["records"]
    written = {}
    cols = [c for c in rec.columns if c not in ("hx", "hy")]
    try:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(rec[cols], geometry=list(state["polygons"]), crs=config.crs)
        path = out / f"{_stem(config, 'L3')}_records.parquet"
        gdf.to_parquet(path)
    except ImportError:
        path = out / f"{_stem(config, 'L3')}_records.csv"
        rec[cols].to_csv(path, index=False)
    written["L3"] = str(path)
    try:
        from .l4.io import write_cog

        written["L4"] = str(
            write_cog(state["l4"], state["grid"], config.crs, out / f"{_stem(config, 'L4')}.tif")
        )
    except ImportError:
        state["qa"]["warnings"].append("rasterio not installed: L4 GeoTIFF not written")
    qa_path = out / f"{_stem(config, 'L4')}_qa.json"
    state["qa"]["product_class"] = config.product_class.value
    qa_path.write_text(json.dumps(state["qa"], indent=2, default=str))
    written["qa"] = str(qa_path)
    state.setdefault("outputs", {}).update(written)


# --- L5 --------------------------------------------------------------------------


def stage_fusion(config: FieldYearConfig, state: dict) -> None:
    if "l4" not in state:
        raise RuntimeError(
            "L5 needs an L4 yield grid; imagery-only relative maps are not built yet"
        )
    if not config.imagery:
        raise ValueError("L5 needs imagery: sentinel2, drone or planetscope")
    from .l5.run import run_sentinel2_fusion

    res = run_sentinel2_fusion(
        config,
        state["l4"],
        state["grid"],
        records=state.get("records"),
        polygons=state.get("polygons"),
    )
    state["l5"] = res
    rc = res.result.model_card.get("record_corrections", {})
    notes = state.setdefault("notes", [])
    shift = rc.get("residual_shift", {})
    if shift.get("significant"):
        notes.append(
            f"image suggests tau_d {shift['suggested_extra_delay_s']:+g} s: check the "
            "machine profile and re-run"
        )
    stripes = rc.get("pass_stripes", {})
    if stripes.get("applied"):
        notes.append(f"pass stripes levelled (sigma_b {stripes['sigma_b']:.1%})")
    state.setdefault("outputs", {}).update(L5=str(res.raster), model_card=str(res.model_card))
