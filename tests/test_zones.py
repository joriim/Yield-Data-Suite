"""Management zones (KM-sPC) on synthetic multi-year maps with known zones."""

import asyncio
import json
import shutil
from math import comb

import numpy as np
import pytest

from yieldsuite import synthetic
from yieldsuite.l4.grid import Grid
from yieldsuite.zones import fpi_nce, fuzzy_kmeans, make_zones, place, union_grid


def ari(a, b):
    a, b = np.asarray(a).ravel(), np.asarray(b).ravel()
    ct = np.zeros((a.max() + 1, b.max() + 1))
    np.add.at(ct, (a, b), 1)
    s = sum(comb(int(n), 2) for n in ct.ravel())
    sa = sum(comb(int(n), 2) for n in ct.sum(1))
    sb = sum(comb(int(n), 2) for n in ct.sum(0))
    e = sa * sb / comb(a.size, 2)
    return (s - e) / (0.5 * (sa + sb) - e)


@pytest.mark.parametrize("n_years,seed,min_ari", [(5, 0, 0.95), (3, 1, 0.8), (2, 2, 0.7)])
def test_zones_recover_truth(n_years, seed, min_ari):
    # With two years an unstable patch is legitimately its own zone; leave it out there.
    grid, layers, zone, unstable = synthetic.multi_year_layers(
        n_years=n_years, seed=seed, unstable_patch=n_years >= 3
    )
    r = make_zones(layers, grid)
    assert r.k == 3
    assert ari(r.zones, zone) > min_ari
    means = [z["mean_relative_yield_pct"] for z in r.summary["zones"]]
    assert means == sorted(means)  # zone 1 = lowest
    if n_years >= 3:
        assert (r.stability[unstable] == 3).mean() > 0.9
        assert (r.stability[~unstable] == 3).mean() < 0.05


def test_missing_cells_and_years():
    grid, layers, zone, _ = synthetic.multi_year_layers(n_years=4, seed=3)
    layers[0][:5, :] = np.nan  # one year did not cover the north edge
    r = make_zones(layers, grid)
    assert (r.zones[:5, :] > 0).all()  # 3 of 4 years still meet the 70 % rule
    layers[1][:5, :] = np.nan
    r = make_zones(layers, grid)
    assert (r.zones[:5, :] == 0).all()


def test_fuzzy_kmeans_and_indices():
    rng = np.random.default_rng(0)
    Z = np.vstack([rng.normal(c, 0.1, (50, 2)) for c in (0, 3, 6)])
    u, C, _ = fuzzy_kmeans(Z, 3)
    assert sorted(np.round(C[:, 0])) == [0, 3, 6]
    fpi, nce = fpi_nce(u)
    assert fpi < 0.05 and nce < 0.05


def test_grid_union_and_alignment():
    a = Grid(0.0, 100.0, 20.0, 3, 3)
    b = Grid(40.0, 140.0, 20.0, 3, 3)
    u = union_grid([a, b])
    assert (u.xmin, u.ymax, u.nx, u.ny) == (0.0, 140.0, 5, 5)
    placed = place(np.ones((3, 3)), b, u)
    assert np.isfinite(placed).sum() == 9 and np.isfinite(placed[0, 2])
    with pytest.raises(ValueError, match="origin"):
        union_grid([a, Grid(5.0, 100.0, 20.0, 3, 3)])


def test_cli_zones(tmp_path, capsys):
    pytest.importorskip("rasterio")
    from yieldsuite.cli import main
    from yieldsuite.l4.io import write_cog

    grid, layers, _, _ = synthetic.multi_year_layers(n_years=3)
    paths = [
        str(write_cog({"yield": y}, grid, "EPSG:3067", tmp_path / f"L5_{i}.tif", names=["yield"]))
        for i, y in enumerate(layers)
    ]
    assert main(["zones", *paths, "--out", str(tmp_path / "z")]) == 0
    assert "3 zones" in capsys.readouterr().out
    assert (tmp_path / "z" / "zones.gpkg").exists()


def test_mcp_zones_from_store(tmp_path):
    pytest.importorskip("mcp")
    pytest.importorskip("geopandas")
    from yieldsuite.mcp_server import build_server
    from yieldsuite.store import FarmStore, init_demo_store

    store = FarmStore(init_demo_store(tmp_path / "s"))
    y26 = store.year_dir("demo-farm", "synthetic-01", 2026)
    y25 = y26.parent / "2025"
    shutil.copytree(y26, y25)
    import yaml

    cfg = yaml.safe_load((y25 / "field.yaml").read_text())
    with pytest.raises(Exception, match="folder and config agree"):
        store.load_config("demo-farm", "synthetic-01", 2025)
    cfg["year"] = 2025
    (y25 / "field.yaml").write_text(yaml.safe_dump(cfg))
    synthetic.make_field(1).log.to_csv(
        y25 / "raw" / "harvest_log" / "combine-a_2026.csv", index=False
    )
    server = build_server(store, actor="t")

    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, args)).content[0].text)

    for year in (2025, 2026):
        assert call("run_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": year})["ok"]
    res = call("make_zones", {"farm": "demo-farm", "field": "synthetic-01", "k_max": 4})
    assert res["years_used"] == {"2025": "L4", "2026": "L4"}
    assert 2 <= res["summary"]["k"] <= 4
    assert (store.root / res["files"]["polygons"]).exists()
