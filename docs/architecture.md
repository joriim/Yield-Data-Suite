# Architecture

One headless Python core, three front ends.

| Layer | What | Who uses it |
|---|---|---|
| Core library `yieldsuite` | L0–L5 as functions on GeoParquet and COG; parameters from versioned machine profiles | everything below |
| Command-line tool | `yieldsuite run field.yaml --to L4` | researchers, advisors, CI, batch jobs |
| QGIS plugin | Processing algorithms + flag/parameter review | agronomists on the desktop |
| Server (phase 2) | PostGIS, pgSTAC, Dagster, FastAPI/OGC API, MapLibre viewer | many farms and years |

## Why not SharePoint, plain HTML or a QGIS-only tool

- **SharePoint** is not open source, cannot run processing, and is not a geodata store.
- **HTML** is used for the docs site and the web viewer, not for processing.
- **QGIS-only** logic cannot run in batch, on a server, or in tests; the plugin
  therefore wraps the core instead of containing it.
- **Google Earth Engine** is avoided as a dependency; Sentinel-2 comes from
  openEO/STAC on Copernicus Data Space.

## Product classes

| Inputs | Class |
|---|---|
| Harvester + scale tickets (+ imagery) | `absolute` |
| Harvester only | `absolute_unreconciled` |
| Imagery only (harvester data from other years optional) | `relative` |

## Data sources entering the chain

| Source | Enters at | Module |
|---|---|---|
| Yield/machine files (CSV, Excel, Shapefile, GeoPackage, GeoJSON, ISOXML, Ag Leader) | L1B or L2 | `yieldsuite.ingest` (`yieldsuite inspect`) |
| Scale tickets | L3 | `yieldsuite.ingest.scale` |
| Sentinel-2 L2A | L5 | `yieldsuite.l5.sentinel2` (openEO) |
| ERA5 / ERA5-Land weather (Open-Meteo) | L5 agroclimate | `yieldsuite.weather` |
| Drone orthomosaics (WebODM) | L5 | planned |
| Field parcels, elevation, soil (Ruokavirasto, MML, GTK) | L0 / zones | planned |

Weather is per field, not per cell: it explains the season (PAR, GDD, stages,
water balance) and, with Sentinel-2 fAPAR, gives a per-cell RUE map.
