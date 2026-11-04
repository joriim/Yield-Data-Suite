"""Runs the chain for one field-year.

Stages are wired one by one as they are implemented; unimplemented stages raise
``NotImplementedError`` so a run fails loudly instead of producing a half map.
In the server deployment each stage becomes a Dagster asset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import stages
from .config import FieldYearConfig
from .levels import Level, ProductClass


@dataclass
class Stage:
    target: Level
    description: str
    run: Callable[[FieldYearConfig, dict], None] | None = None

    @property
    def implemented(self) -> bool:
        return self.run is not None


@dataclass
class RunSummary:
    field_id: str
    year: int
    level: Level
    product_class: ProductClass
    completed: list[Level] = field(default_factory=list)
    flagged_pct: float | None = None
    k_factors: dict[str, float] = field(default_factory=dict)
    mass_ok: bool | None = None
    viewer_url: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


STAGES: list[Stage] = [
    Stage(Level.L1A, "read harvest logs (CSV; ISOXML not yet)", stages.stage_ingest),
    Stage(Level.L1B, "mass flow (vendor exports already at L1B)", stages.stage_mass_flow),
    Stage(
        Level.L2,
        "flow delay, antenna offset, moisture standardisation, effective area, yield",
        stages.stage_georeference,
    ),
    Stage(Level.L3, "quality flags, load reconciliation per machine", stages.stage_clean),
    Stage(Level.L4, "mass apportioning to the fixed grid, quality bands", stages.stage_grid),
    Stage(Level.L5, "Sentinel-2 fusion via openEO: image model, gap fill", stages.stage_fusion),
]


def plan(config: FieldYearConfig, to_level: Level, from_level: Level = Level.L1A) -> list[Stage]:
    if config.product_class is ProductClass.RELATIVE:
        # Imagery only: there are no records, only the L5-style image products.
        return [s for s in STAGES if s.target is Level.L5]
    return [s for s in STAGES if from_level <= s.target <= to_level]


def run(config: FieldYearConfig, to_level: Level, from_level: Level = Level.L1A) -> RunSummary:
    summary = RunSummary(config.field_id, config.year, to_level, config.product_class)
    state: dict = {}
    for stage in plan(config, to_level, from_level):
        if not stage.implemented:
            summary.error = f"{stage.target.value} not implemented yet: {stage.description}"
            return summary
        try:
            stage.run(config, state)
        except Exception as exc:  # noqa: BLE001 - reported to the user and to Slack
            summary.error = f"{stage.target.value} failed: {exc}"
            summary.outputs = state.get("outputs", {})
            return summary
        summary.completed.append(stage.target)
    summary.flagged_pct = state.get("flagged_pct")
    summary.k_factors = state.get("k_factors", {})
    summary.mass_ok = state.get("mass_ok")
    summary.outputs = state.get("outputs", {})
    summary.notes = list(state.get("notes", []))
    return summary


def estimate_delays(config: FieldYearConfig) -> dict:
    """Run ingest and L2 only and report, per machine, the flow delay the data
    suggest next to the profile value. Nothing is written."""
    state: dict = {}
    stages.stage_ingest(config, state)
    stages.stage_mass_flow(config, state)
    stages.stage_georeference(config, state)
    out = {}
    for mid, d in state["qa"]["delays"].items():
        prof = config.machines.get(mid)
        est = d.get("estimate", {})
        curve = d.get("estimate_curve", {})
        out[mid] = {
            "profile_tau_d_s": prof.tau_d_s if prof else None,
            "estimated_tau_d_s": est.get("tau_d_s"),
            "identifiable": est.get("identifiable", False),
            "reason": est.get(
                "reason", "vendor already applied the delay" if d.get("vendor_applied") else ""
            ),
            "opposite_pass_pairs": est.get("n_pairs", 0),
            "contrast": est.get("contrast"),
            "used_tau_d_s": d["tau_d_s"],
            "used_source": d.get("source", "vendor"),
            "curve": {str(k): v for k, v in curve.items()},
        }
    return out
