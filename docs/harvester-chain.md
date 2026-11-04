# Harvester chain L1A → L4

`yieldsuite run field.yaml --to L4` turns harvester logs and scale tickets into
a mass-conserving yield grid.

## Try it

```bash
yieldsuite demo demo/                    # synthetic field with a known true yield
yieldsuite run demo/field.yaml --to L4
```

The synthetic combine logs mass flow 12 s late, reads 7 % high, records grain
at 18 % moisture and has 15 sensor spikes. The chain recovers the true map to
about 0.04 t/ha RMSE on well-covered cells.

## Input: almost any yield or machine data file

```bash
yieldsuite inspect my_export.csv            # or .txt .xlsx .shp .gpkg .geojson .zip TASKDATA
yieldsuite inspect export.csv --crop wheat --vendor-moisture 15.5
```

`inspect` reports what it recognised and prints a `harvest_logs` entry with
every assumption written out. Paste it into `field.yaml` (or use the MCP tool
`register_harvest_log`, which also stores the file in L0).

| Source | Detection | Entry level |
|---|---|---|
| CSV / TXT / TSV | delimiter, decimal comma and encoding sniffed (Nordic exports with `;` and `,` work) | L1B (mass flow) or L2 (yield points) |
| Excel | first sheet or `sheet:` | as above |
| Shapefile, GeoPackage, GeoJSON, zipped shapefile | geometry and CRS from the file | as above |
| ISOXML TaskData (folder, TASKDATA.XML or zip) | TimeLog binaries decoded; DDI 87 wet mass flow, 182 dry mass flow, 99 moisture, 67 width, 397 speed, 141 work state; values forward-filled | L1B |
| Ag Leader advanced text | `format: agleader_advanced` (no header, fixed columns) | L1B |

**Columns** are recognised from English, Finnish and German names and from
common vendor field names (e.g. `Yld_Vol_Dr`, `VRYIELDMAS`, `Swth_Wdth_`,
`Massavirta`, `Työleveys`, `Ertrag`, `Arbeitsbreite`). An explicit
`columns:` mapping always wins.

**Units** come, in this order, from the config, the format itself, the
column header (`kg/s`, `lb/s`, `t/h`, `bu/ac`, `kg/ha`, `dt/ha`, `ft`, `in`,
`mph`, `km/h`, `ppm` …), consistency with the GNSS positions (distance and
speed), or the value range. The report says which; guessed units come with a
warning. Ambiguous cases (a bu/ac yield without a crop, a mass flow with no
unit) stop with a message instead of guessing.

**Yield-point exports** (yield per area at each point, typical of
shapefile exports from farm-management software) are turned back into mass
as yield × distance × swath, so they still reconcile to scale tickets. They
enter at L2 because the vendor has already applied the flow delay; set the
machine's antenna offset to 0 if the vendor positions are already at the
header. Dry/standard yields are re-standardised from
`vendor_reference_moisture_pct` to the suite's reference moisture.

ISOXML decoding is experimental: it is tested on files written to the
specification's layout, not yet on real terminal exports. Check the first real
TaskData against the vendor's totals.

Example entry for the synthetic demo:

```yaml
harvest_logs:
  - path: combine-a_2026.csv
    machine_id: combine-a
    format: table
    columns: {time: Time, x: Easting, y: Northing, mass_flow: Mass_Flow_kg_s}
    units: {speed: km/h}
```

Scale tickets (`scale_tickets:`) are a CSV with `mass_kg`, and optionally
`machine_id` and `moisture_pct`.

## What each stage does

| Stage | Operations |
|---|---|
| L1A | Read logs, unit conversion, project positions to the field CRS |
| L1B | Nothing for vendor exports (mass flow already computed) |
| L2 | Shift mass flow by τ_d and moisture by τ_m so each record gets the grain it cut; standardise to reference moisture; antenna → header offset; pass segmentation; record polygons; effective (non-overlapping) area; yield |
| L3 | Flags: header up, fill/finish, speed, moisture range, effective swath, GNSS, field edge, global outliers (median ± 3·MAD), local outliers (negative local Moran's I with a permutation test, or > 4 robust SD from the neighbours' median). Reconciliation factor k per machine from scale tickets |
| L4 | Mass apportioning of clean records to the fixed grid; quality bands; mass-conservation check |
| L5 | Sentinel-2 fusion, if `--to L5` and a `sentinel2` imagery entry exist |

Moisture standardisation happens in L2 rather than L1B, because it needs
moisture aligned to the same grain as the mass flow.

## Flow delay

The delay τ_d (header → mass-flow sensor) is the single parameter that
damages a map most when wrong. It comes from the machine profile; the chain
also estimates it from the data every run:

```bash
yieldsuite delay field.yaml        # per machine: profile value, estimate, curve
```

The estimate is the delay that makes adjacent passes driven in opposite
directions agree best (mean squared difference of log yields, records
compared at their centres, yields smoothed with a 5-record running median so
sensor spikes do not decide). It is reported as *identifiable* only when the
discrepancy at a typical wrong delay is at least twice the minimum and the
minimum is not at the edge of the 0–30 s search range.

| Profile has τ_d | Estimate | Used |
|---|---|---|
| yes | agrees within 3 s | profile |
| yes | differs by > 3 s | profile, with a QA warning to check |
| no | identifiable | estimate, with a QA warning to store it |
| no | not identifiable | 12 s default, with a QA warning |

On the synthetic field (true 12 s) estimates over six random seeds were
11.6–12.4 s. A field without along-track yield pattern gives a flat curve and
is correctly reported as not identifiable. Vendor exports that already
applied the delay (entry level L2) are not shifted again.

## Reconciliation and outliers

Reconciliation uses all harvested mass, including flagged records, because
they still carried grain to the tank. The exception is outlier records
(global or local): their logged value is a sensor error, so they enter the
reconciliation with the field median yield over their effective area. Without
this, 15 spikes in the synthetic field biased the whole map by −1 %.

## Outputs

In `output_dir`:

- `L3_<field>_<year>_records.parquet`: GeoParquet of record polygons with all
  columns and the flag bitmask (CSV without geometry if geopandas is missing)
- `L4_<field>_<year>.tif`: bands yield, coverage, n_records, spread,
  flagged_share, passes
- `L4_<field>_<year>_qa.json`: delays used, flag counts, k factors,
  mass-conservation check, warnings

## Not yet

Moisture-delay (τ_m) estimation, field-boundary edge flag,
raw force (L0) signals.
