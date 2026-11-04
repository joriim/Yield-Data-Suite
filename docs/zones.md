# Management zones (KM-sPC)

Zones from several years of yield maps, following Córdoba et al. (2013,
2016) as in the design document (correction 4).

```bash
yieldsuite zones L5_field_2023.tif L5_field_2024.tif L5_field_2025.tif \
    --covariate elevation.tif --out zones/ [--k-max 6]
```

MCP: `make_zones(farm, field, years?, covariates?, k_max?)` uses each year's
L5 map where it exists, else L4, and writes to `<farm>/<field>/zones/`.

## Terrain covariates

```bash
yieldsuite terrain dem.tif --like L5_field_2025.tif --out terrain.tif
yieldsuite zones L5_*.tif --covariate terrain.tif --out zones/
```

MCP: `make_terrain(farm, field, dem_path)` writes
`<field>/covariates/terrain.tif`; pass `covariates/terrain.tif` to
`make_zones`. Every band of a covariate raster becomes its own layer.

| Band | Definition |
|---|---|
| `elevation_m` | area-weighted mean elevation |
| `slope_deg` | slope at the DEM's resolution, averaged to the grid |
| `tpi_m` | elevation minus the mean within 100 m (negative = hollow) |
| `twi` | ln(a / tan β), D8 flow accumulation on a grid buffered by 200 m |

Use a DEM in a projected CRS that extends ~200 m beyond the field; otherwise
TWI misses water arriving from outside and the report warns.

**In Finland the DEM can be fetched automatically**: leave the DEM out
(`yieldsuite terrain --like L5.tif`, MCP `make_terrain` without `dem_path`)
and the National Land Survey's 2 m elevation model (`korkeusmalli_2m`, WCS
2.0, CC BY 4.0) is downloaded for the field plus the buffer. It needs a free
personal API key from the NLS *My account* service in `NLS_API_KEY`; the key
is never written to logs, errors or provenance. The coverage's axis labels
are read from the service (DescribeCoverage). Attribution: "Contains data
from the National Land Survey of Finland".

Verified against the live service (November 2026): axis labels `E`/`N` are
discovered, a 2 m GeoTIFF in EPSG:3067 is returned, and terrain covariates
on a real 2 ha parcel near Seinäjoki (boundary fetched by parcel number)
came out plausible (elevation 50.0–51.8 m, slope ≤ 5.6°, TWI 5.2–12.1) with
the DEM covering the whole buffer.

## Method

1. **Relative yield** per year: % of that year's field mean, so years and
   crops with different levels weigh equally. A cell needs values in at
   least 70 % of the years (minimum two); missing years count as the mean.
2. **Covariates** (optional): any raster on the same grid origin, e.g.
   elevation, ECa, soil, image history.
3. **MULTISPATI-PCA** of the standardised layers with 8-neighbour,
   row-standardised weights: components that maximise variance ×
   spatial autocorrelation; the components covering 80 % of the positive
   eigenvalues are kept.
4. **Fuzzy k-means** (m = 1.3) for k = 2…k_max. k is chosen where both the
   fuzziness performance index (FPI) and the normalised classification
   entropy (NCE) are low (smallest sum of ranks; ties → fewer zones).
5. Zones are numbered by mean relative yield (zone 1 = lowest).

**Stability classes** (per cell, across years): high and stable (mean
≥ 100 %, CV < 15 %), low and stable (mean < 100 %, CV < 15 %), unstable
(CV ≥ 15 %).

All layers must share the grid origin and cell size (design rule 5); maps of
different extents are placed on their union.

## Outputs

- `<stem>.tif`: bands `zone`, `membership` (highest fuzzy membership),
  `stability`, `mean_relative_yield`, `cv_relative_yield`
- `<stem>.gpkg`: zone polygons with area, for QGIS and prescription maps
- `<stem>.json`: k scan (FPI, NCE), spatial components, per-zone area and
  relative yield per year, variance explained, stability counts

## Synthetic check

Three true zones (80 / 100 / 120 %) and an unstable patch (±25 % between
years), 5 % cell noise and a year-specific pattern:

| Years | k found | Adjusted Rand index vs truth |
|---|---|---|
| 5 | 3 | > 0.95 |
| 3 | 3 | > 0.8 |
| 2 (no unstable patch) | 3 | > 0.7 |

With two years an unstable patch legitimately becomes its own zone. The
unstable patch is classed unstable (> 90 % of its cells) with < 5 % false
alarms elsewhere.

Zones describe where yield has been consistently high or low; whether to
manage them differently (seed, nitrogen, drainage) is an agronomic decision.
