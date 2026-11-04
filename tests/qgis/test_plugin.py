"""The QGIS plugin's Processing algorithms, run in a headless QGIS.

Skipped unless QGIS's Python bindings are importable (run with the Python
that QGIS uses, e.g. `QT_QPA_PLATFORM=offscreen python3 -m pytest tests/qgis`).
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
qgis_core = pytest.importorskip("qgis.core")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "qgis_plugin"))
sys.path.insert(0, str(ROOT / "tests"))
for p in ("/usr/share/qgis/python/plugins", "/usr/share/qgis/python"):
    if Path(p).exists() and p not in sys.path:
        sys.path.append(p)

from qgis.core import (  # noqa: E402
    QgsApplication,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingFeedback,
    QgsProject,
)

from yieldsuite import synthetic  # noqa: E402


@pytest.fixture(scope="module")
def qgis_app():
    QgsApplication.setPrefixPath("/usr", True)
    app = QgsApplication([], False)
    app.initQgis()
    from processing.core.Processing import Processing

    Processing.initialize()
    from yieldsuite_qgis.provider import YieldSuiteProvider

    provider = YieldSuiteProvider()
    QgsApplication.processingRegistry().addProvider(provider)
    yield app
    QgsApplication.processingRegistry().removeProvider(provider)


def _run(alg_id, params):
    import processing

    context = QgsProcessingContext()
    context.setProject(QgsProject.instance())
    feedback = QgsProcessingFeedback()
    result = processing.run(f"yieldsuite:{alg_id}", params, context=context, feedback=feedback)
    return result, context


def test_provider_lists_algorithms(qgis_app):
    p = QgsApplication.processingRegistry().providerById("yieldsuite")
    names = sorted(a.name() for a in p.algorithms())
    assert names == [
        "estimate_delay",
        "fuse_sentinel2",
        "inspect_file",
        "parcel_boundary",
        "run_chain",
        "weather",
        "zones",
    ]


def test_inspect_file(qgis_app, tmp_path):
    synthetic.make_field(0).log.to_csv(tmp_path / "log.csv", index=False)
    res, _ = _run(
        "inspect_file",
        {
            "INPUT": str(tmp_path / "log.csv"),
            "CRS": "EPSG:3067",
            "MACHINE": "combine-a",
            "OUTPUT_YAML": str(tmp_path / "entry.yaml"),
        },
    )
    assert "mass_flow" in res["REPORT"]
    assert "harvest_logs" in (tmp_path / "entry.yaml").read_text()


def test_run_chain_loads_products(qgis_app, tmp_path):
    cfg = synthetic.write_demo(tmp_path)
    res, context = _run("run_chain", {"CONFIG": str(cfg), "TO_LEVEL": 0, "LOAD": True})
    assert Path(res["L4"]).exists() and "mass check: OK" in res["MESSAGE"]
    to_load = context.layersToLoadOnCompletion()
    assert res["L4"] in to_load and res["L3"] in to_load


def test_estimate_delay(qgis_app, tmp_path):
    res, _ = _run("estimate_delay", {"CONFIG": str(synthetic.write_demo(tmp_path))})
    assert "estimated" in res["MESSAGE"]


def test_fuse_with_cached_features(qgis_app, tmp_path):
    import yaml

    from yieldsuite.l4.io import read_bands

    cfg_path = synthetic.write_demo(tmp_path)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["imagery"] = [{"kind": "sentinel2", "start": "2026-05-01", "end": "2026-08-16"}]
    cfg_path.write_text(yaml.safe_dump(raw))
    res, _ = _run("run_chain", {"CONFIG": str(cfg_path), "TO_LEVEL": 0, "LOAD": False})
    grid = read_bands(res["L4"])[1]
    feats = tmp_path / "features.csv"
    synthetic.image_features(grid).to_csv(feats, index=False)
    res5, context = _run(
        "fuse_sentinel2",
        {"CONFIG": str(cfg_path), "L4": res["L4"], "METHOD": 0, "FEATURES": str(feats)},
    )
    assert Path(res5["L5"]).exists() and "filled" in res5["MESSAGE"]
    assert res5["L5"] in context.layersToLoadOnCompletion()


def test_zones(qgis_app, tmp_path):
    from yieldsuite.l4.io import write_cog

    grid, layers, _, _ = synthetic.multi_year_layers(n_years=3)
    paths = [
        str(write_cog({"yield": y}, grid, "EPSG:3067", tmp_path / f"L5_{i}.tif", names=["yield"]))
        for i, y in enumerate(layers)
    ]
    res, context = _run("zones", {"LAYERS": paths, "K_MAX": 5, "OUTPUT": str(tmp_path / "zones")})
    assert res["MESSAGE"].startswith("3 zones")
    assert Path(res["ZONES"]).exists()
    assert res["ZONES"] in context.layersToLoadOnCompletion()


def test_errors_reach_the_user(qgis_app, tmp_path):
    bad = tmp_path / "field.yaml"
    bad.write_text("field_id: x\nyear: 2026\n")
    with pytest.raises(QgsProcessingException, match="harvest log or imagery"):
        _run("run_chain", {"CONFIG": str(bad), "TO_LEVEL": 0, "LOAD": False})


def test_parcel_boundary(qgis_app, tmp_path, monkeypatch):
    from test_parcels import FakeWFS

    from yieldsuite.public import http
    from yieldsuite.public import parcels as parcels_mod

    fake = FakeWFS()
    monkeypatch.setattr(parcels_mod, "_get_text", lambda url, get=None: fake.text(url))
    monkeypatch.setattr(http, "get_json", fake.json)
    out = str(tmp_path / "boundary.gpkg")
    res, context = _run("parcel_boundary", {"IDS": "123-4567-890", "YEAR": 2026, "OUTPUT": out})
    assert "PERUSLOHKOT_2026" in res["MESSAGE"] and Path(out).exists()
    assert out in context.layersToLoadOnCompletion()
