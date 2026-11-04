# QGIS plugin

Processing provider *Miiro* with seven algorithms. Each is a thin
wrapper around `yieldsuite.tasks`, so results are identical to the command
line. Licensed GPL-2.0-or-later (see `LICENSE`), as required for QGIS
plugins; the core library stays Apache-2.0.

| Algorithm | What it does | Loads |
|---|---|---|
| Data → Inspect yield/machine data file | format, columns, units and their source; writes the `harvest_logs` entry | – |
| Data → Field boundary by parcel number | parcels by peruslohkotunnus from the parcel-register WFS (Ruokavirasto by default) | boundary |
| Run yield chain (L1-L4 / L5) | the whole chain for a `field.yaml` | L3 records, L4, L5, Q4 |
| Estimate grain-flow delay | delay from opposite passes vs. the machine profile | – |
| Fuse L4 with Sentinel-2 (L5) | image model and gap fill on an L4 raster | L5 |
| Management zones (KM-sPC) | zones from multi-year maps (+ covariates) | zones raster and polygons |
| Field weather and agroclimate (ERA5) | PAR, GDD, stages, water balance | – |

## Install

1. Install the library into the Python that QGIS uses:
   - Windows: *OSGeo4W Shell* → `python -m pip install "yieldsuite[geo,fusion]"`
   - Linux: `python3 -m pip install --user "yieldsuite[geo,fusion]"`
     (until the package is on PyPI: `pip install "git+https://github.com/joriim/Yield-Data-Suite@<branch>#egg=yieldsuite[geo,fusion]"`)
2. Install the plugin: *Plugins → Manage and Install Plugins → Install from ZIP*
   with `yieldsuite_qgis.zip` (built by CI, or
   `cd qgis_plugin && zip -r yieldsuite_qgis.zip yieldsuite_qgis`).
3. The algorithms appear in the *Processing Toolbox* under *Miiro*.

If the library is missing, every algorithm stops with the install hint.

## Development

Symlink `yieldsuite_qgis/` into the profile's `python/plugins/` folder and
`pip install -e .` the core into QGIS's Python. Tests run in a headless QGIS:

```bash
QT_QPA_PLATFORM=offscreen python3 -m pytest tests/qgis
```

Tested with QGIS 3.34 (Ubuntu 24.04), Python 3.12, pandas 3 and numpy 2.
