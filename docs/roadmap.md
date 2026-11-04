# Roadmap

Toward the [vision](vision.md): the Linux of modelling agricultural yield and
its causes.

## Yield causes

| Function | Priority |
|---|---|
| As-applied data (sowing, fertilisation, spraying) as covariates, from ISOXML and the machine-cloud connectors below | High. Management is the cause a farmer can change. |
| Attribution of within-field yield variation to weather, soil, terrain, management and measurement error, each with uncertainty | High. The core of "why", not only "how much". |
| On-farm trial design and analysis (strips, checkerboards) so management effects are estimated, not inferred from correlation | Medium |
| Season-to-season comparison: which zones respond to which weather (dry vs wet years) | Medium |
| GTK soil data and soil samples as covariates | Medium |

## Functions from other software worth adding

From a review of existing yield-data and farm-management software (November 2026):
USDA-ARS Yield Editor, CSIRO PAT, GeoFIS, the Yield Data Cleaner QGIS plugin,
John Deere Operations Center, Climate FieldView, GeoPard, xarvio FIELD MANAGER
and Wisu. Commercial features are taken from vendor and dealer pages and are
unverified.

| Function | Seen in | Priority |
|---|---|---|
| Variable-rate prescription export (ISOXML task data or shapefile, by zone) | xarvio, GeoPard, Deere | High. This is what turns a map into action on the farm. |
| Nitrogen removal and balance per zone (yield × protein) | HarvestLab and Operations Center | High. We already have both yield and GrainSense protein. |
| AgGateway ADAPT import | Yield Data Cleaner | Medium. Covers many proprietary formats at once. |
| Direct sync from machine clouds (Deere, CNH, Claas, FieldView APIs) | Commercial platforms | Medium. Avoids exporting files by hand. See [Data connectors](#data-connectors-to-machine-and-farm-software-platforms). |
| Reassigning data to the right field | SMS | Medium. Our boundary clipping partly covers this. |
| Interactive HTML review report | Yield Data Cleaner | Medium. Makes our QA easier to share without QGIS. |
| Yield-stability classes over several years (stable/unstable × high/low) | Blackmore method, Nebraska framework | Medium. A natural addition next to our zones. |
| Correcting outliers instead of deleting them (Leroux: restored about 15 %) | Research | Low to medium. |

## Data connectors to machine and farm-software platforms

Fetch harvest (yield, moisture, protein), as-applied, field-boundary and
machine data straight from manufacturer and software clouds instead of
exporting files by hand. Access details below come from developer portals,
vendor pages and third-party listings (November 2026); each must be checked
against the provider's current API documentation before building.

| Platform | Brands covered | Access route | Priority |
|---|---|---|---|
| John Deere Operations Center | John Deere (and data synced into it from FieldView and others) | Operations Center API at developer.deere.com; OAuth 2.0 per grower, sandbox and production hosts. Trimble and Harvest Profit already download yield task data through it. | High |
| agrirouter (DKE-Data) | CLAAS, Massey Ferguson, Valtra, Fendt and many implement makers and software vendors | Manufacturer-independent data router; ISOXML task data; REST API "G4" (launched April 2026). It transfers, does not store, so data arrive as messages. | High. Covers CLAAS and AGCO brands common in Finland. |
| CNH FieldOps | Case IH, New Holland, Steyr | FieldOps API at develop.cnh.com (replaces the Ag Data and CONNECT APIs); OAuth 2.0 consent, client ID, secret and subscription key; the developer must be invited into the grower's company account. Yield endpoint still to be confirmed. | High |
| Climate FieldView | Any brand logged with FieldView Drive; CLAAS Telematics and AGCO harvest data synced into it | FieldView Platform API (dev.fieldview.com), free partner account; `/v4/layers/asHarvested` with `occurredAfter` / `updatedAfter` filters for polling; backend only, grower's own data only. | Medium |
| AGCO (Fendt, Massey Ferguson, Valtra, Challenger) | AGCO brands | No confirmed public yield API: telemetry API (AgCommand, per a third-party listing), plus agrirouter and FieldView connections. Use agrirouter first. | Medium. Valtra is common in Finland. |
| CLAAS | CLAAS | CLAAS Telematics / CLAAS connect via agrirouter, the FieldView connection, and DataConnect. | Via agrirouter |
| DataConnect | CLAAS, John Deere, CNH, 365FarmNet | Cloud-to-cloud interface between manufacturer clouds. It carries telemetry (position, speed, machine and work status, fuel), not yield maps, so it suits machine-status checks, not harvest data. | Low |
| Trimble, 365FarmNet, NEXT Farming | Mixed fleets | Partner APIs; verify yield export. | Low |
| Wisu (MTech, Finland) | Finnish crop planning | Field and crop-plan data; API availability to be checked. | Medium. Field register and crop plan for Finnish farms. |
| Aggregators (e.g. Leaf) | Deere, CNH, FieldView, CLAAS and others behind one API | Commercial unified API; one integration instead of many, but paid and a third party in the data path. | Option, not default |

Design rules for every connector:

- Credentials and OAuth refresh tokens come only from environment variables
  or the operating system's keyring, never from files in the farm store or
  the repository, and are masked in errors and logs.
- Downloaded files land unchanged in the immutable L0 store with provenance
  (platform, account, operation ID, download time), then enter the normal
  chain through `yieldsuite inspect` and the column mapping, so cloud data and
  hand-exported files are processed identically.
- Fetches are incremental (by "updated after" timestamps or operation IDs) and
  idempotent.
- The MCP server exposes them as farm-data tools, like the public-data fetches.
- Field matching uses the platform's boundary when present, otherwise our
  parcel boundary from Ruokavirasto.

### Connector sources

- [John Deere opens its data platform to other software suppliers](https://precisionfarmingdealer.com/articles/2360-john-deere-opens-data-platform-to-other-software-suppliers)
- [Trimble integration with John Deere Operations Center](https://www.farms.com/news/farm-equipment/trimble-announces-seamless-farm-data-integration-with-the-john-deere-operations-center-112167.aspx)
- [CNH developer portal](https://develop.cnh.com) and [Leaf's CNH FieldOps connector notes](https://docs.withleaf.io/providers/cnhi-fieldops)
- [agrirouter interface documentation](https://www.github.com/DKE-Data/agrirouter-interface-documentation)
- [Climate FieldView developer FAQ](https://dev.fieldview.com:443/faq) and [FieldView Platform API listing](https://apis.apievangelist.com/store/fieldview-platform-api)
- [CLAAS Telematics and Climate FieldView](https://www.bayer.com/media/en-us/climate-fieldviewtm-claas-telematics-offer-easy-data-management-to-help-simplify-farming-operations/)
- [AGCO API listing (third-party)](https://apis.io/providers/agco/)
- [CLAAS DataConnect](https://www.claas.com/en-eg/digital-solutions/data-management/data-connect)

### Sources

- [USDA-ARS Yield Editor](https://catalog.data.gov/dataset/yield-editor-2-0-7)
- [CSIRO PAT QGIS plugin](https://plugins.qgis.org/plugins/pat)
- [GeoFIS](https://doaj.org/article/f147826071a040209887f306e10e8032)
- [Yield Data Cleaner QGIS plugin](https://plugins.qgis.org/plugins/yield_data_cleaner/)
- [John Deere HarvestLab 3000](https://www.futurefarming.com/crop-solutions/john-deere-harvestlab-3000-available-on-combines-for-analysis-of-grain-and-rapeseed/)
- [GeoPard yield cleaning and calibration](https://docs.geopard.tech/geopard-tutorials/product-tour-web-app/geopard-mcp/clean-calibrate-yield-data)
- [xarvio FIELD MANAGER](https://www.xarvio.com/ca/en/products/field-manager/field-monitor.html)
- [Nebraska multi-year yield map framework](https://digitalcommons.unl.edu/agronomyfacpub/365)
- [Leroux et al. 2018, filtering defective yield observations](https://agris.fao.org/search/fr/records/65df798a0f3e94b9e5d8d0d0)
