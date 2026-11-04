"""Terrain covariates from an elevation model with a known slope and hollow."""

import asyncio
import json
import math

import numpy as np
import pytest

from yieldsuite import synthetic
from yieldsuite.covariates.terrain import d8_accumulation
from yieldsuite.l4.grid import Grid

rasterio = pytest.importorskip("rasterio")

GRID = Grid(synthetic.X0, synthetic.Y0 + synthetic.FIELD_H, 20.0, 12, 8)
CX, CY = synthetic.X0 + 120, synthetic.Y0 + 80  # field centre


def write_dem(path, pad=300.0, px=2.0, hollow=True):
    from rasterio.transform import from_origin

    x0, y1 = synthetic.X0 - pad, synthetic.Y0 + synthetic.FIELD_H + pad
    nx = int((synthetic.FIELD_W + 2 * pad) / px)
    ny = int((synthetic.FIELD_H + 2 * pad) / px)
    xs = x0 + (np.arange(nx) + 0.5) * px
    ys = y1 - (np.arange(ny) + 0.5) * px
    xx, yy = np.meshgrid(xs, ys)
    z = 100 + 0.02 * (xx - synthetic.X0)  # 2 % slope rising to the east
    if hollow:
        z -= 1.5 * np.exp(-((xx - CX) ** 2 + (yy - CY) ** 2) / (2 * 30.0**2))
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=nx,
        height=ny,
        count=1,
        dtype="float32",
        crs="EPSG:3067",
        transform=from_origin(x0, y1, px, px),
    ) as dst:
        dst.write(z.astype("float32")[None])
    return path


def test_d8_accumulates_downslope():
    elev = np.tile(np.arange(10, dtype=float), (5, 1))  # rises to the east
    acc = d8_accumulation(elev, 20.0)
    assert (np.diff(acc.mean(axis=0)) < 0).all()  # more upslope area to the west
    assert acc.max() == pytest.approx(10 * 400.0)  # a row of 10 cells drains west


def test_plane_slope_and_hollow(tmp_path):
    from yieldsuite.covariates import terrain_covariates

    bands, rep = terrain_covariates(
        write_dem(tmp_path / "dem.tif", hollow=False), GRID, "EPSG:3067"
    )
    assert np.nanmedian(bands["slope_deg"]) == pytest.approx(
        math.degrees(math.atan(0.02)), abs=0.05
    )
    assert np.nanmax(np.abs(bands["tpi_m"])) < 0.05
    assert rep["buffer_covered_share"] > 0.95 and not rep["warnings"]

    bands, _ = terrain_covariates(write_dem(tmp_path / "dem2.tif"), GRID, "EPSG:3067")
    r, c = 3, 6  # cell at the field centre
    assert bands["tpi_m"][r, c] < -0.5
    assert bands["twi"][r, c] > np.nanpercentile(bands["twi"], 90)


def test_dem_without_buffer_warns(tmp_path):
    from yieldsuite.covariates import terrain_covariates

    _, rep = terrain_covariates(write_dem(tmp_path / "dem.tif", pad=0.0), GRID, "EPSG:3067")
    assert rep["warnings"] and "upslope" in rep["warnings"][0]


def test_cli_terrain_and_zones_with_terrain(tmp_path, capsys):
    from yieldsuite.cli import main
    from yieldsuite.l4.io import read_bands, write_cog

    grid, layers, _, _ = synthetic.multi_year_layers(n_years=3)
    paths = [
        str(write_cog({"yield": y}, grid, "EPSG:3067", tmp_path / f"L5_{i}.tif", names=["yield"]))
        for i, y in enumerate(layers)
    ]
    dem = write_dem(tmp_path / "dem.tif", pad=400.0)
    out = tmp_path / "terrain.tif"
    assert main(["terrain", str(dem), "--like", paths[0], "--out", str(out)]) == 0
    bands, g, _ = read_bands(out, require_yield=False)
    assert set(bands) == {"elevation_m", "slope_deg", "tpi_m", "twi"} and g == grid
    assert main(["zones", *paths, "--covariate", str(out), "--out", str(tmp_path / "z")]) == 0
    summary = json.loads((tmp_path / "z" / "zones.json").read_text())
    assert summary["layers"][-4:] == [f"covariate{i}" for i in range(1, 5)]


def test_mcp_make_terrain(tmp_path):
    pytest.importorskip("mcp")
    from yieldsuite.mcp_server import build_server
    from yieldsuite.store import FarmStore, init_demo_store

    store = FarmStore(init_demo_store(tmp_path / "s"))
    server = build_server(store, actor="t")
    asyncio.run(
        server.call_tool("run_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    )
    write_dem(store.farm_dir("demo-farm") / "synthetic-01" / "dem.tif")
    res = json.loads(
        asyncio.run(
            server.call_tool(
                "make_terrain",
                {"farm": "demo-farm", "field": "synthetic-01", "dem_path": "dem.tif"},
            )
        )
        .content[0]
        .text
    )
    assert res["covariate"] == "covariates/terrain.tif"
    assert res["grid_from"] == "L4_synthetic-01_2026.tif"


# --- NLS (Maanmittauslaitos) elevation model via WCS, against a fake service ---

DESCRIBE = """<?xml version="1.0"?>
<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0"
    xmlns:gml="http://www.opengis.net/gml/3.2">
  <wcs:CoverageDescription gml:id="korkeusmalli_2m">
    <gml:boundedBy><gml:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/3067"
        axisLabels="E N" uomLabels="m m" srsDimension="2">
      <gml:lowerCorner>0 6600000</gml:lowerCorner><gml:upperCorner>800000 7800000</gml:upperCorner>
    </gml:Envelope></gml:boundedBy>
  </wcs:CoverageDescription>
</wcs:CoverageDescriptions>"""


class _Resp:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.data


class FakeNLS:
    def __init__(self, tif_bytes):
        self.tif = tif_bytes
        self.urls = []

    def __call__(self, req, timeout):
        import urllib.parse

        self.urls.append(req.full_url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        assert q["api-key"] == ["secret-key"]
        if q["request"] == ["DescribeCoverage"]:
            return _Resp(DESCRIBE.encode())
        assert q["subset"][0].startswith("E(") and q["subset"][1].startswith("N(")
        return _Resp(self.tif)


def test_nls_fetch_uses_discovered_axes_and_hides_the_key(tmp_path):
    from yieldsuite.public.nls_dem import NLSElevation

    tif = write_dem(tmp_path / "src.tif").read_bytes()
    fake = FakeNLS(tif)
    nls = NLSElevation(api_key="secret-key", opener=fake)
    prov = nls.fetch((400000, 6950000, 400240, 6950160), tmp_path / "dem.tif")
    assert prov["axis_labels"] == ["E", "N"] and prov["licence"] == "CC BY 4.0"
    assert "secret-key" not in (tmp_path / "dem.provenance.json").read_text()
    assert (tmp_path / "dem.tif").read_bytes() == tif

    def failing(req, timeout):
        raise OSError(f"HTTP 403 for {req.full_url}")

    with pytest.raises(OSError) as err:
        NLSElevation(api_key="secret-key", opener=failing).axis_labels()
    assert "secret-key" not in str(err.value) and "***" in str(err.value)

    bad = NLSElevation(
        api_key="secret-key",
        opener=lambda req, timeout: _Resp(
            DESCRIBE.encode()
            if "DescribeCoverage" in req.full_url
            else b"<ExceptionReport>invalid api-key secret-key</ExceptionReport>"
        ),
    )
    with pytest.raises(ValueError) as err:
        bad.fetch((0, 0, 10, 10), tmp_path / "x.tif")
    assert "secret-key" not in str(err.value)


def test_nls_key_required(monkeypatch):
    from yieldsuite.public.nls_dem import NLSElevation

    monkeypatch.delenv("NLS_API_KEY", raising=False)
    monkeypatch.delenv("MML_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NLS_API_KEY"):
        NLSElevation()


def test_mcp_make_terrain_fetches_nls_dem(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    from yieldsuite.mcp_server import build_server
    from yieldsuite.public import nls_dem
    from yieldsuite.store import FarmStore, init_demo_store

    fake = FakeNLS(write_dem(tmp_path / "src.tif", pad=400.0).read_bytes())
    monkeypatch.setenv("NLS_API_KEY", "secret-key")
    monkeypatch.setattr(nls_dem.urllib.request, "urlopen", fake)
    store = FarmStore(init_demo_store(tmp_path / "s"))
    server = build_server(store, actor="t")
    asyncio.run(
        server.call_tool("run_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    )
    res = json.loads(
        asyncio.run(
            server.call_tool("make_terrain", {"farm": "demo-farm", "field": "synthetic-01"})
        )
        .content[0]
        .text
    )
    assert res["covariate"] == "covariates/terrain.tif" and not res["report"]["warnings"]
    fdir = store.farm_dir("demo-farm") / "synthetic-01"
    assert (fdir / "covariates" / "dem_nls_2m.tif").exists()
    assert "secret-key" not in (store.root / "audit.jsonl").read_text()
