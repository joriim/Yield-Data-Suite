# Field weather and agroclimate (ERA5 via Open-Meteo)

Per field and season: cloud-aware PAR, radiation use efficiency (RUE),
growing degree days, stage dates, water balance and stress days.

## Why

Weather is one value per field and day, so it does not explain variation
*within* a field. It explains the *year*: how much light and warmth the crop
had, when it reached its stages, and how efficiently it turned intercepted
light into grain. Combined with Sentinel-2 fAPAR it gives a per-cell RUE map:
low RUE where the canopy intercepted light but did not produce yield (e.g.
drought, disease, lodging, late nitrogen).

## Data

| Variable | Source | Note |
|---|---|---|
| Daily Tmax, Tmin, Tmean | ERA5 / ERA5-Land via Open-Meteo archive API | `models: era5_seamless` (default), `era5`, `era5_land` |
| All-sky shortwave radiation | same | includes clouds, MJ/m²/day |
| Precipitation, FAO-56 ET0, sunshine, daylight | same | |
| Hourly extras (optional) | same, averaged per day | e.g. `soil_moisture_0_to_7cm`, `vapour_pressure_deficit` |

No account is needed. Only a rounded location (default 0.05°, about 5 km) is
sent, which is finer than the ERA5 grid, so the field's exact position never
leaves the farm. Responses are cached in `public/era5_daily.csv` with a
`.provenance.json` file (URL, time, grid point, licence: Copernicus C3S /
Open-Meteo CC BY 4.0).

## Indices

| Index | Definition |
|---|---|
| PAR | 0.48 × all-sky shortwave (configurable `par_fraction`) |
| Clearness index kt | shortwave / top-of-atmosphere radiation (FAO-56 eq. 21); kt < 0.3 counted as overcast, > 0.6 as clear |
| GDD | Σ max(0, min(Tmean, Tcap) − Tbase), Tbase 5 °C by default (Finnish *tehoisa lämpösumma*) |
| Stage dates | first day the GDD sum from sowing reaches each `stages_gdd` threshold |
| PTQ | PAR / (Tmean − Tbase) averaged over a window between two stages |
| Water balance | precipitation − ET0 |
| Heat / frost days | Tmax > 30 °C, Tmin < 0 °C |
| fAPAR | 1.1638 NDVI − 0.1426 (Myneni & Williams 1994), clipped to 0–0.95, interpolated daily between Sentinel-2 dates; 0 at sowing and harvest |
| IPAR | Σ PAR × fAPAR, per cell |
| RUE | dry above-ground biomass / IPAR [g/MJ]; biomass = yield × (1 − MC_ref) / harvest index |

The harvest index is an assumption (default 0.45) and scales RUE directly:
compare RUE within a crop and year, not across crops.

## Config

```yaml
location: {lat: 62.79, lon: 22.84}   # optional; otherwise the centre of the L4 grid
crop:
  name: spring_wheat
  sowing_date: 2026-05-12
  harvest_date: 2026-08-28           # optional; default: from the data
  t_base_c: 5
  harvest_index: 0.45
  stages_gdd: {heading: 700, yellow_ripe: 1100}   # set for your crop and variety
  ptq_window: [heading, yellow_ripe]
weather:
  model: era5_seamless
  hourly: [soil_moisture_0_to_7cm]
```

## Run

```bash
yieldsuite weather field.yaml                 # summary -> L5_<field>_<year>_agroclimate.json
yieldsuite fuse field.yaml --l4 L4.tif        # with crop.sowing_date: also RUE map
```

MCP: `get_weather(farm, field, year)`.

Outputs: `L5_<field>_<year>_agroclimate.json` (season totals, stage dates,
field RUE, RUE percentiles) and `L5_<field>_<year>_rue.tif` (bands
`rue_g_mj`, `ipar_mj_m2`, `biomass_g_m2`). A weather failure (e.g. no
network) is written to the model card and does not stop L5.

## Limits and next steps

- ERA5 radiation under broken cloud is coarse. Satellite-based surface
  radiation (CM SAF SARAH-3, roughly 0.05°) resolves clouds better and is
  the planned second source for PAR.
- FMI open-data station observations for temperature and precipitation
  where a station is close.
- Stage thresholds are crop- and variety-specific; the defaults are empty on
  purpose.
