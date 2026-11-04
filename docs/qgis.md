# QGIS plugin

The *Miiro* provider adds six Processing algorithms: inspect a
data file, run the chain (L1–L4/L5, loads L3 records, L4, L5 and Q4),
estimate the flow delay, fuse with Sentinel-2, make management zones and
summarise field weather. They call the same core functions as the command
line, so results match exactly.

Install and development notes:
[`qgis_plugin/README.md`](https://github.com/joriim/Yield-Data-Suite/tree/main/qgis_plugin).
Tested in a headless QGIS 3.34 in CI.
