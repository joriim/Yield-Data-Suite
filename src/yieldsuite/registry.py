"""Machine and calibration registry (versioned machine profiles).

In the server deployment these live in PostgreSQL. Locally they are loaded from
the field-year YAML file. Every product records the profile version it used.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import date


@dataclass(frozen=True)
class MachineProfile:
    machine_id: str
    version: int = 1
    valid_from: date | None = None
    valid_to: date | None = None
    header_width_m: float | None = None
    tau_d_s: float | None = None  # grain flow dead time, header to sensor
    tau_m_s: float | None = None  # moisture delay
    tau_c_s: float | None = None  # mixing time constant
    antenna_offset_m: tuple[float, float] = (0.0, 0.0)  # (forward, right) to header centre
    curve_coefficients: tuple[float, ...] = field(default_factory=tuple)
    k_factor: float | None = None  # load reconciliation factor for the season
    fill_finish_s: float | None = None  # seconds flagged at pass ends; default tau_c + 2
    speed_limits_m_s: tuple[float, float] = (0.3, 4.0)

    def param_hash(self) -> str:
        """Stable hash of all parameters, stored with every derived product."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    @classmethod
    def from_dict(cls, d: dict) -> MachineProfile:
        """Build from a dict; keys that are not profile fields (e.g. version metadata
        such as created/actor/reason) are ignored."""
        names = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in names}
        for key in ("valid_from", "valid_to"):
            if isinstance(d.get(key), str):
                d[key] = date.fromisoformat(d[key])
        if "antenna_offset_m" in d:
            d["antenna_offset_m"] = tuple(float(v) for v in d["antenna_offset_m"])
        if "speed_limits_m_s" in d:
            d["speed_limits_m_s"] = tuple(float(v) for v in d["speed_limits_m_s"])
        if "curve_coefficients" in d:
            d["curve_coefficients"] = tuple(float(v) for v in d["curve_coefficients"])
        return cls(**d)
