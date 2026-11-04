from yieldsuite.cli import main
from yieldsuite.config import load_config
from yieldsuite.levels import Level, ProductClass
from yieldsuite.pipeline import plan, run


def test_load_example_config(example_config_path):
    cfg = load_config(example_config_path)
    assert cfg.field_id == "demo-field-01"
    assert cfg.validate() == []
    assert cfg.product_class is ProductClass.ABSOLUTE
    assert cfg.machines["combine-a"].antenna_offset_m == (3.2, 0.0)
    assert cfg.slack.channel == "#yield-data-suite"
    assert len(cfg.machines["combine-a"].param_hash()) == 16
    s2 = cfg.sentinel2
    assert (s2.start, s2.end, s2.indices) == (
        "2026-05-01",
        "2026-08-16",
        ("NDVI", "NDRE", "CIre", "NDMI"),
    )


def test_plan_and_missing_files(example_config_path):
    cfg = load_config(example_config_path)
    stages = plan(cfg, Level.L4)
    assert [s.target for s in stages] == [Level.L1A, Level.L1B, Level.L2, Level.L3, Level.L4]
    assert all(s.implemented for s in plan(cfg, Level.L5))
    summary = run(cfg, Level.L4)
    assert not summary.ok and "not found" in summary.error


def test_cli_validate_and_plan(example_config_path, capsys):
    assert main(["validate", str(example_config_path)]) == 0
    assert main(["plan", str(example_config_path), "--to", "L5"]) == 0
    assert "L5" in capsys.readouterr().out
