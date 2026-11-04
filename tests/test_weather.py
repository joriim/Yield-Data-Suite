"""Field weather (ERA5 via Open-Meteo), agroclimate indices and RUE, offline."""

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from yieldsuite.weather import (
    AgroSettings,
    WeatherRequest,
    extraterrestrial_radiation,
    fapar_from_ndvi,
    field_rue,
    growing_degree_days,
    parse_response,
    season_summary,
)


def fake_payload(start="2026-04-01", days=200, lat=62.8):
    d0 = date.fromisoformat(start)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(days)]
    doy = np.array([(d0 + timedelta(days=i)).timetuple().tm_yday for i in range(days)])
    tmean = 2 + 14 * np.sin(np.pi * (doy - 90) / 200)  # cool spring, warm July
    ra = extraterrestrial_radiation(lat, doy)
    kt = 0.45 + 0.1 * np.sin(np.arange(days))  # clouds vary
    hours = [f"{d}T{h:02d}:00" for d in dates for h in range(24)]
    return {
        "latitude": 62.8,
        "longitude": 22.85,
        "elevation": 40.0,
        "daily_units": {"shortwave_radiation_sum": "MJ/m²"},
        "daily": {
            "time": dates,
            "temperature_2m_max": list(tmean + 5),
            "temperature_2m_min": list(tmean - 5),
            "temperature_2m_mean": list(tmean),
            "shortwave_radiation_sum": list(ra * kt),
            "precipitation_sum": [2.0] * days,
            "et0_fao_evapotranspiration": [2.5] * days,
            "sunshine_duration": [6 * 3600.0] * days,
            "daylight_duration": [17 * 3600.0] * days,
        },
        "hourly": {"time": hours, "soil_moisture_0_to_7cm": [0.3] * len(hours)},
    }


def test_extraterrestrial_radiation_matches_fao56_example():
    # FAO-56 example 8: 20°S on 3 September -> Ra = 32.2 MJ/m2/day
    assert extraterrestrial_radiation(-20.0, 246) == pytest.approx(32.2, abs=0.1)
    # Polar night in Finnish Lapland, midsummer near maximum
    assert extraterrestrial_radiation(69.0, 355) == pytest.approx(0.0, abs=0.01)
    assert extraterrestrial_radiation(62.8, 172) > 40


def test_gdd_and_fapar():
    assert list(growing_degree_days([3, 5, 12, 35], 5.0, t_cap=30)) == [0, 0, 7, 25]
    assert fapar_from_ndvi([0.1, 0.5, 0.95]) == pytest.approx([0.0, 0.4393, 0.95], abs=1e-3)


def test_request_rounds_location_for_privacy():
    r = WeatherRequest(lat=62.79123, lon=22.84877, start="2026-04-01", end="2026-10-31")
    assert r.rounded() == (62.8, 22.85)
    assert "latitude=62.8" in r.url() and "62.791" not in r.url()
    assert "models=era5_seamless" in r.url() and "shortwave_radiation_sum" in r.url()


def test_parse_response_with_hourly():
    df = parse_response(fake_payload(days=10))
    assert len(df) == 10 and "soil_moisture_0_to_7cm_daily_mean" in df
    with pytest.raises(ValueError):
        parse_response({"error": True, "reason": "bad variable"})


def test_season_summary_stages_and_ptq():
    w = parse_response(fake_payload())
    s = AgroSettings(
        sowing_date=date(2026, 5, 10),
        harvest_date=date(2026, 8, 25),
        stages_gdd={"heading": 600, "yellow_ripe": 1000},
        ptq_window=("heading", "yellow_ripe"),
    )
    out = season_summary(w, 62.8, s)
    assert out["period"]["days"] == 108 and out["period"]["complete"]
    assert 900 < out["gdd_c_days"] < 1600
    assert out["par_mj_m2"] == pytest.approx(0.48 * out["shortwave_mj_m2"])
    assert 0.3 < out["mean_clearness_index"] < 0.6
    assert out["precipitation_mm"] == pytest.approx(216.0)
    assert out["water_balance_mm"] == pytest.approx(216.0 - 270.0)
    assert out["stage_dates"]["heading"] < out["stage_dates"]["yellow_ripe"]
    assert out["ptq_window"]["ptq_mj_m2_per_c_day"] > 0


def test_field_rue_known_answer():
    sow, harvest = date(2026, 5, 1), date(2026, 8, 9)  # 100 days
    days = pd.date_range(sow, harvest)
    par = pd.Series(10.0, index=days.date)
    # NDVI giving fAPAR 0.5 throughout the observed period
    ndvi_val = (0.5 + 0.1426) / 1.1638
    obs = pd.Series(ndvi_val, index=[date(2026, 5, 2), date(2026, 8, 8)])
    res = field_rue(6.0, obs, par, sow, harvest, 14.0, 0.45)
    # biomass = 6 * 0.86 * 100 / 0.45 = 1146.7 g/m2; IPAR ~ 10 * 0.5 * ~99 days
    assert res["biomass_g_m2"] == pytest.approx(1146.67, rel=1e-3)
    assert res["ipar_mj_m2"] == pytest.approx(495, rel=0.02)
    assert res["rue_g_mj"] == pytest.approx(res["biomass_g_m2"] / res["ipar_mj_m2"])


def test_l5_writes_agroclimate_and_rue_map(tmp_path, example_config_path):
    pytest.importorskip("rasterio")
    pytest.importorskip("gstools")
    pytest.importorskip("pyproj")
    from test_l5 import GRID, synthetic_field

    from yieldsuite.config import CropSettings, load_config
    from yieldsuite.l5.run import run_sentinel2_fusion
    from yieldsuite.weather import openmeteo
    from yieldsuite.weather.run import weather_path

    cfg = load_config(example_config_path)
    cfg.output_dir = tmp_path / "output"
    cfg.crop = CropSettings(
        name="spring_wheat",
        sowing_date="2026-05-10",
        harvest_date="2026-08-25",
        stages_gdd={"heading": 600},
    )
    w = parse_response(fake_payload())
    openmeteo.save(
        w, {"source": "test fixture", "request": {"lat": 62.7, "lon": 22.9}}, weather_path(cfg)
    )
    l4, long_df, _, _ = synthetic_field()
    feats = tmp_path / "f.csv"
    long_df.to_csv(feats, index=False)
    out = run_sentinel2_fusion(cfg, l4, GRID, features_path=feats, out_dir=cfg.output_dir)
    card = json.loads(out.model_card.read_text())
    assert "agroclimate_error" not in card, card.get("agroclimate_error")
    agro = card["agroclimate"]
    assert agro["stage_dates"]["heading"] and agro["field_rue"]["rue_g_mj"] > 0
    assert agro["rue_map"]["p10"] < agro["rue_map"]["p90"]
    from yieldsuite.l4.io import read_bands

    bands, grid, _ = read_bands(card["agroclimate_files"]["rue"], require_yield=False)
    assert {"rue_g_mj", "ipar_mj_m2"} <= set(bands) and grid == GRID


def test_mcp_get_weather_uses_fetch(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    import asyncio

    import yaml

    from yieldsuite.mcp_server import build_server
    from yieldsuite.store import FarmStore, init_demo_store
    from yieldsuite.weather import openmeteo

    calls = []

    def fake_fetch(req):
        calls.append(req)
        return parse_response(fake_payload()), {
            "source": "fixture",
            "request": {"lat": req.rounded()[0], "lon": req.rounded()[1]},
        }

    monkeypatch.setattr(openmeteo, "fetch_daily", fake_fetch)
    store = FarmStore(init_demo_store(tmp_path / "s"))
    ydir = store.year_dir("demo-farm", "synthetic-01", 2026)
    cfg = yaml.safe_load((ydir / "field.yaml").read_text())
    cfg["crop"] = {
        "name": "spring_wheat",
        "sowing_date": "2026-05-10",
        "harvest_date": "2026-08-25",
    }
    (ydir / "field.yaml").write_text(yaml.safe_dump(cfg))
    server = build_server(store, actor="t")
    asyncio.run(
        server.call_tool("run_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    )
    res = asyncio.run(
        server.call_tool(
            "get_weather", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026}
        )
    )
    data = json.loads(res.content[0].text)
    assert data["summary"]["gdd_c_days"] > 0 and len(calls) == 1
    lat, lon = calls[0].rounded()  # from the L4 grid centre, rounded to 0.05°
    assert 62.5 < lat < 62.8 and 20 < lon < 32  # within Finland
    assert round(lat / 0.05, 6) == round(lat / 0.05)
    # second call reuses the cache
    asyncio.run(
        server.call_tool(
            "get_weather", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026}
        )
    )
    assert len(calls) == 1
