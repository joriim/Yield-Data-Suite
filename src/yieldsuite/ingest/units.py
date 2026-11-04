"""Unit detection and conversion to the suite's SI units.

Canonical units: mass_flow kg/s, yield t/ha, moisture %, swath/distance/elevation m,
speed m/s, duration s, heading rad.
"""

from __future__ import annotations

import re

import numpy as np

LB = 0.45359237  # kg
LB_PER_AC_TO_T_PER_HA = LB / 4046.8564224 * 10.0  # 1 lb/ac in t/ha

# Typical test weights [lb/bu] (USDA); used only for bu/ac yields.
BUSHEL_WEIGHT_LB = {
    "corn": 56.0,
    "maize": 56.0,
    "wheat": 60.0,
    "soybean": 60.0,
    "soybeans": 60.0,
    "barley": 48.0,
    "oats": 32.0,
    "oat": 32.0,
    "canola": 50.0,
    "rapeseed": 50.0,
    "rye": 56.0,
    "sorghum": 56.0,
    "sunflower": 24.0,
}

# canonical field -> {unit token: factor to canonical unit}
FACTORS: dict[str, dict[str, float]] = {
    "mass_flow": {
        "kg/s": 1.0,
        "lb/s": LB,
        "t/h": 1000 / 3600,
        "kg/h": 1 / 3600,
        "kg/min": 1 / 60,
        "g/s": 1e-3,
        "mg/s": 1e-6,
    },
    "yield": {
        "t/ha": 1.0,
        "kg/ha": 1e-3,
        "dt/ha": 0.1,
        "lb/ac": LB_PER_AC_TO_T_PER_HA,
        "t/ac": 2.4710538,
        "kg/m2": 10.0,
        "mg/m2": 1e-5,
    },
    "moisture": {"%": 1.0, "ppm": 1e-4, "fraction": 100.0},
    "length": {"m": 1.0, "ft": 0.3048, "in": 0.0254, "cm": 0.01, "mm": 1e-3},
    "speed": {"m/s": 1.0, "km/h": 1 / 3.6, "mph": 0.44704, "mm/s": 1e-3, "kn": 0.514444},
    "duration": {"s": 1.0, "ms": 1e-3, "min": 60.0},
    "heading": {"deg": np.pi / 180, "rad": 1.0},
}
QUANTITY = {
    "mass_flow": "mass_flow",
    "dry_mass_flow": "mass_flow",
    "yield_wet": "yield",
    "yield_dry": "yield",
    "yield_volume": "yield",
    "moisture": "moisture",
    "swath": "length",
    "distance": "length",
    "elevation": "length",
    "speed": "speed",
    "duration": "duration",
    "heading": "heading",
}

# Header text patterns -> unit token, checked in order.
_HEADER_UNITS: list[tuple[str, str]] = [
    (r"lbs?\s*/\s*s(ec)?\b", "lb/s"),
    (r"kg\s*/\s*s(ec)?\b", "kg/s"),
    (r"\bt\s*/\s*h\b", "t/h"),
    (r"kg\s*/\s*h\b", "kg/h"),
    (r"kg\s*/\s*min", "kg/min"),
    (r"mg\s*/\s*s\b", "mg/s"),
    (r"bu\s*/\s*ac", "bu/ac"),
    (r"lbs?\s*/\s*ac", "lb/ac"),
    (r"\bt\s*/\s*ac", "t/ac"),
    (r"kg\s*/\s*ha", "kg/ha"),
    (r"\b(dt|dz|q)\s*/\s*ha", "dt/ha"),
    (r"\bt\s*/\s*ha", "t/ha"),
    (r"km\s*/\s*h|kph|kmh", "km/h"),
    (r"\bmph\b", "mph"),
    (r"m\s*/\s*s\b", "m/s"),
    (r"mm\s*/\s*s\b", "mm/s"),
    (r"\bppm\b", "ppm"),
    (r"%|percent|pct", "%"),
    (r"\bdeg|°|degree", "deg"),
    (r"\brad\b", "rad"),
    (r"\(\s*ft\s*\)|\bfeet\b|_ft\b|\bft\b", "ft"),
    (r"\(\s*in\s*\)|\binch|_in\b", "in"),
    (r"\(\s*mm\s*\)|_mm\b", "mm"),
    (r"\(\s*cm\s*\)|_cm\b", "cm"),
    (r"\(\s*m\s*\)|_m\b", "m"),
    (r"\(\s*ms\s*\)|_ms\b", "ms"),
    (r"\(\s*s\s*\)|_s\b|\bsec\b", "s"),
    (r"\bmin\b", "min"),
]


def unit_from_header(header: str, canonical: str) -> str | None:
    """Unit token found in a column header that fits the canonical field, or None."""
    q = QUANTITY.get(canonical)
    if q is None:
        return None
    allowed = set(FACTORS[q]) | ({"bu/ac"} if q == "yield" else set())
    text = header.lower()
    variants = (text, text.replace("_", "/"), text.replace("_per_", "/"))
    for pattern, token in _HEADER_UNITS:
        if token in allowed and any(re.search(pattern, v) for v in variants):
            return token
    return None


def unit_from_values(values, canonical: str) -> tuple[str | None, str]:
    """Guess a unit from the value range. Returns (unit or None, reason)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v) & (v > 0)]
    if v.size == 0:
        return None, "no positive values"
    med = float(np.median(v))
    q = QUANTITY.get(canonical)
    if q == "length" and canonical == "swath":
        if med <= 16:
            return "m", f"median {med:.3g} looks like metres"
        if med <= 60:
            return "ft", f"median {med:.3g} looks like feet"
        if med <= 1000:
            return "in", f"median {med:.3g} looks like inches"
        return "mm", f"median {med:.3g} looks like millimetres"
    if q == "moisture":
        if med < 1:
            return "fraction", f"median {med:.3g} looks like a fraction"
        if med <= 60:
            return "%", f"median {med:.3g} looks like percent"
        return "ppm", f"median {med:.3g} looks like ppm"
    if q == "yield":
        if 0.3 <= med <= 25:
            return "t/ha", f"median {med:.3g} looks like t/ha"
        if 300 <= med <= 25000:
            return "kg/ha", f"median {med:.3g} looks like kg/ha"
        return None, f"median {med:.3g}: ambiguous (bu/ac? dt/ha? lb/ac?)"
    if q == "heading":
        mx = float(np.max(v))
        return ("rad", "values <= 2π") if mx <= 2 * np.pi + 0.01 else ("deg", "values > 2π")
    return None, f"median {med:.3g}: cannot tell the unit from values"


def convert(values, canonical: str, unit: str, bushel_weight_lb: float | None = None):
    v = np.asarray(values, dtype=float)
    q = QUANTITY[canonical]
    if unit == "bu/ac":
        if not bushel_weight_lb:
            raise ValueError("bu/ac yield needs the crop (or bushel_weight_lb) to convert")
        return v * bushel_weight_lb * LB_PER_AC_TO_T_PER_HA
    try:
        return v * FACTORS[q][unit]
    except KeyError as exc:
        raise ValueError(
            f"unknown unit {unit!r} for {canonical}; known: {sorted(FACTORS[q])}"
        ) from exc


def bushel_weight(crop: str | None, override: float | None = None) -> float | None:
    if override:
        return float(override)
    if crop:
        return BUSHEL_WEIGHT_LB.get(crop.lower().replace(" ", "_").split("_")[-1])
    return None
