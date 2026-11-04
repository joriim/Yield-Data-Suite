"""End-to-end L1A -> L4 on a synthetic field with a known true yield."""

import json

import numpy as np
import pytest

from yieldsuite import synthetic
from yieldsuite.cli import main
from yieldsuite.config import load_config
from yieldsuite.l2.track import (
    derive_heading,
    new_segments,
    segment_lengths,
    segment_passes,
    shift_signal,
)
from yieldsuite.levels import Level
from yieldsuite.pipeline import run
from yieldsuite.schema import Flag


def test_shift_signal_and_track_helpers():
    t = np.arange(10.0)
    v = t * 2
    out = shift_signal(t, v, 3.0)
    assert out[0] == 6.0 and np.isnan(out[-1])
    t_gap = np.r_[np.arange(5.0), np.arange(20.0, 25.0)]
    assert np.isnan(shift_signal(t_gap, np.ones(10), 2.0)[4])  # would cross the gap
    h = derive_heading([0, 0, 1], [0, 1, 1])
    assert h == pytest.approx([0, 0, np.pi / 2])
    # A jump after a logging gap must not become a long polygon or a turn.
    t = np.array([0.0, 1, 2, 30, 31])
    x = np.array([0.0, 0, 0, 6, 6])
    y = np.array([0.0, 1.5, 3, 50, 48.5])
    starts = new_segments(t)
    assert list(starts) == [True, False, False, True, False]
    assert segment_lengths(x, y, np.full(5, 1.5), np.ones(5), starts) == pytest.approx([1.5] * 5)
    assert derive_heading(x, y, starts)[3] == pytest.approx(np.pi)
    pid = segment_passes(np.arange(6.0), [1, 1, 0, 0, 1, 1])
    assert list(pid) == [0, 0, -1, -1, 1, 1]


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    cfg = load_config(synthetic.write_demo(out))
    summary = run(cfg, Level.L4)
    return cfg, summary


def test_chain_runs_and_conserves_mass(chain):
    cfg, summary = chain
    assert summary.ok, summary.error
    assert summary.completed == [Level.L1A, Level.L1B, Level.L2, Level.L3, Level.L4]
    assert summary.mass_ok is True
    # The monitor reads 7 % high; scale tickets bring it back. Injected spikes
    # are outliers and must not inflate the monitor total.
    assert summary.k_factors["combine-a"] == pytest.approx(1 / synthetic.MONITOR_GAIN, rel=0.005)
    assert 0 < summary.flagged_pct < 40


def test_chain_recovers_true_yield(chain, tmp_path):
    pytest.importorskip("rasterio")
    from yieldsuite.l4.io import read_bands
    from yieldsuite.l5.fuse import cell_centres

    cfg, summary = chain
    bands, grid, _ = read_bands(summary.outputs["L4"])
    assert (grid.nx, grid.ny) == (12, 8)  # 240 x 160 m field at 20 m
    _, _, xy = cell_centres(grid)
    truth = synthetic.true_yield(xy[:, 0], xy[:, 1]).reshape(grid.ny, grid.nx)
    good = bands["coverage"] > 0.8
    assert good.sum() >= 60
    err = bands["yield"][good] - truth[good]
    assert abs(np.mean(err)) < 0.05  # level: reconciliation and moisture
    assert np.sqrt(np.mean(err**2)) < 0.35  # pattern: delay, offset, gridding

    qa = json.loads(open(summary.outputs["qa"]).read())
    assert qa["mass_conservation"]["ok"]
    assert qa["flags"]["GLOBAL_OUTLIER"] >= 10  # the injected spikes
    assert qa["flags"][Flag.FILL_FINISH.name] > 0


def test_wrong_delay_makes_map_worse(tmp_path):
    pytest.importorskip("rasterio")
    from yieldsuite.l4.io import read_bands
    from yieldsuite.l5.fuse import cell_centres

    def rmse(tau):
        cfg = load_config(synthetic.write_demo(tmp_path / f"tau{tau}"))
        prof = cfg.machines["combine-a"]
        cfg.machines["combine-a"] = type(prof)(**{**prof.__dict__, "tau_d_s": tau})
        s = run(cfg, Level.L4)
        bands, grid, _ = read_bands(s.outputs["L4"])
        _, _, xy = cell_centres(grid)
        truth = synthetic.true_yield(xy[:, 0], xy[:, 1]).reshape(grid.ny, grid.nx)
        good = bands["coverage"] > 0.8
        return np.sqrt(np.mean((bands["yield"][good] - truth[good]) ** 2))

    assert rmse(12.0) < rmse(0.0)


def test_cli_demo_and_run(tmp_path, capsys):
    assert main(["demo", str(tmp_path)]) == 0
    assert main(["run", str(tmp_path / "field.yaml"), "--to", "L4"]) == 0
    out = capsys.readouterr().out
    assert "mass check OK" in out and "combine-a=" in out


# --- flow delay estimation ------------------------------------------------------


def _profile_without_delay(cfg, tau=None):
    prof = cfg.machines["combine-a"]
    cfg.machines["combine-a"] = type(prof)(**{**prof.__dict__, "tau_d_s": tau, "tau_m_s": tau})
    return cfg


def test_delay_estimated_when_profile_has_none(tmp_path):
    from yieldsuite.pipeline import estimate_delays

    cfg = _profile_without_delay(load_config(synthetic.write_demo(tmp_path)))
    d = estimate_delays(cfg)["combine-a"]
    assert d["identifiable"] and abs(d["estimated_tau_d_s"] - synthetic.TAU_D) <= 1.0
    assert d["used_source"] == "estimated from opposite passes"
    s = run(cfg, Level.L4)
    assert s.ok and s.mass_ok


def test_wrong_profile_delay_is_flagged(tmp_path):
    cfg = _profile_without_delay(load_config(synthetic.write_demo(tmp_path)), tau=20.0)
    s = run(cfg, Level.L4)
    qa = json.loads(open(s.outputs["qa"]).read())
    assert any("data suggest tau_d" in w for w in qa["warnings"])


def test_flat_field_delay_not_identifiable(tmp_path, monkeypatch):
    monkeypatch.setattr(synthetic, "true_yield", lambda x, y: np.full(np.shape(x), 7.0))
    from yieldsuite.pipeline import estimate_delays

    cfg = _profile_without_delay(load_config(synthetic.write_demo(tmp_path)))
    d = estimate_delays(cfg)["combine-a"]
    assert not d["identifiable"] and d["used_source"] == "default"


def test_cli_delay(tmp_path, capsys):
    assert main(["delay", str(synthetic.write_demo(tmp_path))]) == 0
    assert "estimated" in capsys.readouterr().out


def test_local_outliers_in_the_chain(tmp_path):
    import pandas as pd

    from yieldsuite import stages
    from yieldsuite.timeutil import epoch_seconds

    cfg_path = synthetic.write_demo(tmp_path)
    log = synthetic.make_field(0).log
    rng = np.random.default_rng(3)
    on = np.flatnonzero(log["Header"] == 1)
    picks = rng.choice(on[(on % 120 > 30) & (on % 120 < 90)], 12, replace=False)
    log.loc[picks, "Mass_Flow_kg_s"] *= 0.45  # local drops (e.g. a partial blockage)
    log.to_csv(tmp_path / "combine-a_2026.csv", index=False)
    cfg = load_config(cfg_path)
    st = {}
    for stage in (
        stages.stage_ingest,
        stages.stage_mass_flow,
        stages.stage_georeference,
        stages.stage_clean,
    ):
        stage(cfg, st)
    rec = st["records"]
    flags = rec["flags"].to_numpy()
    injected = np.isin(
        np.round(rec["t"].to_numpy() + synthetic.TAU_D),
        np.round(epoch_seconds(pd.Series(log.loc[picks, "Time"]))),
    )
    caught = (flags & int(Flag.LOCAL_OUTLIER | Flag.GLOBAL_OUTLIER)) != 0
    assert injected.sum() >= 10 and (caught & injected).sum() >= 0.7 * injected.sum()
    false_local = ((flags & int(Flag.LOCAL_OUTLIER)) != 0) & ~injected
    assert false_local.sum() <= 0.002 * rec["header_down"].sum()
    assert st["qa"]["local_outliers"]["records"] >= 5
