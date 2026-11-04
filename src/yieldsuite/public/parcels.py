"""Field boundaries by parcel number from a parcel-register WFS.

Default service: the Finnish Food Authority's (Ruokavirasto) INSPIRE WFS,
which publishes the field parcel register (peltolohkorekisteri) as one layer
per year. Layer and attribute names are not hard-coded: they are discovered
from the service (GetCapabilities, DescribeFeatureType) and every choice is
reported, so the first real run shows what was used. Both can be fixed in the
config (``public_data.parcels_typename`` / ``parcel_id_column``).

Requests use standard WFS 2.0 with an OGC Filter Encoding PropertyIsEqualTo
filter and ask for GeoJSON in EPSG:3067.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import http

RUOKAVIRASTO_WFS = "https://inspire.ruokavirasto-awsa.com/geoserver/wfs"
LAYER_HINTS = (
    "peruslohk",
    "plohk",
    "peltolohk",
    "basicparcel",
    "agriculturalparcel",
    "gsaagriculturalparcel",
    "parcel",
)
ID_HINTS = (
    "peruslohkotunnus",
    "plohkotunnus",
    "lohkotunnus",
    "peruslohko",
    "plohko",
    "parcelid",
    "parcel_id",
    "tunnus",
    "inspireid",
    "localid",
)


def normalise_parcel_id(value) -> str:
    """Finnish parcel numbers are 10 digits; separators and spaces are dropped."""
    s = re.sub(r"\D", "", str(value))
    if not s:
        raise ValueError(f"not a parcel number: {value!r}")
    return s


def _safe_id(value) -> str | None:
    try:
        return normalise_parcel_id(value)
    except ValueError:
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _get_text(url: str, get=None) -> str:
    if get is not None:
        return get(url)
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": http.USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - configured URL
        return resp.read().decode("utf-8", errors="replace")


@dataclass
class LayerChoice:
    typename: str
    id_column: str
    year: int | None
    candidates: list[dict] = field(default_factory=list)
    properties: list[str] = field(default_factory=list)
    reason: str = ""


class ParcelService:
    def __init__(self, url: str = RUOKAVIRASTO_WFS, get_text=None, get_json=None):
        self.url = url
        self._get_text = get_text
        self._get_json = get_json or http.get_json

    def _url(self, **params) -> str:
        sep = "&" if "?" in self.url else "?"
        return (
            self.url
            + sep
            + urllib.parse.urlencode({"service": "WFS", "version": "2.0.0", **params})
        )

    # --- discovery ---------------------------------------------------------------

    def layers(self) -> list[dict]:
        xml = _get_text(self._url(request="GetCapabilities"), self._get_text)
        root = ET.fromstring(xml)
        out = []
        for ft in root.iter():
            if _local(ft.tag) != "FeatureType":
                continue
            item = {}
            for child in ft:
                if _local(child.tag) in ("Name", "Title"):
                    item[_local(child.tag).lower()] = (child.text or "").strip()
            if item.get("name"):
                out.append(item)
        if not out:
            raise ValueError("the WFS lists no feature types")
        return out

    def properties(self, typename: str) -> list[str]:
        xml = _get_text(
            self._url(request="DescribeFeatureType", typeNames=typename), self._get_text
        )
        root = ET.fromstring(xml)
        props = []
        for el in root.iter():
            if _local(el.tag) == "element" and el.get("name") and el.get("type"):
                if "gml:" in el.get("type", "") and "Property" in el.get("type", ""):
                    continue  # geometry
                props.append(el.get("name"))
        return props

    def choose(
        self, year: int | None = None, typename: str | None = None, id_column: str | None = None
    ) -> LayerChoice:
        candidates = []
        if typename is None:
            layers = self.layers()
            for lyr in layers:
                text = (lyr["name"] + " " + lyr.get("title", "")).lower().replace(" ", "")
                score = next(
                    (len(LAYER_HINTS) - i for i, h in enumerate(LAYER_HINTS) if h in text), 0
                )
                if not score:
                    continue
                years = [int(y) for y in re.findall(r"(?<!\d)(20\d\d)(?!\d)", text)]
                candidates.append({**lyr, "score": score, "year": max(years) if years else None})
            if not candidates:
                raise ValueError(
                    "no parcel layer found in the WFS; set "
                    "public_data.parcels_typename (layers: "
                    + ", ".join(lyr["name"] for lyr in layers[:15])
                    + ")"
                )
            best_score = max(c["score"] for c in candidates)
            pool = [c for c in candidates if c["score"] == best_score]
            dated = [c for c in pool if c["year"] is not None]
            if year is not None and dated:
                same = [c for c in dated if c["year"] == year]
                earlier = [c for c in dated if c["year"] <= year]
                pick = (
                    same
                    or sorted(earlier, key=lambda c: c["year"])[-1:]
                    or sorted(dated, key=lambda c: c["year"])[:1]
                )[0]
                reason = f"layer for {pick['year']}" + (
                    "" if pick["year"] == year else f" (no {year} layer; closest earlier/available)"
                )
            else:
                pick = sorted(dated, key=lambda c: c["year"])[-1] if dated else pool[0]
                reason = "latest parcel layer" if dated else "only undated parcel layer"
            typename, chosen_year = pick["name"], pick["year"]
        else:
            chosen_year, reason = year, "from config"
        props = self.properties(typename)
        if id_column is None:
            low = {p.lower(): p for p in props}
            id_column = next((low[h] for h in ID_HINTS if h in low), None)
            if id_column is None:
                id_column = next(
                    (p for p in props if any(h in p.lower() for h in ID_HINTS[:5])), None
                )
            if id_column is None:
                raise ValueError(
                    f"no parcel id attribute in {typename}; set "
                    f"public_data.parcel_id_column (attributes: {props})"
                )
        elif props and id_column not in props:
            raise ValueError(f"{id_column!r} is not an attribute of {typename}: {props}")
        return LayerChoice(typename, id_column, chosen_year, candidates, props, reason)

    # --- query -------------------------------------------------------------------

    @staticmethod
    def filter_xml(id_column: str, ids: list[str]) -> str:
        eq = "".join(
            f"<fes:PropertyIsEqualTo><fes:ValueReference>{id_column}</fes:ValueReference>"
            f"<fes:Literal>{v}</fes:Literal></fes:PropertyIsEqualTo>"
            for v in ids
        )
        body = f"<fes:Or>{eq}</fes:Or>" if len(ids) > 1 else eq
        return f'<fes:Filter xmlns:fes="http://www.opengis.net/fes/2.0">{body}</fes:Filter>'

    def fetch(self, parcel_ids, choice: LayerChoice, srs: str = "EPSG:3067") -> dict:
        ids = [normalise_parcel_id(p) for p in parcel_ids]
        url = self._url(
            request="GetFeature",
            typeNames=choice.typename,
            outputFormat="application/json",
            srsName=srs,
            FILTER=self.filter_xml(choice.id_column, ids),
        )
        fc = self._get_json(url)
        if fc.get("type") != "FeatureCollection":
            raise ValueError(f"WFS did not return GeoJSON (got {str(fc)[:120]})")
        found = {
            _safe_id((f.get("properties") or {}).get(choice.id_column))
            for f in fc.get("features", [])
        }
        missing = sorted(set(ids) - found)
        if missing:
            raise ValueError(f"parcels not found in {choice.typename}: {missing}")
        fc.setdefault("crs", {"type": "name", "properties": {"name": srs}})
        return fc
