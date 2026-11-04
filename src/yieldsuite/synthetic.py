"""Synthetic field-year with a known true yield, for tests and ``yieldsuite demo``.

A combine with a 6 m header drives north-south passes over a 240 x 160 m field
in EPSG:3067. The logged mass flow is delayed by tau_d, the monitor reads 7 %
high, and grain is logged at 18 % moisture. Scale tickets carry the true mass,
so the chain must recover the true yield pattern and level.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

X0, Y0 = 400_000.0, 6_950_000.0
FIELD_W, FIELD_H = 240.0, 160.0
HEADER_W = 6.0
SPEED = 1.5  # m/s
TAU_D = 12.0  # s
ANTENNA_BEHIND_HEADER = 3.2  # m
MOISTURE = 18.0
MONITOR_GAIN = 1.07
REF_MOISTURE = 14.0


def true_yield(x, y):
    """t/ha at 14 % moisture."""
    u = (np.asarray(x) - X0) / 200.0
    v = (np.asarray(y) - Y0) / 150.0
    return 7.0 + 1.5 * np.sin(2 * np.pi * u) + 1.0 * np.cos(2 * np.pi * v)


@dataclass
class SyntheticField:
    log: pd.DataFrame
    tickets: pd.DataFrame
    true_mass_ref_kg: float
    cut: pd.DataFrame | None = None  # second-by-second cut position: t (s from start), x, y


def make_field(
    seed: int = 0, noise: float = 0.03, n_spikes: int = 15, pass_gain_sd: float = 0.0
) -> SyntheticField:
    """``pass_gain_sd`` adds a multiplicative monitor gain per pass (stripes)."""
    rng = np.random.default_rng(seed)
    n_pass = int(FIELD_W // HEADER_W)
    cut_rows: list[tuple[float, float, float]] = []
    rows = []
    t = 0.0
    true_mass = 0.0
    for p in range(n_pass):
        x = X0 + HEADER_W * (p + 0.5)
        north = p % 2 == 0
        heading = 0.0 if north else 180.0
        sign = 1.0 if north else -1.0
        y_start = Y0 if north else Y0 + FIELD_H
        n_cut = int(FIELD_H // SPEED)  # stay inside the field
        n_tail = int(TAU_D) + 4  # keep driving with header up while the elevator empties
        pass_gain = float(np.exp(rng.normal(0, pass_gain_sd))) if pass_gain_sd else 1.0
        cut_hist = []  # wet mass flow cut each second, at monitor gain
        for k in range(n_cut + n_tail):
            hy = y_start + sign * SPEED * (k + 1)  # header centre at end of second k
            cutting = k < n_cut
            if cutting:
                y_mid = hy - sign * SPEED / 2
                ref_kg_s = true_yield(x, y_mid) / 10.0 * SPEED * HEADER_W
                cut_rows.append((t + k, x, y_mid))
                true_mass += ref_kg_s
                wet = ref_kg_s * (100 - REF_MOISTURE) / (100 - MOISTURE)
            else:
                wet = 0.0
            cut_hist.append(wet)
            delayed = cut_hist[k - int(TAU_D)] if k >= TAU_D else 0.0
            logged = delayed * MONITOR_GAIN * pass_gain * (1 + rng.normal(0, noise))
            rows.append(
                {
                    "Time": t + k,
                    "Easting": x,
                    "Northing": hy - sign * ANTENNA_BEHIND_HEADER,
                    "Mass_Flow_kg_s": max(logged, 0.0),
                    "Moisture_pct": MOISTURE + rng.normal(0, 0.2),
                    "Swath_m": HEADER_W if cutting else 0.0,
                    "Speed_kmh": SPEED * 3.6,
                    "Heading_deg": heading,
                    "Header": 1 if cutting else 0,
                    "GPS_Fix": 4,
                }
            )
        t += n_cut + n_tail + 20  # headland turn, logging paused
    log = pd.DataFrame(rows)
    spikes = rng.choice(np.flatnonzero(log["Header"] == 1), n_spikes, replace=False)
    log.loc[spikes, "Mass_Flow_kg_s"] *= 4.0
    log["Time"] = (pd.Timestamp("2026-08-10T10:00:00Z") + pd.to_timedelta(log["Time"], "s")).map(
        lambda ts: ts.isoformat()
    )
    wet_total = true_mass * (100 - REF_MOISTURE) / (100 - MOISTURE)
    n_loads = 6
    tickets = pd.DataFrame(
        {
            "machine_id": "combine-a",
            "mass_kg": np.full(n_loads, wet_total / n_loads),
            "moisture_pct": MOISTURE,
        }
    )
    cut = pd.DataFrame(cut_rows, columns=["t", "x", "y"])
    return SyntheticField(log, tickets, true_mass, cut)


def write_demo(out_dir: str | Path, seed: int = 0, **field_kwargs) -> Path:
    """Write log, scale tickets and a field-year config; return the config path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    f = make_field(seed, **field_kwargs)
    f.log.to_csv(out / "combine-a_2026.csv", index=False)
    f.tickets.to_csv(out / "scale_tickets.csv", index=False)
    config = {
        "field_id": "synthetic-01",
        "year": 2026,
        "crs": "EPSG:3067",
        "reference_moisture_pct": REF_MOISTURE,
        "grid": {"resolution_m": 20, "origin": [0, 0]},
        "machines": [
            {
                "machine_id": "combine-a",
                "header_width_m": HEADER_W,
                "tau_d_s": TAU_D,
                "tau_m_s": TAU_D,
                "antenna_offset_m": [ANTENNA_BEHIND_HEADER, 0.0],
            }
        ],
        "harvest_logs": [
            {
                "path": "combine-a_2026.csv",
                "machine_id": "combine-a",
                "format": "csv",
                "columns": {
                    "time": "Time",
                    "x": "Easting",
                    "y": "Northing",
                    "mass_flow": "Mass_Flow_kg_s",
                    "moisture": "Moisture_pct",
                    "swath": "Swath_m",
                    "speed": "Speed_kmh",
                    "heading": "Heading_deg",
                    "header": "Header",
                    "gnss_quality": "GPS_Fix",
                },
                "speed_unit": "km/h",
            }
        ],
        "scale_tickets": "scale_tickets.csv",
        "output_dir": "output",
    }
    path = out / "field.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def image_features(
    grid, dates=("2026-06-10", "2026-06-30", "2026-07-15"), noise: float = 0.01, seed: int = 0
):
    """Synthetic Sentinel-2 feature table (long format) whose NDVI follows the true yield.

    Pixel noise only; no passes, no delay: the image is the independent spatial
    reference the L5 corrections need.
    """
    rng = np.random.default_rng(seed)
    rows, cols = np.divmod(np.arange(grid.ny * grid.nx), grid.nx)
    x = grid.xmin + (cols + 0.5) * grid.resolution
    y = grid.ymax - (rows + 0.5) * grid.resolution
    base = 0.2 + 0.06 * true_yield(x, y)
    recs = []
    for k, d in enumerate(dates):
        nd = base + 0.02 * k + rng.normal(0, noise, base.size)
        for cid in range(base.size):
            recs.append((cid, d, "NDVI", nd[cid]))
            recs.append((cid, d, "NDRE", 0.6 * nd[cid]))
    return pd.DataFrame.from_records(recs, columns=["cell", "date", "index", "value"])


def multi_year_layers(
    n_years: int = 5,
    seed: int = 0,
    nx: int = 40,
    ny: int = 30,
    res: float = 20.0,
    unstable_patch: bool = True,
):
    """Synthetic yield maps of several years with known management zones.

    Three zones (relative productivity 80 / 100 / 120 %) plus an unstable patch
    whose yield swings +-25 % between years. Each year has its own level, a
    smooth year-specific pattern (4 %) and cell noise (5 %).
    Returns (grid, layers, true_zone (1..3), unstable_mask).
    """
    from .l4.grid import Grid

    rng = np.random.default_rng(seed)
    grid = Grid(X0, Y0 + ny * res, res, nx, ny)
    rows, cols = np.mgrid[0:ny, 0:nx]
    u, v = cols / nx, rows / ny
    s = np.sin(2.2 * np.pi * u) + 0.8 * np.cos(1.6 * np.pi * v)
    zone = np.digitize(s, np.quantile(s, [1 / 3, 2 / 3])) + 1
    level = np.choose(zone - 1, [0.8, 1.0, 1.2])
    unstable = (u > 0.75) & (v < 0.3) if unstable_patch else np.zeros(zone.shape, bool)
    layers = []
    for year in range(n_years):
        mean = rng.uniform(5, 9)
        phase = rng.uniform(0, 2 * np.pi)
        pattern = 1 + 0.04 * np.sin(2 * np.pi * (u + v) + phase)
        swing = np.where(unstable, 1 + 0.25 * (-1) ** year, 1.0)
        y = mean * level * pattern * swing * (1 + rng.normal(0, 0.05, zone.shape))
        layers.append(y)
    return grid, layers, zone, unstable


START = datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)


def true_protein(x, y):
    """% of dry matter: higher where yield is low (dilution) plus an east-west trend."""
    u = (np.asarray(x) - X0) / FIELD_W
    return 12.5 - 0.4 * (true_yield(x, y) - 7.0) + 0.8 * np.cos(np.pi * u)


def quality_export(
    field: SyntheticField,
    interval_s: int = 5,
    delay_s: float = TAU_D,
    bias: float = 0.5,
    noise: float = 0.15,
    seed: int = 0,
):
    """GrainSense-like export (one row per measurement) and lab samples.

    Each measurement averages the grain cut during the previous interval,
    seen ``delay_s`` later at the analyzer, with an analyzer bias. Lab samples
    are the true mean protein of 60 s windows. Returns (export, lab).
    """
    rng = np.random.default_rng(seed)
    cut = field.cut
    p_true = true_protein(cut["x"].to_numpy(), cut["y"].to_numpy())
    ct = cut["t"].to_numpy(float)
    rows = []
    for s in np.arange(ct.min() + delay_s + interval_s, ct.max() + delay_s + 1, interval_s):
        sel = (ct > s - delay_s - interval_s) & (ct <= s - delay_s)
        if sel.sum() < interval_s // 2 + 1:
            continue  # no grain flowing through the analyzer
        rows.append(
            {
                "Measured at": (START + timedelta(seconds=float(s))).isoformat(),
                "Protein (%)": round(float(p_true[sel].mean()) + bias + rng.normal(0, noise), 2),
                "Moisture (%)": round(MOISTURE + rng.normal(0, 0.3), 2),
                "Oil (%)": round(2.1 + rng.normal(0, 0.05), 2),
                "Session": "Session 001",
            }
        )
    export = pd.DataFrame(rows)
    lab = []
    for a in np.quantile(ct, [0.1, 0.35, 0.6, 0.85]):
        sel = (ct >= a) & (ct < a + 60)
        lab.append(
            {
                "start": (START + timedelta(seconds=float(a + delay_s))).isoformat(),
                "end": (START + timedelta(seconds=float(a + 60 + delay_s))).isoformat(),
                "protein_lab_pct": round(float(p_true[sel].mean()), 2),
            }
        )
    return export, pd.DataFrame(lab)


def _reflectance_from_ndvi(ndvi, red=0.05):
    nir = red * (1 + ndvi) / (1 - ndvi)
    return red, nir


def drone_ortho(
    path,
    ndvi_offset: float = 0.15,
    ndvi_gain: float = 0.07,
    pixel_m: float = 1.0,
    noise: float = 0.01,
    seed: int = 0,
):
    """Mavic 3M-like reflectance orthophoto (bands G, R, RE, NIR + alpha), EPSG:3067.

    Its NDVI relates to yield with other coefficients than the Sentinel-2
    features (0.2 + 0.06 x yield), so harmonisation has something to correct.
    """
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    nx, ny = int(FIELD_W / pixel_m), int(FIELD_H / pixel_m)
    xs = X0 + (np.arange(nx) + 0.5) * pixel_m
    ys = Y0 + FIELD_H - (np.arange(ny) + 0.5) * pixel_m
    xx, yy = np.meshgrid(xs, ys)
    ndvi = np.clip(
        ndvi_offset + ndvi_gain * true_yield(xx, yy) + rng.normal(0, noise, xx.shape), 0.05, 0.95
    )
    red, nir = _reflectance_from_ndvi(ndvi)
    ndre = 0.6 * ndvi
    rededge = nir * (1 - ndre) / (1 + ndre)
    green = np.full_like(ndvi, 0.08)
    alpha = np.full(ndvi.shape, 255, dtype="float32")
    alpha[:, :3] = 0  # a strip outside the flight
    stack = np.stack([green, np.full_like(ndvi, red), rededge, nir, alpha]).astype("float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=nx,
        height=ny,
        count=5,
        dtype="float32",
        crs="EPSG:3067",
        transform=from_origin(X0, Y0 + FIELD_H, pixel_m, pixel_m),
    ) as dst:
        dst.write(stack)
    return path


def planetscope_sr(
    path,
    mask_path,
    ndvi_offset: float = 0.18,
    ndvi_gain: float = 0.065,
    pixel_m: float = 3.0,
    noise: float = 0.01,
    seed: int = 1,
):
    """PlanetScope SuperDove-like 8-band SR (x10 000) with a cloud and a UDM2-style mask."""
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(seed)
    nx, ny = int(FIELD_W / pixel_m), int(FIELD_H / pixel_m)
    xs = X0 + (np.arange(nx) + 0.5) * pixel_m
    ys = Y0 + FIELD_H - (np.arange(ny) + 0.5) * pixel_m
    xx, yy = np.meshgrid(xs, ys)
    ndvi = np.clip(
        ndvi_offset + ndvi_gain * true_yield(xx, yy) + rng.normal(0, noise, xx.shape), 0.05, 0.95
    )
    red, nir = _reflectance_from_ndvi(ndvi)
    bands = [np.full_like(ndvi, v) for v in (0.04, 0.05, 0.07, 0.08, 0.07)]  # cb, b, gI, g, y
    bands += [np.full_like(ndvi, red), nir * (1 - 0.6 * ndvi) / (1 + 0.6 * ndvi), nir]
    stack = np.stack(bands)
    cloud = (xx > X0 + 160) & (yy > Y0 + 100)  # north-east corner under a cloud
    stack[:, cloud] = 0.5  # bright and flat: would wreck NDVI if not masked
    dn = np.round(stack * 10000).astype("uint16")
    tf = from_origin(X0, Y0 + FIELD_H, pixel_m, pixel_m)
    profile = {"driver": "GTiff", "width": nx, "height": ny, "crs": "EPSG:3067", "transform": tf}
    with rasterio.open(path, "w", count=8, dtype="uint16", **profile) as dst:
        dst.write(dn)
    with rasterio.open(mask_path, "w", count=1, dtype="uint8", **profile) as dst:
        dst.write((~cloud).astype("uint8")[None])
    return path, mask_path
