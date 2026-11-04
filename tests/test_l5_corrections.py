"""L5 corrections from the design document on synthetic fields with known answers.

Correction 1 (residual shift) is a check that recommends a delay; correction 2
levels pass stripes with a random pass effect and keeps the field total.
"""

import json

import numpy as np
import pandas as pd
import pytest
import yaml

from yieldsuite import synthetic
from yieldsuite.config import load_config
from yieldsuite.l4.grid import Grid
from yieldsuite.l5.records import sample_bilinear
from yieldsuite.l5.stripes import pass_effects
from yieldsuite.levels import Level
from yieldsuite.pipeline import run

pytest.importorskip("rasterio")
pytest.importorskip("gstools")


def test_bilinear_reproduces_a_plane():
    g = Grid(0.0, 100.0, 20.0, 5, 5)
    rows, cols = np.divmod(np.arange(25), 5)
    plane = 2.0 + 0.01 * (cols * 20 + 10) + 0.02 * (100 - rows * 20 - 10)
    x, y = np.array([23.0, 51.0, 77.0]), np.array([66.0, 41.0, 18.0])
    assert sample_bilinear(g, plane, x, y) == pytest.approx(2 + 0.01 * x + 0.02 * y)


def test_pass_effects_recover_gains_and_ignore_noise():
    rng = np.random.default_rng(0)
    passes = np.repeat(np.arange(30), 40)
    unit = np.tile(np.repeat(np.arange(8), 5), 30)
    true_b = rng.normal(0, 0.08, 30)
    resid = true_b[passes] + rng.normal(0, 0.03, passes.size)
    b, stats = pass_effects(resid, passes, unit)
    assert stats["applied"] and abs(stats["sigma_b"] - 0.08) < 0.03
    est = np.array([b[p] for p in range(30)])
    assert np.corrcoef(est, true_b)[0, 1] > 0.95
    _, stats = pass_effects(rng.normal(0, 0.03, passes.size), passes, unit)
    assert not stats["applied"]


def _l5_case(tmp_path, tau=None, **field_kwargs):
    from yieldsuite.l4.io import read_bands
    from yieldsuite.l5.fuse import cell_centres

    cfg_path = synthetic.write_demo(tmp_path, **field_kwargs)
    raw = yaml.safe_load(cfg_path.read_text())
    raw["imagery"] = [{"kind": "sentinel2", "start": "2026-05-01", "end": "2026-08-16"}]
    if tau is not None:
        raw["machines"][0].update(tau_d_s=tau, tau_m_s=tau)
    cfg_path.write_text(yaml.safe_dump(raw))
    cfg = load_config(cfg_path)
    s4 = run(cfg, Level.L4)
    b4, grid, _ = read_bands(s4.outputs["L4"])
    feats = tmp_path / "output" / "L5_synthetic-01_2026_s2_features.csv"
    synthetic.image_features(grid).to_csv(feats, index=False)
    s5 = run(cfg, Level.L5)
    assert s5.ok, s5.error
    b5, _, _ = read_bands(tmp_path / "output" / "L5_synthetic-01_2026.tif")
    card = json.loads((tmp_path / "output" / "L5_synthetic-01_2026_model_card.json").read_text())
    _, _, xy = cell_centres(grid)
    truth = synthetic.true_yield(xy[:, 0], xy[:, 1]).reshape(grid.ny, grid.nx)
    good = b4["coverage"] > 0.8

    def rmse(b):
        return float(np.sqrt(np.mean((b["yield"][good] - truth[good]) ** 2)))

    return rmse(b4), rmse(b5), card["record_corrections"], s5


def test_stripes_are_levelled(tmp_path):
    r4, r5, rc, s5 = _l5_case(tmp_path, pass_gain_sd=0.10)
    st = rc["pass_stripes"]
    assert st["applied"] and abs(st["sigma_b"] - 0.10) < 0.03 and st["mass_conserved"]
    assert st["field_total_kg"]["after"] == pytest.approx(st["field_total_kg"]["before"])
    assert r5 < 0.5 * r4
    assert any("pass stripes levelled" in n for n in s5.notes)


def test_clean_field_is_left_alone(tmp_path):
    r4, r5, rc, _ = _l5_case(tmp_path)
    assert not rc["pass_stripes"]["applied"]
    assert not rc["residual_shift"]["significant"]
    assert r5 == pytest.approx(r4, abs=1e-6)


def test_wrong_delay_is_reported_not_edited(tmp_path):
    r4, r5, rc, s5 = _l5_case(tmp_path, tau=0.0)
    sh = rc["residual_shift"]
    assert sh["significant"] and abs(sh["suggested_extra_delay_s"] - synthetic.TAU_D) <= 3
    assert any("image suggests tau_d" in n for n in s5.notes)


def test_l4_only_run_skips_record_corrections(tmp_path, example_config_path):
    from test_l5 import GRID, synthetic_field

    from yieldsuite.l5.run import run_sentinel2_fusion

    cfg = load_config(example_config_path)
    cfg.crop = None
    l4, long_df, _, _ = synthetic_field()
    feats = tmp_path / "f.csv"
    pd.DataFrame(long_df).to_csv(feats, index=False)
    out = run_sentinel2_fusion(cfg, l4, GRID, features_path=feats, out_dir=tmp_path)
    rc = json.loads(out.model_card.read_text())["record_corrections"]
    assert rc["applied"] is False and "L3 records" in rc["reason"]
