"""Many yield/machine data formats, all built from the same synthetic field.

Each must be recognised, converted to SI units and, where run through the chain,
reproduce the known true yield.
"""

import zipfile

import numpy as np
import pandas as pd
import pytest
import yaml

from yieldsuite import synthetic
from yieldsuite.config import load_config
from yieldsuite.ingest.inspect import inspect_file
from yieldsuite.ingest.mapping import match_columns
from yieldsuite.ingest.units import convert, unit_from_header
from yieldsuite.levels import Level
from yieldsuite.pipeline import run
from yieldsuite.timeutil import epoch_seconds

CRS = "EPSG:3067"


def test_unit_and_column_recognition():
    assert unit_from_header("Mass_Flow_kg_s", "mass_flow") == "kg/s"
    assert unit_from_header("Crop Flw(M) (lb/s)", "mass_flow") == "lb/s"
    assert unit_from_header("Yld Vol(Dry)(bu/ac)", "yield_volume") == "bu/ac"
    assert unit_from_header("Swth Wdth(ft)", "swath") == "ft"
    assert unit_from_header("Nopeus (km/h)", "speed") == "km/h"
    assert unit_from_header("Ertrag [dt/ha]", "yield_dry") == "dt/ha"
    assert convert([60.0], "yield_volume", "bu/ac", 60.0)[0] == pytest.approx(4.0353, rel=1e-3)
    m = match_columns(
        [
            "Aika",
            "Itä",
            "Pohjoinen",
            "Massavirta (kg/s)",
            "Kosteus (%)",
            "Työleveys (m)",
            "Nopeus (km/h)",
            "Leikkuupöytä",
            "Kone",
        ]
    )
    assert m["mass_flow"] == "Massavirta (kg/s)" and m["swath"] == "Työleveys (m)"
    assert m["x"] == "Itä" and m["header"] == "Leikkuupöytä" and m["machine"] == "Kone"
    m = match_columns(
        [
            "Zeitstempel",
            "Breitengrad",
            "Laengengrad",
            "Ertrag trocken [t/ha]",
            "Feuchte [%]",
            "Arbeitsbreite [m]",
            "Geschwindigkeit [km/h]",
        ]
    )
    assert m["yield_dry"].startswith("Ertrag") and m["lat"] == "Breitengrad"


def _to_lonlat(x, y):
    from pyproj import Transformer

    return Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(x, y)


def _write_config(tmp_path, log_entry, tickets=True, machine=True):
    f = synthetic.make_field(0)
    cfg = {
        "field_id": "fmt",
        "year": 2026,
        "crs": CRS,
        "reference_moisture_pct": 14,
        "grid": {"resolution_m": 20, "origin": [0, 0]},
        "harvest_logs": [log_entry],
        "output_dir": "out",
    }
    if machine:
        cfg["machines"] = [
            {
                "machine_id": "combine-a",
                "tau_d_s": 12,
                "tau_m_s": 12,
                "antenna_offset_m": [synthetic.ANTENNA_BEHIND_HEADER, 0.0],
            }
        ]
    if tickets:
        f.tickets.to_csv(tmp_path / "tickets.csv", index=False)
        cfg["scale_tickets"] = "tickets.csv"
    path = tmp_path / "field.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return path


def _map_rmse(summary):
    from yieldsuite.l4.io import read_bands
    from yieldsuite.l5.fuse import cell_centres

    bands, grid, _ = read_bands(summary.outputs["L4"])
    _, _, xy = cell_centres(grid)
    truth = synthetic.true_yield(xy[:, 0], xy[:, 1]).reshape(grid.ny, grid.nx)
    good = bands["coverage"] > 0.8
    err = bands["yield"][good] - truth[good]
    return float(np.mean(err)), float(np.sqrt(np.mean(err**2)))


def test_finnish_csv_semicolon_decimal_comma(tmp_path):
    pytest.importorskip("rasterio")
    log = synthetic.make_field(0).log
    t = pd.to_datetime(log["Time"])
    fi = pd.DataFrame(
        {
            "Aika": t.dt.strftime("%d.%m.%Y %H:%M:%S"),
            "Itä": log["Easting"],
            "Pohjoinen": log["Northing"],
            "Massavirta (kg/s)": log["Mass_Flow_kg_s"],
            "Kosteus (%)": log["Moisture_pct"],
            "Työleveys (m)": log["Swath_m"],
            "Nopeus (km/h)": log["Speed_kmh"],
            "Suunta (°)": log["Heading_deg"],
            "Leikkuupöytä": np.where(log["Header"] == 1, "kyllä", "ei"),
            "Kone": "combine-a",
        }
    )
    path = tmp_path / "puinti.csv"
    fi.to_csv(path, sep=";", decimal=",", index=False, encoding="cp1252")

    res = inspect_file(path, CRS)
    r = res["report"]
    assert r["input_family"] == "mass_flow" and r["entry_level"] == "L1B"
    assert r["units"]["speed"] == {"unit": "km/h", "source": "header", "column": "Nopeus (km/h)"}
    assert not [w for w in r["warnings"] if "implausible" in w]

    cfg = load_config(_write_config(tmp_path, {"path": "puinti.csv"}))
    s = run(cfg, Level.L4)
    assert s.ok, s.error
    bias, rmse = _map_rmse(s)
    assert abs(bias) < 0.05 and rmse < 0.1


def _yield_points(units="us"):
    """Vendor-style yield-point export: harvesting points only, delay already applied."""
    log = synthetic.make_field(0).log
    on = log["Header"] == 1
    hx = log["Easting"][on].to_numpy()
    hy = log["Northing"][on].to_numpy()
    north = log["Heading_deg"][on].to_numpy() == 0
    hy = hy + np.where(north, 1, -1) * synthetic.ANTENNA_BEHIND_HEADER  # header position
    y_mid = hy - np.where(north, 1, -1) * synthetic.SPEED / 2
    rng = np.random.default_rng(1)
    yt = synthetic.true_yield(hx, y_mid) * (1 + rng.normal(0, 0.03, hx.size))
    return log[on], hx, hy, yt


def test_us_yield_points_bu_ac_lonlat(tmp_path):
    pytest.importorskip("pyproj")
    pytest.importorskip("rasterio")
    log, hx, hy, yt = _yield_points()
    lon, lat = _to_lonlat(hx, hy)
    bw = 60.0  # wheat
    from yieldsuite.ingest.units import LB_PER_AC_TO_T_PER_HA

    us = pd.DataFrame(
        {
            "Longitude": lon,
            "Latitude": lat,
            "Yld Vol(Dry)(bu/ac)": yt / (bw * LB_PER_AC_TO_T_PER_HA),
            "Moisture(%)": 18.0,
            "Swth Wdth(ft)": synthetic.HEADER_W / 0.3048,
            "Distance(ft)": synthetic.SPEED / 0.3048,
            "Duration(s)": 1.0,
            "Speed(mph)": synthetic.SPEED / 0.44704,
            "Track(deg)": log["Heading_deg"].to_numpy(),
            "Time": log["Time"].to_numpy(),
        }
    )
    path = tmp_path / "yield_export.csv"
    us.to_csv(path, index=False)

    with pytest.raises(ValueError, match="crop"):
        inspect_file(path, CRS)  # bu/ac without crop must not be guessed
    res = inspect_file(path, CRS, crop="wheat", vendor_reference_moisture_pct=14)
    r = res["report"]
    assert r["input_family"].startswith("yield_point") and r["entry_level"] == "L2"
    assert r["units"]["swath"]["unit"] == "ft"
    assert r["summary"]["median_swath_m"] == pytest.approx(6.0, rel=1e-3)

    entry = {
        "path": "yield_export.csv",
        "machine_id": "combine-a",
        "crop": "wheat",
        "vendor_reference_moisture_pct": 14,
    }
    # Vendor points are already at the header: no antenna offset, no delay.
    cfg = load_config(_write_config(tmp_path, entry, machine=False))
    s = run(cfg, Level.L4)
    assert s.ok, s.error
    assert s.k_factors["combine-a"] == pytest.approx(1.0, abs=0.02)
    bias, rmse = _map_rmse(s)
    assert abs(bias) < 0.1 and rmse < 0.15


def test_geopackage_deere_style_names_warn_on_guessed_units(tmp_path):
    gpd = pytest.importorskip("geopandas")
    log, hx, hy, yt = _yield_points()
    gdf = gpd.GeoDataFrame(
        {
            "VRYIELDMAS": yt,
            "WetMass": yt * 86 / 82,
            "Moisture": 18.0,
            "SWATHWIDTH": 6.0,
            "VEHICLSPEED": synthetic.SPEED * 3.6,
            "Distance": synthetic.SPEED,
            "IsoTime": log["Time"].to_numpy(),
        },
        geometry=gpd.points_from_xy(hx, hy),
        crs=CRS,
    )
    path = tmp_path / "deere.gpkg"
    gdf.to_file(path, driver="GPKG")
    r = inspect_file(path, CRS)["report"]
    assert r["format"] == "vector"
    assert r["mapping"]["yield_wet"] == "WetMass"  # wet yield preferred with its moisture
    assert r["units"]["yield_wet"]["source"].startswith("value range")
    # No unit in the names: speed and distance are resolved against GNSS positions.
    assert r["units"]["speed"]["unit"] == "km/h"
    assert r["units"]["speed"]["source"] == "consistency with GNSS positions"
    assert r["units"]["distance"]["unit"] == "m"
    assert r["summary"]["median_yield_t_ha_wet"] is not None


def test_excel(tmp_path):
    pytest.importorskip("openpyxl")
    log = synthetic.make_field(0).log.head(200)
    path = tmp_path / "loki.xlsx"
    log.to_excel(path, index=False)
    r = inspect_file(path, CRS, units={"speed": "km/h"})["report"]
    assert r["format"] == "excel" and r["rows_out"] == 200


def test_isoxml_taskdata_zip_through_chain(tmp_path):
    pytest.importorskip("pyproj")
    pytest.importorskip("rasterio")
    from yieldsuite.ingest.isoxml import write_timelog

    log = synthetic.make_field(0).log
    lon, lat = _to_lonlat(log["Easting"].to_numpy(), log["Northing"].to_numpy())
    t = epoch_seconds(log["Time"])
    df = pd.DataFrame(
        {
            "t": t,
            "lat": lat,
            "lon": lon,
            "status": 4,
            "DDI_87": log["Mass_Flow_kg_s"] * 1e6,  # mg/s
            "DDI_99": log["Moisture_pct"] * 1e4,  # ppm
            "DDI_67": log["Swath_m"] * 1000,  # mm
            "DDI_141": log["Header"],
        }
    )
    header, binary = write_timelog(df, [87, 99, 67, 141])
    zpath = tmp_path / "taskdata.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr(
            "TASKDATA/TASKDATA.XML",
            '<ISO11783_TaskData VersionMajor="4"><TSK A="TSK1"><TLG A="TLG00001"/></TSK>'
            "</ISO11783_TaskData>",
        )
        z.writestr("TASKDATA/TLG00001.XML", header)
        z.writestr("TASKDATA/TLG00001.BIN", binary)

    r = inspect_file(zpath, CRS, machine_id="combine-a")["report"]
    assert r["format"] == "isoxml" and r["entry_level"] == "L1B"
    assert r["summary"]["median_swath_m"] == pytest.approx(6.0)
    assert r["summary"]["median_moisture_pct"] == pytest.approx(18.0, abs=0.3)

    cfg = load_config(_write_config(tmp_path, {"path": "taskdata.zip", "machine_id": "combine-a"}))
    s = run(cfg, Level.L4)
    assert s.ok, s.error
    bias, rmse = _map_rmse(s)
    assert abs(bias) < 0.05 and rmse < 0.1


def test_agleader_advanced_preset(tmp_path):
    pytest.importorskip("pyproj")
    log = synthetic.make_field(0).log.head(300)
    lon, lat = _to_lonlat(log["Easting"].to_numpy(), log["Northing"].to_numpy())
    t = epoch_seconds(log["Time"])
    rows = pd.DataFrame(
        {
            "lon": lon,
            "lat": lat,
            "flow": log["Mass_Flow_kg_s"] / 0.45359237,
            "time": t,
            "dur": 1.0,
            "dist": synthetic.SPEED / 0.0254,
            "swath": log["Swath_m"] / 0.0254,
            "moist": log["Moisture_pct"],
            "hdr": log["Header"],
            "pass": 1,
            "serial": "AL123",
        }
    )
    path = tmp_path / "advanced.txt"
    rows.to_csv(path, index=False, header=False)
    r = inspect_file(path, CRS, format="agleader_advanced")["report"]
    assert r["format"] == "agleader_advanced"
    assert r["summary"]["machines"] == ["AL123"]
    assert r["summary"]["median_swath_m"] == pytest.approx(6.0, rel=1e-3)
    assert r["summary"]["median_mass_flow_kg_s"] == pytest.approx(
        log["Mass_Flow_kg_s"][log["Header"] == 1].median(), rel=1e-3
    )


def test_cli_inspect(tmp_path, capsys):
    from yieldsuite.cli import main

    synthetic.make_field(0).log.to_csv(tmp_path / "log.csv", index=False)
    assert main(["inspect", str(tmp_path / "log.csv"), "--unit", "speed=km/h"]) == 0
    out = capsys.readouterr().out
    assert "mass_flow" in out and "harvest_logs:" in out


def test_epoch_seconds_independent_of_resolution():
    s = pd.Series(["2026-08-10T10:00:00+00:00", "2026-08-10T10:00:05+00:00"])
    expected = [1786356000.0, 1786356005.0]
    assert epoch_seconds(s).tolist() == expected
    for unit in ("s", "ms", "us", "ns"):
        dt = pd.to_datetime(s, utc=True).dt.as_unit(unit)
        assert epoch_seconds(dt).tolist() == expected
