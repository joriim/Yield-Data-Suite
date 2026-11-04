"""Multi-source image features: drone and PlanetScope harmonised to Sentinel-2."""

import json

import numpy as np
import pandas as pd
import pytest
import yaml

from yieldsuite import synthetic
from yieldsuite.l5.harmonize import collapse, harmonize

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("gstools")

from yieldsuite.l4.grid import Grid  # noqa: E402
from yieldsuite.l5.fuse import cell_centres  # noqa: E402
from yieldsuite.l5.raster_features import raster_features  # noqa: E402

GRID = Grid(synthetic.X0, synthetic.Y0 + synthetic.FIELD_H, 20.0, 12, 8)


def _truth_cells():
    _, _, xy = cell_centres(GRID)
    return synthetic.true_yield(xy[:, 0], xy[:, 1])


def test_drone_features_follow_the_reflectance(tmp_path):
    path = synthetic.drone_ortho(tmp_path / "drone.tif")
    df, rep = raster_features(path, GRID, "EPSG:3067", "2026-06-30", "drone", sensor="mavic3m")
    assert rep["scale"] == 1.0 and rep["indices"] == ["NDVI", "NDRE", "GNDVI"]
    assert any("730 nm" in w for w in rep["warnings"])
    nd = df[df["index"] == "NDVI"].set_index("cell")["value"]
    expected = 0.15 + 0.07 * _truth_cells()
    # cell means of a smooth field vs the centre value: close, not identical
    assert np.abs(nd.to_numpy() - expected[nd.index]).max() < 0.02
    assert len(nd) == GRID.nx * GRID.ny  # the 3 m alpha strip leaves coverage > 0.5


def test_planetscope_scale_and_cloud_mask(tmp_path):
    sr, udm = synthetic.planetscope_sr(tmp_path / "ps.tif", tmp_path / "udm2.tif")
    masked, rep = raster_features(
        sr, GRID, "EPSG:3067", "2026-07-16", "planetscope", sensor="superdove", mask_path=udm
    )
    assert rep["scale"] == 1e-4
    unmasked, _ = raster_features(
        sr, GRID, "EPSG:3067", "2026-07-16", "planetscope", sensor="superdove"
    )
    n_m = masked[masked["index"] == "NDVI"]["cell"].nunique()
    n_u = unmasked[unmasked["index"] == "NDVI"]["cell"].nunique()
    assert n_m < n_u  # cloudy cells dropped
    nd = masked[masked["index"] == "NDVI"].set_index("cell")["value"]
    expected = 0.18 + 0.065 * _truth_cells()
    assert np.abs(nd.to_numpy() - expected[nd.index]).max() < 0.02


def test_harmonise_maps_sources_to_sentinel2_and_drops_bad_ones(tmp_path):
    s2 = synthetic.image_features(GRID).assign(source="sentinel2")
    drone, _ = raster_features(
        synthetic.drone_ortho(tmp_path / "d.tif"),
        GRID,
        "EPSG:3067",
        "2026-06-30",
        "drone",
        sensor="mavic3m",
    )
    rng = np.random.default_rng(5)
    junk = s2[s2["date"] == "2026-06-30"].assign(
        source="junk", date="2026-07-01", value=lambda d: rng.uniform(0.2, 0.9, len(d))
    )
    merged, rep = harmonize(pd.concat([s2, drone, junk], ignore_index=True))
    nd = rep["sources"]["drone"]["indices"]["NDVI"]
    assert nd["used"] and nd["r2"] > 0.9
    assert nd["slope"] == pytest.approx(0.06 / 0.07, rel=0.05)  # S2 gain / drone gain
    assert not rep["sources"]["junk"]["used"]
    assert "weak fit" in rep["sources"]["junk"]["indices"]["NDVI"]["reason"]
    assert "GNDVI" in rep["sources"]["drone"]["indices"]  # reference lacks GNDVI -> unused
    assert set(merged["source"]) == {"sentinel2", "drone"}
    # harmonised drone NDVI on 06-30 now matches the S2 NDVI of the same day
    c = collapse(merged)
    assert len(c) == len(c.drop_duplicates(["cell", "date", "index"]))


def _chain_with_imagery(tmp_path, entries, s2=True):
    from yieldsuite.config import load_config
    from yieldsuite.l4.io import read_bands
    from yieldsuite.levels import Level
    from yieldsuite.pipeline import run

    cfg_path = synthetic.write_demo(tmp_path)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["imagery"] = (
        [{"kind": "sentinel2", "start": "2026-05-01", "end": "2026-08-16"}] if s2 else []
    ) + entries
    cfg_path.write_text(yaml.safe_dump(raw))
    cfg = load_config(cfg_path)
    s4 = run(cfg, Level.L4)
    grid = read_bands(s4.outputs["L4"])[1]
    if s2:
        synthetic.image_features(grid).to_csv(
            tmp_path / "output" / "L5_synthetic-01_2026_s2_features.csv", index=False
        )
    s5 = run(cfg, Level.L5)
    assert s5.ok, s5.error
    return json.loads((tmp_path / "output" / "L5_synthetic-01_2026_model_card.json").read_text())


def test_chain_with_sentinel2_drone_and_planetscope(tmp_path):
    synthetic.drone_ortho(tmp_path / "drone.tif")
    synthetic.planetscope_sr(tmp_path / "ps.tif", tmp_path / "udm2.tif")
    card = _chain_with_imagery(
        tmp_path,
        [
            {"kind": "drone", "path": "drone.tif", "date": "2026-06-30", "sensor": "mavic3m"},
            {
                "kind": "planetscope",
                "path": "ps.tif",
                "date": "2026-07-16",
                "sensor": "superdove",
                "mask": "udm2.tif",
            },
        ],
    )
    src = card["sources"]
    assert src["used"] == ["drone", "planetscope", "sentinel2"]
    assert src["harmonisation"]["sources"]["planetscope"]["indices"]["NDVI"]["used"]
    assert set(src["ablation_cv_rmse"]) == {"drone", "planetscope", "sentinel2"}
    assert len(card["local_imagery"]) == 2


def test_drone_only_without_sentinel2(tmp_path):
    synthetic.drone_ortho(tmp_path / "drone.tif")
    card = _chain_with_imagery(
        tmp_path,
        [{"kind": "drone", "path": "drone.tif", "date": "2026-06-30", "sensor": "mavic3m"}],
        s2=False,
    )
    assert card["sources"]["used"] == ["drone"]
    assert card["fit"]["r2"] > 0.8
