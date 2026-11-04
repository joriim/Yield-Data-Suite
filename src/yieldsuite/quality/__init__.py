"""Grain quality channel Q1-Q4 (e.g. GrainSense Flow / GS Combine analyzers).

Quality is not mass: it is attached to the harvester records and averaged with
mass weights, never apportioned by area.

Q1  measurements in SI units (time, protein / moisture / oil %, basis)
Q2  measurement aligned to the harvester record that cut the grain (time + delay)
Q3  flagged and calibrated to lab reference samples (bias)
Q4  grid: mass-weighted protein, protein yield and nitrogen uptake
"""

from .grainsense import GrainSenseClient, nutrient_percentages
from .pipeline import QualityResult, align_to_records, grid_quality, read_quality

__all__ = [
    "GrainSenseClient",
    "QualityResult",
    "align_to_records",
    "grid_quality",
    "nutrient_percentages",
    "read_quality",
]
