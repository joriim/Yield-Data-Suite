"""Public data: Sentinel-2 STAC search, parcel WFS, field boundary and FIELD_EDGE."""

import asyncio
import json
import urllib.parse

import pytest
import shapely
import yaml

from yieldsuite import synthetic
from yieldsuite.public.boundary import edge_mask
from yieldsuite.public.stac import search_sentinel2
from yieldsuite.public.wfs import get_features

gpd = pytest.importorskip("geopandas")
CRS = "EPSG:3067"


def _scene(i, date, cc, tile="35VLL"):
    return {
        "id": f"S2_{i}",
        "properties": {"datetime": f"{date}T10:20:31Z", "eo:cloud_cover": cc, "s2:mgrs_tile": tile},
    }


def test_stac_search_paginates_filters_and_picks_best_per_day():
    pages = [
        {
            "features": [
                _scene(1, "2026-06-01", 80.0),
                _scene(2, "2026-06-03", 5.0),
                _scene(3, "2026-06-03", 20.0, "35VLK"),
            ],
            "links": [
                {"rel": "next", "href": "https://stac.example/v1/search", "body": {"token": "p2"}}
            ],
        },
        {"features": [_scene(4, "2026-06-11", 30.0)], "links": []},
    ]
    calls = []

    def post(url, body):
        calls.append((url, body))
        return pages[len(calls) - 1]

    res = search_sentinel2(
        (22.8, 62.7, 22.9, 62.8),
        "2026-05-01",
        "2026-08-31",
        60.0,
        "https://stac.example/v1",
        post=post,
    )
    assert calls[0][1]["collections"] == ["sentinel-2-l2a"] and calls[1][1]["token"] == "p2"
    assert res["n_scenes"] == 4 and res["n_below_max_cloud"] == 3
    assert [d["date"] for d in res["dates"]] == ["2026-06-03", "2026-06-11"]
    assert res["dates"][0]["cloud_cover"] == 5.0 and res["dates"][0]["tile"] == "35VLL"


def test_wfs_request_and_validation():
    seen = {}

    def get(url):
        seen["url"] = url
        return {"type": "FeatureCollection", "features": []}

    get_features("https://wfs.example/ows", "parcels:fields", (22.8, 62.7, 22.9, 62.8), get=get)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert q["typeNames"] == ["parcels:fields"] and q["outputFormat"] == ["application/json"]
    assert q["bbox"][0].startswith("62.7,22.8,62.8,22.9")  # lat/lon order for the EPSG urn
    with pytest.raises(ValueError):
        get_features("https://wfs.example/ows", "x", (0, 0, 1, 1), get=lambda u: {"error": 1})


def test_edge_mask():
    field = shapely.box(0, 0, 100, 50)
    m = edge_mask(field, [50, 2, 50, 120], [25, 25, 48, 25], buffer_m=5)
    assert list(m) == [False, True, True, True]


def _parcels_gpkg(path):
    x0, y0 = synthetic.X0, synthetic.Y0
    w, h = synthetic.FIELD_W, synthetic.FIELD_H
    gdf = gpd.GeoDataFrame(
        {"LOHKO": ["0001", "0002", "0003"]},
        geometry=[
            shapely.box(x0, y0, x0 + w, y0 + h),
            shapely.box(x0 + w + 5, y0, x0 + 2 * w, y0 + h),
            shapely.box(x0, y0 + h + 5, x0 + w, y0 + 2 * h),
        ],
        crs=CRS,
    ).to_crs("EPSG:4326")
    gdf.to_file(path, driver="GPKG")
    return path


def test_store_boundary_import_and_field_edge(tmp_path):
    pytest.importorskip("mcp")
    pytest.importorskip("rasterio")
    from yieldsuite.mcp_server import build_server
    from yieldsuite.store import FarmStore, init_demo_store

    store = FarmStore(init_demo_store(tmp_path / "s"))
    server = build_server(store, actor="t")

    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, args)).content[0].text)

    key = {"farm": "demo-farm", "field": "synthetic-01", "year": 2026}
    first = call("run_chain", key)
    parcels = _parcels_gpkg(tmp_path / "lohkot.gpkg")
    out = call("import_boundary", {**key, "source_path": str(parcels), "id_column": "LOHKO"})
    assert out["parcels"] == ["0001"]  # chosen by overlap with the harvested area
    assert abs(out["area_ha"] - 3.84) < 0.01
    second = call("run_chain", key)
    assert second["ok"] and second["mass_ok"]
    qa = call("get_qa", key)["L4_qa"]
    assert qa["field_edge"]["records"] > 0 and qa["flags"]["FIELD_EDGE"] > 0
    assert second["flagged_pct"] > first["flagged_pct"]
    # explicit ids work too, unknown ids are refused
    out = call(
        "import_boundary",
        {**key, "source_path": str(parcels), "id_column": "LOHKO", "parcel_ids": ["0001", "0002"]},
    )
    assert out["n_parcels"] == 2 and out["boundary"].endswith("boundary_2.gpkg")
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="not found"):
        asyncio.run(
            server.call_tool(
                "import_boundary",
                {**key, "source_path": str(parcels), "id_column": "LOHKO", "parcel_ids": ["9999"]},
            )
        )


def test_mcp_search_and_parcels_with_fake_services(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from yieldsuite.mcp_server import build_server
    from yieldsuite.public import http
    from yieldsuite.store import FarmStore, init_demo_store

    store = FarmStore(init_demo_store(tmp_path / "s"))
    ydir = store.year_dir("demo-farm", "synthetic-01", 2026)
    cfg = yaml.safe_load((ydir / "field.yaml").read_text())
    cfg["public_data"] = {
        "parcels_wfs_url": "https://wfs.example/ows",
        "parcels_typename": "fields",
        "parcel_id_column": "LOHKO",
    }
    cfg["location"] = {"lat": 62.65, "lon": 25.05}
    (ydir / "field.yaml").write_text(yaml.safe_dump(cfg))

    parcels = gpd.read_file(_parcels_gpkg(tmp_path / "p.gpkg"))
    monkeypatch.setattr(
        http,
        "post_json",
        lambda url, body: {"features": [_scene(1, "2026-06-03", 12.0)], "links": []},
    )
    monkeypatch.setattr(http, "get_json", lambda url: json.loads(parcels.to_json()))
    server = build_server(store, actor="t")
    key = {"farm": "demo-farm", "field": "synthetic-01", "year": 2026}
    res = json.loads(asyncio.run(server.call_tool("search_sentinel2", key)).content[0].text)
    assert res["dates"][0]["date"] == "2026-06-03"
    res = json.loads(asyncio.run(server.call_tool("get_parcels", key)).content[0].text)
    assert {p["id"] for p in res["parcels"]} == {"0001", "0002", "0003"}
    assert (ydir / "public" / "parcels.geojson").exists()
    out = json.loads(
        asyncio.run(
            server.call_tool(
                "import_boundary",
                {**key, "source_path": "public/parcels.geojson", "parcel_ids": ["0001"]},
            )
        )
        .content[0]
        .text
    )
    assert out["parcels"] == ["0001"]


def test_utm_zone_suggested_from_tiles():
    from yieldsuite.public.stac import utm_epsg_for_tiles

    assert utm_epsg_for_tiles(["MGRS-34VEQ", "34VEQ", "35VLL"]) == {
        "zone": 34,
        "epsg": "EPSG:32634",
        "zones_seen": [34, 35],
    }
    assert utm_epsg_for_tiles(["T36WVT"])["epsg"] == "EPSG:32636"
    assert utm_epsg_for_tiles([None, "x"]) == {}
