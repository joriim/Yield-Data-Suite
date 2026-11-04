"""National Land Survey of Finland (Maanmittauslaitos) 2 m elevation model via WCS 2.0.

Service: https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2
Coverage ``korkeusmalli_2m`` (GeoTIFF, EPSG:3067), licence CC BY 4.0 (attribution:
Maanmittauslaitos). Free, but every request needs a personal API key from the
NLS "My account" service, read here from NLS_API_KEY (or MML_API_KEY). The key
is never written to logs, errors or provenance.

The coverage's axis labels are read from DescribeCoverage instead of assumed.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from . import http

NLS_WCS = "https://avoin-karttakuva.maanmittauslaitos.fi/ortokuvat-ja-korkeusmallit/wcs/v2"
DEM_COVERAGE = "korkeusmalli_2m"
ATTRIBUTION = "Contains data from the National Land Survey of Finland (CC BY 4.0)"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class NLSElevation:
    def __init__(
        self,
        api_key: str | None = None,
        url: str = NLS_WCS,
        coverage: str = DEM_COVERAGE,
        opener=None,
    ):
        self.api_key = api_key or os.environ.get("NLS_API_KEY") or os.environ.get("MML_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "set NLS_API_KEY: a free key from the National Land Survey "
                "of Finland's My Account service"
            )
        self.url = url
        self.coverage = coverage
        self._open = opener or urllib.request.urlopen

    def _get(self, params: list[tuple[str, str]]) -> bytes:
        query = urllib.parse.urlencode(
            [("service", "WCS"), ("version", "2.0.1"), *params, ("api-key", self.api_key)]
        )
        req = urllib.request.Request(f"{self.url}?{query}", headers={"User-Agent": http.USER_AGENT})
        try:
            with self._open(req, timeout=120) as resp:  # noqa: S310 - fixed host
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - re-raised without the key in the text
            msg = str(exc).replace(self.api_key, "***")
            raise OSError(f"NLS WCS request failed: {msg}") from None

    def axis_labels(self) -> tuple[str, str]:
        xml = self._get([("request", "DescribeCoverage"), ("coverageId", self.coverage)])
        root = ET.fromstring(xml)
        for el in root.iter():
            if el.get("axisLabels"):
                labels = el.get("axisLabels").split()
                if len(labels) >= 2:
                    return labels[0], labels[1]
            if _local(el.tag) == "axisLabels" and el.text:
                labels = el.text.split()
                if len(labels) >= 2:
                    return labels[0], labels[1]
        raise ValueError(f"DescribeCoverage of {self.coverage} lists no axis labels")

    def fetch(self, bbox_3067: tuple[float, float, float, float], out: str | Path) -> dict:
        """Write the DEM for (xmin, ymin, xmax, ymax) in EPSG:3067 to ``out`` (GeoTIFF)."""
        ax, ay = self.axis_labels()
        x0, y0, x1, y1 = bbox_3067
        data = self._get(
            [
                ("request", "GetCoverage"),
                ("coverageId", self.coverage),
                ("subset", f"{ax}({x0:.1f},{x1:.1f})"),
                ("subset", f"{ay}({y0:.1f},{y1:.1f})"),
                ("format", "image/tiff"),
            ]
        )
        if not data.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
            text = data[:300].decode("utf-8", errors="replace")
            text = re.sub(r"\s+", " ", text).replace(self.api_key, "***")
            raise ValueError(f"NLS WCS did not return a GeoTIFF: {text}")
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        provenance = {
            "source": "National Land Survey of Finland, elevation model 2 m (WCS)",
            "service": self.url,
            "coverage": self.coverage,
            "bbox_epsg3067": [x0, y0, x1, y1],
            "axis_labels": [ax, ay],
            "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "licence": "CC BY 4.0",
            "attribution": ATTRIBUTION,
        }
        out.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2))
        return provenance


def grid_bbox(grid, buffer_m: float) -> tuple[float, float, float, float]:
    r = grid.resolution
    return (
        grid.xmin - buffer_m,
        grid.ymax - grid.ny * r - buffer_m,
        grid.xmin + grid.nx * r + buffer_m,
        grid.ymax + buffer_m,
    )
