"""Scale tickets: weighed loads for reconciliation.

CSV columns: ``mass_kg`` (required), ``machine_id`` (optional; without it one
factor is used for the field), ``moisture_pct`` (optional; converts the weight
to the reference moisture).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..l1.moisture import to_reference_moisture


def read_scale_tickets(path: str | Path, reference_pct: float) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "mass_kg" not in df.columns:
        raise ValueError(f"{path}: needs a mass_kg column")
    mass = df["mass_kg"].to_numpy(dtype=float)
    if "moisture_pct" in df.columns:
        mc = df["moisture_pct"].to_numpy(dtype=float)
        mass = np.where(np.isfinite(mc), to_reference_moisture(mass, mc, reference_pct), mass)
    machine = df["machine_id"].astype(str) if "machine_id" in df.columns else "*"
    return pd.DataFrame({"machine_id": machine, "mass_ref_kg": mass})
