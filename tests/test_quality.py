"""Grain quality channel (GrainSense-style analyzer) on a synthetic field."""

import json

import numpy as np
import pandas as pd
import pytest
import yaml

from yieldsuite import synthetic
from yieldsuite.config import load_config
from yieldsuite.levels import Level
from yieldsuite.pipeline import run
from yieldsuite.quality import GrainSenseClient, nutrient_percentages, read_quality


def test_percentages_follow_the_documented_formula():
    mg = {"protein": 120.0, "carbohydrate": 650.0, "oil": 30.0, "moisture": 200.0}
    out = nutrient_percentages(mg, {"protein_moisture_basis": "dry", "oil_moisture_basis": "wet"})
    assert out["protein_pct"] == pytest.approx(100 * 120 / 800)  # dry: / (p + c + o)
    assert out["oil_pct"] == pytest.approx(100 * 30 / 1000)  # wet: / total
    assert out["moisture_pct"] == pytest.approx(20.0)
    with pytest.raises(NotImplementedError):
        nutrient_percentages(mg, {"protein_moisture_basis": "fixed"})


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return (
            self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode()
        )


def test_client_login_sessions_and_export(monkeypatch):
    calls = []
    pages = {
        0: {"results": [{"uuid": "a"}, {"uuid": "b"}], "next": "more"},
        2: {"results": [{"uuid": "c"}], "next": None},
    }

    def opener(req, timeout):
        calls.append(req)
        url = req.full_url
        if url.endswith("/v1/login/"):
            assert json.loads(req.data) == {"email": "e@x", "password": "pw"}
            return _Resp({"token": "abc"})
        if "measurementSessions/?" in url:
            assert req.headers["Authorization"] == "JWT abc"
            offset = int(url.split("offset=")[1].split("&")[0])
            return _Resp(pages[offset])
        if url.endswith("measurementSessions/export/"):
            assert json.loads(req.data)["measurement_sessions"] == ["a"]
            return _Resp(b"xlsx-bytes")
        raise AssertionError(url)

    monkeypatch.setenv("GRAINSENSE_EMAIL", "e@x")
    monkeypatch.setenv("GRAINSENSE_PASSWORD", "pw")
    c = GrainSenseClient("https://gs.example/api", opener=opener)
    assert c.login() == "abc"
    assert [s["uuid"] for s in c.flow_sessions(start="2026-08-01T00:00:00Z")] == ["a", "b", "c"]
    assert c.export_flow_sessions(["a"]) == b"xlsx-bytes"
    assert "measured_at__gte=2026-08-01" in calls[1].full_url


def test_read_quality_recognises_columns_and_fractions(tmp_path):
    df = pd.DataFrame(
        {
            "Mittausaika": ["10.8.2026 10:00:05", "10.8.2026 10:00:10"],
            "Proteiini": [0.125, 0.13],
            "Kosteus": [0.18, 0.19],
        }
    )
    path = tmp_path / "q.csv"
    df.to_csv(path, sep=";", index=False)
    r = read_quality(path)
    assert r.report["mapping"]["protein"] == "Proteiini"
    assert r.measurements["protein_pct"].tolist() == pytest.approx([12.5, 13.0])
    assert r.measurements["moisture_pct"].tolist() == pytest.approx([18.0, 19.0])
    assert r.report["median_interval_s"] == 5.0


def _quality_case(tmp_path, lab=True, basis="dry"):
    cfg_path = synthetic.write_demo(tmp_path)
    export, lab_df = synthetic.quality_export(synthetic.make_field(0))
    export.to_csv(tmp_path / "gs.csv", index=False)
    lab_df.to_csv(tmp_path / "lab.csv", index=False)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["quality"] = {
        "sources": [{"path": "gs.csv", "machine_id": "combine-a"}],
        "n_factor": 5.7,
        "protein_basis": basis,
    }
    if lab:
        raw["quality"]["lab_samples"] = "lab.csv"
    cfg_path.write_text(yaml.safe_dump(raw))
    s = run(load_config(cfg_path), Level.L4)
    assert s.ok, s.error
    return s


def test_quality_map_with_lab_calibration(tmp_path):
    pytest.importorskip("rasterio")
    from yieldsuite.l4.io import read_bands
    from yieldsuite.l5.fuse import cell_centres

    s = _quality_case(tmp_path)
    qa = json.loads(open(s.outputs["Q4_qa"]).read())
    assert qa["lab_calibration"]["applied"]
    assert qa["lab_calibration"]["bias_pct_points"] == pytest.approx(-0.5, abs=0.1)
    assert qa["product_class"] == "absolute"
    assert qa["sources"][0]["mapping"]["moisture"] == "Moisture (%)"
    b, grid, _ = read_bands(s.outputs["Q4"], require_yield=False)
    _, _, xy = cell_centres(grid)
    truth = synthetic.true_protein(xy[:, 0], xy[:, 1]).reshape(grid.ny, grid.nx)
    good = b["quality_mass_share"] > 0.8
    err = b["protein_pct_dm"][good] - truth[good]
    assert abs(err.mean()) < 0.1 and np.sqrt((err**2).mean()) < 0.15
    # protein yield and N uptake follow from yield x dry matter x protein
    l4, _, _ = read_bands(s.outputs["L4"])
    expected = l4["yield"] * 1000 * 0.86 * b["protein_pct_dm"] / 100 / 5.7
    np.testing.assert_allclose(b["n_uptake_kg_ha"][good], expected[good], rtol=1e-5)
    assert any("lab-calibrated" in n for n in s.notes)


def test_quality_without_lab_is_relative(tmp_path):
    s = _quality_case(tmp_path, lab=False)
    qa = json.loads(open(s.outputs["Q4_qa"]).read())
    assert qa["product_class"] == "relative" and "not calibrated" in qa["note"]
    assert qa["field_mean_protein_pct_dm"] == pytest.approx(12.9, abs=0.3)  # bias kept


def test_quality_wet_basis_converted(tmp_path):
    s = _quality_case(tmp_path, lab=False, basis="wet")
    qa = json.loads(open(s.outputs["Q4_qa"]).read())
    # wet-basis numbers divided by (1 - moisture) -> higher on dry matter
    assert qa["field_mean_protein_pct_dm"] > 14.0


def test_quality_time_must_overlap(tmp_path):
    cfg_path = synthetic.write_demo(tmp_path)
    pd.DataFrame({"time": ["2025-01-01T00:00:00Z"], "protein": [12.0]}).to_csv(
        tmp_path / "gs.csv", index=False
    )
    raw = yaml.safe_load(cfg_path.read_text())
    raw["quality"] = {"sources": [{"path": "gs.csv", "machine_id": "combine-a"}]}
    cfg_path.write_text(yaml.safe_dump(raw))
    s = run(load_config(cfg_path), Level.L4)
    assert s.ok
    assert any("no matched measurements" in n for n in s.notes)
