"""L5 Sentinel-2 fusion, tested offline on a synthetic field with a known answer."""

import json

import numpy as np
import pandas as pd
import pytest

from yieldsuite.l4 import Grid
from yieldsuite.l5 import S2Request, feature_matrix, fit_yield_image_model, fuse, gap_fill
from yieldsuite.l5.fuse import cell_centres
from yieldsuite.l5.gapfill import SOURCE_FILLED, SOURCE_MEASURED
from yieldsuite.l5.indices import INDICES, required_bands
from yieldsuite.l5.sentinel2 import parse_timeseries_json

GRID = Grid(xmin=400_000.0, ymax=6_950_000.0, resolution=20.0, nx=30, ny=30)
CRS = "EPSG:3067"


def synthetic_field(seed=0):  # noqa: D401 - also used by test_weather
    """NDVI pattern + smooth residual -> yield. Returns (l4 bands, long features, truth)."""
    rng = np.random.default_rng(seed)
    rows, cols, xy = cell_centres(GRID)
    u = (xy[:, 0] - GRID.xmin) / 600
    v = (GRID.ymax - xy[:, 1]) / 600
    ndvi_season = 0.55 + 0.2 * np.sin(2 * np.pi * u) * np.cos(np.pi * v)
    smooth_resid = 0.6 * np.sin(3 * np.pi * v)  # spatially smooth, not seen by imagery
    truth = 1.5 + 9.0 * ndvi_season + smooth_resid
    y = truth + rng.normal(0, 0.15, truth.size)

    records = []
    dates = ["2026-06-01", "2026-06-21", "2026-07-11", "2026-07-26"]
    for k, date in enumerate(dates):
        ndvi = ndvi_season + 0.03 * (k - 1.5) + rng.normal(0, 0.01, truth.size)
        ndre = 0.6 * ndvi + rng.normal(0, 0.01, truth.size)
        cloudy = date == "2026-07-11"
        for cid in range(truth.size):
            if cloudy and rng.random() < 0.7:
                continue  # 70 % of cells cloud-masked -> date must be dropped
            records.append((cid, date, "NDVI", ndvi[cid]))
            records.append((cid, date, "NDRE", ndre[cid]))
    long_df = pd.DataFrame.from_records(records, columns=["cell", "date", "index", "value"])

    coverage = np.ones(truth.size)
    hidden = (rows >= 10) & (rows < 18) & (cols >= 12) & (cols < 20)  # a gap in the harvest data
    coverage[hidden] = 0.0
    y_obs = np.where(hidden, np.nan, y)
    shape = (GRID.ny, GRID.nx)
    l4 = {"yield": y_obs.reshape(shape), "coverage": coverage.reshape(shape)}
    return l4, long_df, truth, hidden


def test_indices_on_numpy():
    bands = {
        "B04": np.array([0.05]),
        "B08": np.array([0.45]),
        "B05": np.array([0.1]),
        "B07": np.array([0.3]),
        "B8A": np.array([0.4]),
        "B11": np.array([0.2]),
    }
    get = bands.__getitem__
    assert INDICES["NDVI"].formula(get)[0] == pytest.approx(0.8)
    assert INDICES["CIre"].formula(get)[0] == pytest.approx(2.0)
    assert required_bands(["NDVI", "NDRE"]) == ["B04", "B05", "B08", "B8A"]


def test_process_graph_offline():
    pytest.importorskip("openeo")
    pytest.importorskip("pyproj")
    from yieldsuite.l5.sentinel2 import bbox_wgs84, build_process_graph, grid_cells_geojson

    small = Grid(GRID.xmin, GRID.ymax, 20.0, 3, 2)
    fc = grid_cells_geojson(small, CRS)
    assert [f["properties"]["cell"] for f in fc["features"]] == list(range(6))
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and 20 < ring[0][0] < 32 and 59 < ring[0][1] < 71

    req = S2Request(start="2026-05-01", end="2026-08-16", indices=("NDVI", "NDRE"))
    graph = build_process_graph(None, fc, bbox_wgs84(small, CRS), req).flat_graph()
    procs = {n["process_id"]: n["arguments"] for n in graph.values()}
    assert {"load_collection", "mask", "apply_dimension", "aggregate_spatial"} <= set(procs)
    load = procs["load_collection"]
    assert load["id"] == "SENTINEL2_L2A"
    assert load["bands"] == ["B04", "B05", "B08", "B8A", "SCL"]
    assert load["temporal_extent"] == ["2026-05-01", "2026-08-16"]
    assert procs["rename_labels"]["target"] == ["NDVI", "NDRE"]


def test_parse_timeseries_json():
    ts = {
        "2026-06-01T00:00:00Z": [[0.5, 0.3], [], [None, 0.2]],
        "2026-06-11T00:00:00Z": [[0.7, 0.4], [0.6, 0.35], [0.1, 0.1]],
    }
    df = parse_timeseries_json(ts, [10, 11, 12], ["NDVI", "NDRE"])
    assert len(df) == 2 + 1 + 6
    row = df[(df.cell == 12) & (df.date == "2026-06-01")]
    assert list(row["index"]) == ["NDRE"]
    with pytest.raises(ValueError):
        parse_timeseries_json(ts, [10, 11], ["NDVI", "NDRE"])


def test_feature_matrix_drops_cloudy_date():
    _, long_df, truth, _ = synthetic_field()
    V, info = feature_matrix(long_df, truth.size)
    assert info.dates_dropped == ["2026-07-11"]
    assert len(info.dates_used) == 3
    assert list(V.columns) == ["NDRE_mean", "NDRE_max", "NDVI_mean", "NDVI_max"]
    assert V.notna().all().all()


def test_linear_model_and_block_cv():
    l4, long_df, truth, hidden = synthetic_field()
    V, _ = feature_matrix(long_df, truth.size)
    rows, cols, _ = cell_centres(GRID)
    m = fit_yield_image_model(l4["yield"].ravel(), V, ~hidden, rows=rows, cols=cols)
    assert m.stats["n_train"] == (~hidden).sum()
    assert m.stats["r2"] > 0.8
    assert 0 < m.stats["cv_rmse"] < 1.0


def test_random_forest_runs():
    pytest.importorskip("sklearn")
    l4, long_df, truth, hidden = synthetic_field()
    V, _ = feature_matrix(long_df, truth.size, per_date=True)
    m = fit_yield_image_model(l4["yield"].ravel(), V, ~hidden, method="rf")
    assert m.stats["r2"] > 0.8 and np.isfinite(m.predict(V)).all()


def test_kriging_beats_model_only_in_gap():
    pytest.importorskip("gstools")
    l4, long_df, truth, hidden = synthetic_field()
    V, _ = feature_matrix(long_df, truth.size)
    y = l4["yield"].ravel()
    m = fit_yield_image_model(y, V, ~hidden)
    pred = m.predict(V)
    _, _, xy = cell_centres(GRID)
    rk = gap_fill(y, pred, ~hidden, xy, sigma_f=m.stats["rmse"], resolution=20.0)
    only = gap_fill(y, pred, ~hidden, xy, sigma_f=m.stats["rmse"], resolution=20.0, residual="none")

    def rmse(out):
        return np.sqrt(np.mean((out["yield"][hidden] - truth[hidden]) ** 2))

    assert (rk["source"][hidden] == SOURCE_FILLED).all()
    assert (rk["source"][~hidden] == SOURCE_MEASURED).all()
    np.testing.assert_array_equal(rk["yield"][~hidden], y[~hidden])  # measured kept
    assert np.isnan(rk["sigma"][~hidden]).all() and (rk["sigma"][hidden] > 0).all()
    assert rmse(rk) < rmse(only)
    assert rk["variogram"]["len_scale_m"] >= 20.0


def test_fuse_end_to_end():
    pytest.importorskip("gstools")
    l4, long_df, truth, hidden = synthetic_field()
    req = S2Request(start="2026-05-01", end="2026-08-16", indices=("NDVI", "NDRE"))
    res = fuse(l4, GRID, long_df, request=req)
    card = res.model_card
    assert card["cells"]["filled"] == hidden.sum()
    assert card["sentinel2"]["dates_dropped"] == ["2026-07-11"]
    assert card["fit"]["cv_rmse"] > 0
    assert res.bands["yield"].shape == (GRID.ny, GRID.nx)
    filled = res.bands["yield"].ravel()[hidden]
    assert np.sqrt(np.mean((filled - truth[hidden]) ** 2)) < 0.5
    assert np.nanmean(res.bands["relative_yield"]) == pytest.approx(100.0)
    json.dumps(card, default=str)


def test_cli_fuse_offline(tmp_path, example_config_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("gstools")
    from yieldsuite.cli import main
    from yieldsuite.l4.io import read_bands, write_cog
    from yieldsuite.l5.sentinel2 import save_features
    from yieldsuite.schema import L5_BANDS

    l4, long_df, _, hidden = synthetic_field()
    l4_path = write_cog(l4, GRID, CRS, tmp_path / "l4.tif", names=["yield", "coverage"])
    feats = save_features(long_df, tmp_path / "features.csv")
    rc = main(
        [
            "fuse",
            str(example_config_path),
            "--l4",
            str(l4_path),
            "--features",
            str(feats),
            "--out",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 0
    bands, grid, crs = read_bands(tmp_path / "out" / "L5_demo-field-01_2026.tif")
    assert set(L5_BANDS) <= set(bands) and grid == GRID and "3067" in crs
    assert (bands["source"].ravel()[hidden] == SOURCE_FILLED).all()
    card = json.loads((tmp_path / "out" / "L5_demo-field-01_2026_model_card.json").read_text())
    assert card["field_id"] == "demo-field-01" and card["product_class"] == "absolute"
