"""Field boundary by parcel number from a parcel-register WFS (fake Ruokavirasto)."""

import asyncio
import json
import urllib.parse

import pytest
import shapely

from yieldsuite import synthetic
from yieldsuite.public import parcels as parcels_mod
from yieldsuite.public.parcels import ParcelService, normalise_parcel_id

CAPS = """<?xml version="1.0"?>
<wfs:WFS_Capabilities xmlns:wfs="http://www.opengis.net/wfs/2.0" version="2.0.0">
  <wfs:FeatureTypeList>
    <wfs:FeatureType><wfs:Name>ruokavirasto:PERUSLOHKOT_2024</wfs:Name>
      <wfs:Title>Peruslohkot 2024</wfs:Title></wfs:FeatureType>
    <wfs:FeatureType><wfs:Name>ruokavirasto:PERUSLOHKOT_2026</wfs:Name>
      <wfs:Title>Peruslohkot 2026</wfs:Title></wfs:FeatureType>
    <wfs:FeatureType><wfs:Name>ruokavirasto:PERUSLOHKOT_2025</wfs:Name>
      <wfs:Title>Peruslohkot 2025</wfs:Title></wfs:FeatureType>
    <wfs:FeatureType><wfs:Name>ruokavirasto:ELAINTEN_PITOPAIKAT</wfs:Name>
      <wfs:Title>Animal holdings</wfs:Title></wfs:FeatureType>
  </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""

SCHEMA = """<?xml version="1.0"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:gml="http://www.opengis.net/gml/3.2">
  <xsd:complexType name="T"><xsd:complexContent><xsd:extension base="gml:AbstractFeatureType">
    <xsd:sequence>
      <xsd:element name="PERUSLOHKOTUNNUS" type="xsd:string"/>
      <xsd:element name="PINTA_ALA" type="xsd:double"/>
      <xsd:element name="geom" type="gml:MultiSurfacePropertyType"/>
    </xsd:sequence>
  </xsd:extension></xsd:complexContent></xsd:complexType>
</xsd:schema>"""


def _parcel(pid, box):
    return {
        "type": "Feature",
        "properties": {"PERUSLOHKOTUNNUS": pid, "PINTA_ALA": 3.84},
        "geometry": shapely.geometry.mapping(box),
    }


FIELD_BOX = shapely.box(
    synthetic.X0, synthetic.Y0, synthetic.X0 + synthetic.FIELD_W, synthetic.Y0 + synthetic.FIELD_H
)
REGISTER = {
    "1234567890": FIELD_BOX,
    "1234567891": shapely.box(
        synthetic.X0 + 300, synthetic.Y0, synthetic.X0 + 500, synthetic.Y0 + 100
    ),
}


class FakeWFS:
    def __init__(self):
        self.requests = []

    def text(self, url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.requests.append(q)
        return CAPS if q["request"] == ["GetCapabilities"] else SCHEMA

    def json(self, url):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.requests.append(q)
        assert q["srsName"] == ["EPSG:3067"] and q["outputFormat"] == ["application/json"]
        wanted = [v for v in REGISTER if f"<fes:Literal>{v}</fes:Literal>" in q["FILTER"][0]]
        return {"type": "FeatureCollection", "features": [_parcel(v, REGISTER[v]) for v in wanted]}


def test_normalise_parcel_id():
    assert normalise_parcel_id("123-4567-890") == "1234567890"
    assert normalise_parcel_id(1234567890) == "1234567890"
    with pytest.raises(ValueError):
        normalise_parcel_id("abc")


def test_layer_and_attribute_discovery():
    fake = FakeWFS()
    svc = ParcelService("https://wfs.example/wfs", get_text=fake.text, get_json=fake.json)
    c = svc.choose(2026)
    assert (c.typename, c.id_column, c.year) == (
        "ruokavirasto:PERUSLOHKOT_2026",
        "PERUSLOHKOTUNNUS",
        2026,
    )
    c = svc.choose(2027)  # not published yet: latest earlier year, and say so
    assert c.typename.endswith("2026") and "no 2027" in c.reason
    assert svc.choose(None).typename.endswith("2026")
    assert "ELAINTEN" not in json.dumps(c.candidates)
    with pytest.raises(ValueError, match="not an attribute"):
        svc.choose(2026, id_column="LOHKO")


def test_fetch_by_parcel_number_and_missing():
    fake = FakeWFS()
    svc = ParcelService("https://wfs.example/wfs", get_text=fake.text, get_json=fake.json)
    choice = svc.choose(2026)
    fc = svc.fetch(["123-4567-890"], choice)
    assert len(fc["features"]) == 1 and fc["crs"]["properties"]["name"] == "EPSG:3067"
    flt = fake.requests[-1]["FILTER"][0]
    assert "PropertyIsEqualTo" in flt and "PERUSLOHKOTUNNUS" in flt
    fc = svc.fetch(["1234567890", "1234567891"], choice)
    assert len(fc["features"]) == 2 and "<fes:Or>" in fake.requests[-1]["FILTER"][0]
    with pytest.raises(ValueError, match="not found"):
        svc.fetch(["9999999999"], choice)


def test_mcp_boundary_by_parcel_number_enables_field_edge(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    pytest.importorskip("geopandas")
    from yieldsuite.mcp_server import build_server
    from yieldsuite.public import http
    from yieldsuite.store import FarmStore, init_demo_store

    fake = FakeWFS()
    monkeypatch.setattr(parcels_mod, "_get_text", lambda url, get=None: fake.text(url))
    monkeypatch.setattr(http, "get_json", fake.json)
    store = FarmStore(init_demo_store(tmp_path / "s"))
    server = build_server(store, actor="t")
    key = {"farm": "demo-farm", "field": "synthetic-01", "year": 2026}

    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, {**key, **args})).content[0].text)

    res = call("get_boundary_by_parcel_id", {"parcel_ids": ["123-4567-890"]})
    assert res["parcels"] == ["1234567890"] and abs(res["area_ha"] - 3.84) < 0.01
    assert res["source"]["layer"] == "ruokavirasto:PERUSLOHKOT_2026"
    assert res["source"]["id_column"] == "PERUSLOHKOTUNNUS"
    import yaml

    cfg = yaml.safe_load(
        (store.year_dir("demo-farm", "synthetic-01", 2026) / "field.yaml").read_text()
    )
    assert cfg["parcel_ids"] == ["1234567890"] and cfg["boundary"].startswith("raw/boundary/")
    run = call("run_chain", {})
    assert run["ok"]
    qa = call("get_qa", {})["L4_qa"]
    assert qa["flags"]["FIELD_EDGE"] > 0 and abs(qa["field_edge"]["boundary_area_ha"] - 3.84) < 0.01


def test_cli_parcel(tmp_path, monkeypatch, capsys):
    pytest.importorskip("geopandas")
    from yieldsuite.cli import main
    from yieldsuite.public import http

    fake = FakeWFS()
    monkeypatch.setattr(parcels_mod, "_get_text", lambda url, get=None: fake.text(url))
    monkeypatch.setattr(http, "get_json", fake.json)
    out = tmp_path / "b.gpkg"
    assert main(["parcel", "1234567890", "--year", "2026", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "PERUSLOHKOT_2026" in text and "3.84 ha" in text and out.exists()
