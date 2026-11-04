"""Record-level view of L3 used by the L5 corrections (centres, mass, area, passes)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..l4.grid import Grid


@dataclass
class RecordView:
    cx: np.ndarray
    cy: np.ndarray
    heading: np.ndarray
    speed: np.ndarray
    mass: np.ndarray  # reconciled mass, kg
    area: np.ndarray  # effective area, m2
    clean: np.ndarray
    pass_key: np.ndarray
    flags: np.ndarray

    @classmethod
    def from_records(cls, rec: pd.DataFrame) -> RecordView:
        h = rec["heading"].to_numpy(float)
        half = rec["length"].to_numpy(float) / 2
        area = rec["effective_area"].to_numpy(float)
        mass = rec["mass_kg_reconciled"].to_numpy(float)
        flags = rec["flags"].to_numpy()
        return cls(
            cx=rec["hx"].to_numpy(float) - np.sin(h) * half,
            cy=rec["hy"].to_numpy(float) - np.cos(h) * half,
            heading=h,
            speed=rec["speed"].to_numpy(float),
            mass=mass,
            area=area,
            clean=(flags == 0) & (area > 0) & (mass > 0),
            pass_key=rec["pass_key"].to_numpy(),
            flags=flags,
        )


def cell_index(grid: Grid, x, y) -> np.ndarray:
    """Flat cell id for points, -1 outside the grid."""
    col = np.floor((np.asarray(x) - grid.xmin) / grid.resolution).astype(int)
    row = np.floor((grid.ymax - np.asarray(y)) / grid.resolution).astype(int)
    inside = (col >= 0) & (col < grid.nx) & (row >= 0) & (row < grid.ny)
    return np.where(inside, row * grid.nx + col, -1)


def sample_bilinear(grid: Grid, values: np.ndarray, x, y) -> np.ndarray:
    """Bilinear interpolation of a cell-centred raster at points.

    A cell-constant lookup would make every pass inside a cell look offset by
    the within-cell yield gradient; interpolating keeps linear gradients.
    Falls back to the nearest cell where a neighbour is missing.
    """
    v = np.asarray(values, float).reshape(grid.ny, grid.nx)
    r = grid.resolution
    fc = (np.asarray(x, float) - grid.xmin) / r - 0.5
    fr = (grid.ymax - np.asarray(y, float)) / r - 0.5
    c0 = np.clip(np.floor(fc).astype(int), 0, grid.nx - 1)
    r0 = np.clip(np.floor(fr).astype(int), 0, grid.ny - 1)
    c1 = np.clip(c0 + 1, 0, grid.nx - 1)
    r1 = np.clip(r0 + 1, 0, grid.ny - 1)
    tc = np.clip(fc - c0, 0, 1)
    tr = np.clip(fr - r0, 0, 1)
    q = [v[r0, c0], v[r0, c1], v[r1, c0], v[r1, c1]]
    out = q[0] * (1 - tc) * (1 - tr) + q[1] * tc * (1 - tr) + q[2] * (1 - tc) * tr + q[3] * tc * tr
    near = cell_index(grid, x, y)
    nearest = np.where(near >= 0, v.ravel()[np.clip(near, 0, None)], np.nan)
    return np.where(np.isfinite(out), out, nearest)
