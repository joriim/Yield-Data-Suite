"""L4 -> L5: fusion with satellite and drone imagery.

Implemented: Sentinel-2 features via openEO (``sentinel2``), yield-image model
(``model``), regression-kriging gap fill (``gapfill``), and ``fuse`` which
chains them.

With L3 records (a chain run): ``residual_shift`` (check of the delay against
the image) and ``level_pass_stripes`` (random pass effect, field total kept).

Planned (see docs/design/processing-levels.md, "L4 -> L5"):

- ``zones``: multi-year relative yield + imagery -> KM-sPC zones.
- drone orthomosaic features (harmonised to Sentinel-2 reflectance).

Filled cells are always labelled and never reused as training data (design rule 6).
"""

from __future__ import annotations

from .features import feature_matrix
from .fuse import FusionResult, fuse
from .gapfill import gap_fill
from .model import YieldImageModel, fit_yield_image_model
from .sentinel2 import S2Request
from .shift import residual_shift
from .stripes import level_pass_stripes


def _todo(name: str):
    def fn(*args, **kwargs):
        raise NotImplementedError(f"L5 {name} is not implemented yet")

    fn.__name__ = name
    return fn


zones = _todo("zones")

__all__ = [
    "FusionResult",
    "S2Request",
    "YieldImageModel",
    "feature_matrix",
    "fit_yield_image_model",
    "fuse",
    "gap_fill",
    "level_pass_stripes",
    "residual_shift",
    "zones",
]
