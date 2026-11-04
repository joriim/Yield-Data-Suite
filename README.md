# Miiro

**Vision: the Linux of modelling agricultural yield and its causes.** An
open, shared core that anyone can run, inspect and improve, with connectors
for every data source and many front ends on top. See
[docs/vision.md](docs/vision.md).

Miiro is an open-source processing chain for **as-good-as-achievable yield maps** from
combine harvester logs, weighed loads, drone and public satellite data
(Sentinel-2), together with the field weather, soil, terrain and grain-quality
data that explain them.

The Python package and command are `yieldsuite` (the command is also
available as `miiro`).

The chain follows six processing levels, modelled on NASA EOSDIS data levels.
Each level has a defined input, model, output and uncertainty, so any
field-year can be reprocessed from raw data when a model improves.

| Level | Content |
|---|---|
| L0 | Raw harvester log, archived as received |
| L1A / L1B | Decoded samples in SI units → mass flow at reference moisture |
| L2 | Georeferenced record polygons: delay, antenna offset, effective area, yield |
| L3 | Reconciled to weighed loads, quality-flagged (records are never deleted) |
| L4 | Mass-conserving grid (10/20 m Cloud-Optimised GeoTIFF) with quality bands |
| L5 | Fused with imagery: residual shift, pass stripes, gap fill, zones |

Full design: [docs/design/processing-levels.md](docs/design/processing-levels.md).

## What each data stack can produce

| Inputs | Product class |
|---|---|
| Harvester + scale tickets (+ imagery) | `absolute` (t/ha, reconciled) |
| Harvester only | `absolute_unreconciled` |
| Imagery only (harvester from other years optional) | `relative` |

Every product carries its class, so a relative map is never mistaken for an absolute one.

## Layout

```
src/yieldsuite/   core library (headless, pip-installable)
  ingest/         format/column/unit detection, readers incl. ISOXML
  weather/        ERA5 via Open-Meteo, PAR, GDD, RUE
  l1/ … l5/       one package per processing level
  registry.py     versioned machine profiles (delays, offsets, curves, k factors)
  catalog.py      STAC product metadata
  pipeline.py     runs a field-year; becomes Dagster assets on the server
  notify/         optional Slack notifications
  store.py        farm data store (folder mode), versioned, audited
  mcp_server.py   farm-data MCP server
  cli.py          `yieldsuite` command
qgis_plugin/      QGIS Processing provider (GPL-2.0-or-later)
server/           phase 2: PostGIS, pgSTAC, Dagster, FastAPI, web viewer
docs/             MkDocs site
examples/         field-year config
tests/            pytest suite
```

## Quick start

```bash
pip install -e ".[dev,geo]"
yieldsuite demo demo/                     # synthetic field with a known true yield
yieldsuite run demo/field.yaml --to L4    # harvester log -> calibrated yield grid
pytest
```

## Status

Pre-alpha. Working end to end:

- **Ingest** of almost any yield/machine file: CSV/TXT (incl. Nordic `;` and
  decimal comma), Excel, Shapefile/GeoPackage/GeoJSON, ISOXML TaskData
  (experimental), Ag Leader advanced; columns and units recognised and
  reported (`yieldsuite inspect FILE`), mass-flow logs and yield-point
  exports both supported ([docs/harvester-chain.md](docs/harvester-chain.md)).
- **Multi-source imagery** ([docs/multisource.md](docs/multisource.md)): drone
  orthophotos (Mavic 3M, processed via WebODM) and PlanetScope harmonised to
  Sentinel-2, with per-source fit and ablation in the model card.
- **Field weather and agroclimate** ([docs/weather-agroclimate.md](docs/weather-agroclimate.md)):
  ERA5 via Open-Meteo, cloud-aware PAR, clearness index, growing degree days,
  stage dates, water balance, and a per-cell radiation-use-efficiency map.

- **L1A–L4** ([docs/harvester-chain.md](docs/harvester-chain.md)): flow-delay
  shift, antenna offset, moisture standardisation, effective area, quality
  flags, load reconciliation per machine, mass-conserving grid, flow-delay
  estimation from opposite passes (`yieldsuite delay`). On the
  synthetic field the map is within about 0.04 t/ha RMSE of the truth.
- **Public data** ([docs/public-data.md](docs/public-data.md)): Sentinel-2
  scene search over the field (STAC API), **field boundary by parcel number**
  from the Ruokavirasto parcel register (`yieldsuite parcel <id>`; layer and id
  attribute discovered from the service), boundary in L0 and the `FIELD_EDGE` flag.
- **QGIS plugin** ([qgis_plugin/README.md](qgis_plugin/README.md)): seven
  Processing algorithms (inspect, parcel boundary, run chain, delay, fusion,
  zones, weather)
  on the same core; tested in a headless QGIS 3.34.
- **Grain quality** ([docs/quality.md](docs/quality.md)): GrainSense Flow /
  combine analyzer exports or cloud API, aligned to the harvester records,
  lab-calibrated, mass-weighted protein map, protein yield and N uptake.
- **Management zones** ([docs/zones.md](docs/zones.md)): multi-year relative
  yield + covariates, MULTISPATI-PCA, fuzzy k-means with k chosen by FPI/NCE,
  stability classes (`yieldsuite zones`, MCP `make_zones`); terrain covariates
  (elevation, slope, TPI, wetness index) from a DEM file or the NLS 2 m DEM
  fetched automatically (`yieldsuite terrain`, needs a free NLS API key).
- **Farm-data MCP server** (`yieldsuite mcp --store <root>`): list fields and
  products, QA, versioned machine profiles, immutable L0 upload, scale
  tickets, QA approvals, `run_chain` with optional Slack summary; every write
  goes to an audit log. Setup in the Finnish guide below.
- **L5** ([docs/l5-sentinel2.md](docs/l5-sentinel2.md)): Sentinel-2 via openEO,
  yield-image model, regression-kriging gap fill, pass-stripe levelling and a
  residual-shift check of the delay. Not yet run against the live backend.

Not yet: raw-force (L0) input, validation of ISOXML on real terminal files,
elevation/soil/station-weather tools in the MCP server, PlanetScope download,
the PostGIS/pgSTAC server.

## Työnkulkuohje (suomeksi)

QGIS, WebODM, openEO ja tiladata-MCP vaiheittain:
[docs/ohje/tyonkulku-qgis-webodm-openeo-mcp.md](docs/ohje/tyonkulku-qgis-webodm-openeo-mcp.md).

Suunnitelma Datakasvukunnon satokartoitusdatalle, GrainSense Combinelle,
Mavic 3M:lle, Sentinel-2:lle ja PlanetScopelle:
[docs/ohje/datalahteet-suunnitelma.md](docs/ohje/datalahteet-suunnitelma.md).

## Slack

Run summaries can be posted to `#yield-data-suite`; see [docs/slack.md](docs/slack.md).

## Licence

Core: Apache-2.0 ([LICENSE](LICENSE)). QGIS plugin: GPL-2.0-or-later
([qgis_plugin/LICENSE](qgis_plugin/LICENSE)), as QGIS requires.
