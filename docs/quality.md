# Grain quality channel (GrainSense Flow / GS Combine)

Protein, moisture and oil measured on the combine, turned into a protein
map, protein yield and nitrogen uptake. Quality is not mass: it is attached
to the harvester records and averaged with mass weights, never spread over
area.

## Getting the data

GrainSense cloud REST API (from the GrainSense public documentation,
16/10/2024): JWT login, combine data as **Flow measurement sessions**, export
as XLSX/CSV.

```bash
export GRAINSENSE_EMAIL=... GRAINSENSE_PASSWORD=...   # or GRAINSENSE_TOKEN
yieldsuite grainsense sessions --start 2026-08-01T00:00:00Z
yieldsuite grainsense export --uuid <session uuid> --format xlsx --out gs_2026.xlsx
```

Credentials stay in the environment, never in the store or the repository.
The live API has not been tested from the development environment.

Measurements in the API carry **milligrams** (protein, carbohydrate, oil,
moisture); percentages depend on each calibration's moisture basis
(`applicationData`): dry = protein + carbohydrate + oil, `dry` basis divides
by dry, `wet` by dry + moisture. `nutrient_percentages()` implements this;
the `fixed` basis is not documented completely and is refused. Exports
already contain percentages, so exports are the recommended input.

## Config

```yaml
quality:
  sources:
    - path: gs_2026.xlsx
      machine_id: combine-a        # the combine carrying the analyzer
      # columns: {time: ..., protein: ..., moisture: ...}   # only if not recognised
      # delay_s: 12                # default: the machine's tau_d
  protein_basis: dry               # basis of the analyzer's protein %: dry | wet
  n_factor: 5.7                    # wheat 5.7, barley/oats 6.25
  lab_samples: lab_protein.csv     # protein_lab_pct + start/end (or time)
```

## Q1–Q4

| Level | What happens |
|---|---|
| Q1 | Export read with column recognition (English/Finnish/German names: protein/proteiini, moisture/kosteus, oil/öljy …), fractions turned into %, time must be absolute |
| Q2 | Grain cut by a record at time t reaches the analyzer at t + delay; the nearest measurement within 0.75 × interval + 1 s is attached to that record (and so gets its position and mass) |
| Q3 | Bias to lab samples (mean lab − analyzer over the sample windows) on the analyzer's basis; then conversion to dry matter (wet: ÷ (1 − moisture)); protein outliers (median ± 3 MAD) flagged |
| Q4 | Per cell: mass-weighted protein (% DM), protein yield = yield × (1 − MC_ref) × protein, N uptake = protein yield / n_factor, share of the cell's mass with a quality value, record count |

Without lab samples the product class is `relative`: patterns are usable,
absolute protein levels are the analyzer's own.

Outputs: `Q4_<field>_<year>.tif` (bands `protein_pct_dm`, `protein_kg_ha`,
`n_uptake_kg_ha`, `quality_mass_share`, `n_records`) and
`Q4_<field>_<year>_qa.json`.

Synthetic check (analyzer bias +0.5 %-points, 5 s interval, 12 s delay): lab
bias estimated −0.51, protein map RMSE 0.06 %-points against the truth.

## Still unknown

The documentation shows no response examples: export column names, the
timestamp format and whether Flow measurements carry their own position are
unverified. Time-matching to the harvester log does not need positions from
the analyzer. Check the first real export with `read_quality()` / the QA
mapping before trusting it.
