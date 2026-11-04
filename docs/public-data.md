# Public data: Sentinel-2 search, field parcels, boundary

Endpoints are configuration, not code. Public services move and rename more
often than this suite is released, so check them when setting up:

```yaml
public_data:
  stac_url: https://stac.dataspace.copernicus.eu/v1   # default; any STAC API works
  s2_collection: sentinel-2-l2a                       # default
  parcels_wfs_url: https://inspire.ruokavirasto-awsa.com/geoserver/wfs   # default
  # parcels_typename: <layer>        # default: discovered by year
  # parcel_id_column: <attribute>    # default: discovered
edge_buffer_m: 6            # FIELD_EDGE buffer; default one header width
```

## Sentinel-2 scenes

```bash
yieldsuite s2-search field.yaml [--l4 L4.tif] [--max-cloud 60]
```

MCP: `search_sentinel2(farm, field, year)`. Lists the scenes over the field
(STAC API `POST /search`, paginated) with their date, tile and scene cloud
cover, keeping the least cloudy scene per day. Verified against Copernicus
Data Space (tile codes such as `MGRS-34VEQ`). Scene cloud cover describes the
whole 100 km tile; the field-level cloud mask is applied in openEO (SCL).
The field is located from its boundary, else the L4 grid, else `location`.

## Boundary by parcel number

The quickest way to a field boundary is its parcel number (Finnish
*peruslohkotunnus*, 10 digits; separators are ignored):

```bash
yieldsuite parcel 123-4567-890 --year 2026 --out boundary.gpkg
```

MCP: `get_boundary_by_parcel_id(farm, field, year, parcel_ids)` (or
`parcel_ids:` in field.yaml) fetches the parcels, stores the boundary in L0,
sets `boundary:` and `parcel_ids:` in field.yaml and enables `FIELD_EDGE`.
QGIS: *Data → Field boundary by parcel number*.

Default service: the Finnish Food Authority's (Ruokavirasto) INSPIRE WFS,
`https://inspire.ruokavirasto-awsa.com/geoserver/wfs`, which publishes the
parcel register as one layer per year. The layer and the id attribute are
**discovered from the service**, not hard-coded:

1. GetCapabilities → parcel layers (names/titles with *peruslohko*, *plohko*,
   *peltolohko*, *parcel*); the layer of the field year, else the latest
   earlier one (reported);
2. DescribeFeatureType → the id attribute (`PERUSLOHKOTUNNUS`,
   `PLOHKOTUNNUS`, `LOHKOTUNNUS`, …);
3. GetFeature with an OGC Filter (`PropertyIsEqualTo`, `Or` for several
   parcels), GeoJSON in EPSG:3067. Missing parcels stop with their numbers.

The result reports the layer, its year and the id attribute. If discovery
picks the wrong one, set `public_data.parcels_typename` /
`parcel_id_column` (CLI: `--layer`, `--id-column`).

**Verified against the live service (November 2026):** layers
`inspire:LandUse.ExistingLandUse.GSAAAgriculturalParcel.<year>` for
2020–2025 are discovered, the id attribute is `PERUSLOHKOTUNNUS`, the filter
query returns the parcel in EPSG:3067, and the area matches the register
(e.g. 7430047224: 2.02 ha). The layer holds one feature per growing block
(`LOHKONUMERO`); a parcel with several blocks is returned as several
features and the boundary is their union. For the current year, before its
layer is published, the latest earlier year is used and reported.

## Field parcels and boundary

1. Get parcel polygons, either
   - from a file you downloaded (e.g. the farm's parcels as GeoPackage or
     Shapefile), or
   - from the parcel register's WFS: MCP `get_parcels(farm, field, year)`
     (needs `public_data.parcels_wfs_url` and `parcels_typename`), which saves
     `public/parcels.geojson` and lists parcel ids and areas.
2. Choose the field: MCP `import_boundary(farm, field, year, source_path,
   parcel_ids?, id_column?)`. Without ids, the parcels overlapping the
   harvested area (from the last L3 run) are taken. The boundary is stored in
   L0 as `raw/boundary/boundary_<n>.gpkg` and the config points to it.
3. The next run flags records whose centre lies within `edge_buffer_m` of the
   boundary (or outside it) as `FIELD_EDGE`; their area shows up as reduced
   coverage instead of edge-biased yield. QA reports the count and the
   boundary area.

Only a bounding box of the field is sent to the WFS and STAC services.
