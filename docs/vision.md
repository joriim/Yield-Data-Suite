# Vision

**Miiro aims to be the Linux of modelling agricultural yield and its causes:**
an open, shared core that anyone can run, inspect and improve, and that
farmers, advisers, researchers and companies build on instead of each
rebuilding it behind closed doors.

## What the comparison means

| Linux | Miiro |
|---|---|
| An open kernel that every distribution shares | An open core: processing levels L0–L5, the data model, quality flags, uncertainty and provenance. Every yield map can be traced back to raw data and reprocessed when a model improves. |
| Drivers for every kind of hardware | Readers and connectors for every data source: combine logs from any brand, scale tickets, grain-quality analysers, drones, Sentinel-2 and PlanetScope, weather, soil, terrain, machine clouds and farm software. |
| Distributions and applications on top | Front ends on the same core: command line, QGIS plugin, MCP server for AI assistants, a server for farms and advisory services, and products that companies build. |
| Runs on any hardware, no vendor lock-in | Runs on any operating system, on one laptop or a server, and works with data from any machine brand. The farmer's data stay in the farmer's store. |
| Developed in the open by a community | Open licences (Apache-2.0 core, GPL plugin), open design documents, tests with a known true answer, contributions from research and practice. |

## Yield and its causes

A yield map says *how much* grew where. Miiro's goal is also to explain
*why*: to separate the effects of

- **weather**: radiation (PAR), temperature sums, water balance, stress periods;
- **soil and terrain**: texture, organic matter, elevation, slope, wetness;
- **management**: sowing, fertilisation and spraying as applied, variety, timing;
- **crop status during the season**: canopy development from satellite and drone images;
- **measurement errors**: machine calibration, flow delay, pass stripes,

so that the next season's decisions rest on causes, not only on last year's
pattern. Every explanation carries its uncertainty, and a cause is claimed
only where the data can support it (for example on-farm trials or repeated
seasons), not from a single correlation.

## Principles

1. **Open and auditable.** Methods are documented and testable; no black boxes.
2. **Data owned by the farmer.** Raw data are kept unchanged, every change is
   logged, and credentials never leave the user's environment.
3. **Honest about quality.** Every product states whether it is absolute or
   relative and how uncertain it is; filled cells are labelled.
4. **Vendor-neutral.** Any combine, sensor, drone, satellite or farm software
   can plug in.
5. **One core, many front ends.** The same computation behind every interface,
   so results do not depend on which tool was used.

## Where we are

The core chain from raw harvester logs to fused yield maps, field weather,
grain quality and management zones works and is tested on a synthetic field.
The next steps toward the vision are in the [roadmap](roadmap.md).
