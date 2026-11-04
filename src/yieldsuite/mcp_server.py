"""Farm-data MCP server: read and update the farm data store, run the chain.

Run with ``yieldsuite mcp --store <root>`` (stdio). Needs ``yieldsuite[mcp]``.

Rules (see docs/ohje/tyonkulku-qgis-webodm-openeo-mcp.md):
- farm data belongs to the farmer; nothing here sends yield data to public services
- L0 is immutable; writes append or version, never overwrite
- human decisions are stored as versioned parameters
- every write is recorded in audit.jsonl with the actor from YIELDSUITE_ACTOR
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .levels import Level
from .store import FarmStore, StoreError

INSTRUCTIONS = """\
Miiro farm-data server. Use it to inspect fields, products and QA,
to record human decisions, and to run the yield-map chain.

- Read tools never change anything. Write tools (update_machine_profile,
  upload_raw, add_scale_ticket, approve_qa, run_chain) are recorded in the
  audit log; confirm them with the user first.
- Fix a map by changing parameters (e.g. update_machine_profile with a new
  tau_d_s) and running the chain again, never by editing products.
- Product classes: absolute (reconciled to scale tickets), absolute_unreconciled,
  relative. Do not present a relative or unreconciled map as calibrated t/ha.
- Yield maps belong to the farmer: do not paste raw records or map files into
  chat or Slack; share QA numbers and product ids.
"""


def build_server(store: FarmStore, actor: str | None = None):
    try:
        from mcp.server.mcpserver import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError
        from mcp.types import ToolAnnotations
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ImportError("the MCP server needs: pip install yieldsuite[mcp]") from exc

    actor = actor or os.environ.get("YIELDSUITE_ACTOR", "mcp-client")
    server = MCPServer("miiro-farm-data", instructions=INSTRUCTIONS)
    read = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )

    def guard(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (StoreError, FileNotFoundError, ValueError, KeyError, RuntimeError, OSError) as exc:
            raise ToolError(str(exc)) from exc

    # --- read ----------------------------------------------------------------------

    @server.tool(annotations=read)
    def list_farms() -> dict:
        """List farm ids in the data store."""
        return {"farms": store.list_farms()}

    @server.tool(annotations=read)
    def list_fields(farm: str) -> dict:
        """List a farm's fields and the harvest years that have data."""
        return {"fields": guard(store.list_fields, farm)}

    @server.tool(annotations=read)
    def list_products(
        farm: str, field: str, year: int | None = None, level: str | None = None
    ) -> dict:
        """List products (L3 records, L4/L5 rasters, QA reports, model cards) of a field."""
        return {"products": guard(store.list_products, farm, field, year, level)}

    @server.tool(annotations=read)
    def get_product(product_id: str) -> dict:
        """Product details. Rasters return grid and yield statistics, JSON products their
        content. product_id is farm/field/year/file as returned by list_products."""
        return guard(store.get_product, product_id)

    @server.tool(annotations=read)
    def get_qa(farm: str, field: str, year: int) -> dict:
        """QA of a field-year: k factors, flag counts, mass-conservation check, L5 model
        card, and the human approvals recorded so far."""
        return guard(store.get_qa, farm, field, year)

    @server.tool(annotations=read)
    def get_machine_profile(farm: str, machine_id: str, version: int | None = None) -> dict:
        """A machine profile (delays, antenna offset, header width, calibration) and its
        version history. Omit version for the current one."""
        return guard(store.get_machine_profile, farm, machine_id, version)

    @server.tool(annotations=read)
    def plan_chain(farm: str, field: str, year: int, to_level: str = "L4") -> dict:
        """Show what run_chain would do: product class and stages, without running."""
        from .pipeline import plan

        cfg = guard(store.load_config, farm, field, year)
        stages = plan(cfg, guard(Level.parse, to_level))
        problems = cfg.validate()
        return {
            "product_class": cfg.product_class.value,
            "stages": [
                {
                    "level": s.target.value,
                    "description": s.description,
                    "implemented": s.implemented,
                }
                for s in stages
            ],
            "config_problems": problems,
        }

    @server.tool(annotations=read)
    def estimate_delay(farm: str, field: str, year: int) -> dict:
        """Estimate each machine's grain-flow delay (tau_d) from adjacent passes driven in
        opposite directions, next to the machine-profile value. Nothing is written; if
        the estimate is identifiable and differs, propose update_machine_profile."""
        from .pipeline import estimate_delays

        cfg = guard(store.load_config, farm, field, year)
        result = guard(estimate_delays, cfg)
        for d in result.values():
            d.pop("curve", None)  # keep the answer short; the CLI shows the curve
        return {"machines": result}

    @server.tool(annotations=read)
    def inspect_harvest_file(
        path: str, crs: str = "EPSG:3067", options: dict | None = None
    ) -> dict:
        """Detect format, columns and units of a yield/machine data file (CSV, TXT,
        Excel, Shapefile, GeoPackage, GeoJSON, ISOXML TaskData, Ag Leader advanced).
        Returns the report, warnings and a config entry to confirm. options may set
        crop, machine_id, default_swath_m, units, columns, vendor_reference_moisture_pct."""
        from .ingest.inspect import inspect_file

        result = guard(inspect_file, path, crs, **(options or {}))
        result.pop("config_yaml", None)
        return result

    public = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )

    @server.tool(annotations=public)
    def get_weather(farm: str, field: str, year: int, refresh: bool = False) -> dict:
        """Field weather and agroclimate for a season: ERA5 (via Open-Meteo) cached in the
        store, with PAR (all-sky, i.e. cloud-aware), clearness index, growing degree days,
        stage dates, precipitation, ET0, water balance, heat and frost days, and field RUE
        when L5 exists. Sends only a rounded location (~5 km) to the weather service."""
        from .l4.io import read_bands
        from .weather.run import run_agroclimate, write_agroclimate

        cfg = guard(store.load_config, farm, field, year)
        grid = None
        l4 = store.year_dir(farm, field, year) / "output" / f"L4_{field}_{year}.tif"
        if l4.exists():
            grid = read_bands(l4)[1]
        try:
            result = run_agroclimate(cfg, grid, refresh=refresh)
        except (ValueError, OSError) as exc:
            raise ToolError(f"weather not available: {exc}") from exc
        files = write_agroclimate(cfg, result, grid)
        store._audit(
            actor,
            "get_weather",
            f"{farm}/{field}/{year}",
            source=result["summary"].get("weather_source"),
        )
        return {
            "summary": result["summary"],
            "products": [f"{farm}/{field}/{year}/{Path(p).name}" for p in files.values()],
        }

    def _grid(farm, field, year):
        from .l4.io import read_bands

        l4 = store.year_dir(farm, field, year) / "output" / f"L4_{field}_{year}.tif"
        return read_bands(l4)[1] if l4.exists() else None

    @server.tool(annotations=public)
    def search_sentinel2(
        farm: str,
        field: str,
        year: int,
        start: str | None = None,
        end: str | None = None,
        max_cloud: float = 60.0,
    ) -> dict:
        """List Sentinel-2 L2A scenes over the field (STAC API, default Copernicus Data
        Space) with date and scene cloud cover, least cloudy per day. Use it to judge
        whether a season has enough clear dates before running L5. Scene cloud cover is
        for the whole tile; the field-level cloud mask is applied later in openEO."""
        from .public.field import field_bbox_wgs84
        from .public.stac import search_sentinel2 as search

        cfg = guard(store.load_config, farm, field, year)
        bbox = guard(field_bbox_wgs84, cfg, _grid(farm, field, year))
        try:
            return search(
                bbox,
                start or f"{year}-04-15",
                end or f"{year}-09-15",
                max_cloud,
                cfg.public_data.stac_url,
                cfg.public_data.s2_collection,
            )
        except OSError as exc:
            raise ToolError(f"STAC search failed ({cfg.public_data.stac_url}): {exc}") from exc

    @server.tool(annotations=public)
    def get_parcels(farm: str, field: str, year: int) -> dict:
        """Fetch field parcels around the field from the configured parcel WFS
        (public_data.parcels_wfs_url / parcels_typename), save them to
        public/parcels.geojson and list their ids and areas. Then call import_boundary
        with the chosen parcel ids."""
        import json as _json

        from .public.field import field_bbox_wgs84
        from .public.wfs import get_features

        cfg = guard(store.load_config, farm, field, year)
        pd_cfg = cfg.public_data
        if not pd_cfg.parcels_wfs_url:
            raise ToolError("set public_data.parcels_wfs_url in field.yaml")
        bbox = guard(field_bbox_wgs84, cfg, _grid(farm, field, year))
        try:
            typename, idc = pd_cfg.parcels_typename, pd_cfg.parcel_id_column
            if typename is None or idc is None:
                from .public.parcels import ParcelService

                choice = ParcelService(pd_cfg.parcels_wfs_url).choose(year, typename, idc)
                typename, idc = choice.typename, choice.id_column
            fc = get_features(pd_cfg.parcels_wfs_url, typename, bbox)
        except (OSError, ValueError) as exc:
            raise ToolError(f"parcel WFS failed: {exc}") from exc
        path = store.public_dir(farm, field, year) / "parcels.geojson"
        path.write_text(_json.dumps(fc))
        import geopandas as gpd

        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:4326").to_crs(cfg.crs)
        parcels = [
            {
                "id": str(r[idc]) if idc in gdf.columns else str(i),
                "area_ha": round(r.geometry.area / 1e4, 2),
            }
            for i, r in gdf.iterrows()
        ]
        store._audit(actor, "get_parcels", f"{farm}/{field}/{year}", n=len(parcels))
        return {"saved": "public/parcels.geojson", "parcels": parcels}

    # --- write ---------------------------------------------------------------------

    @server.tool(annotations=write)
    def import_boundary(
        farm: str,
        field: str,
        year: int,
        source_path: str,
        parcel_ids: list[str] | None = None,
        id_column: str | None = None,
    ) -> dict:
        """Set the field boundary from a vector file (absolute path, or relative to the
        field-year folder such as public/parcels.geojson). Without parcel_ids the
        parcels overlapping the harvested area are chosen. Enables the FIELD_EDGE flag
        on the next run_chain."""
        return guard(
            store.import_boundary, farm, field, year, source_path, actor, parcel_ids, id_column
        )

    @server.tool(annotations=write)
    def register_harvest_log(
        farm: str, field: str, year: int, source_path: str, options: dict | None = None
    ) -> dict:
        """Copy a yield/machine data file into L0 and add it to the field-year config
        with the detected (or given) columns and units. Inspect it first and confirm
        the warnings with the user."""
        return guard(
            store.register_harvest_log, farm, field, year, source_path, options or {}, actor
        )

    @server.tool(annotations=write)
    def update_machine_profile(
        farm: str, machine_id: str, changes: dict[str, Any], reason: str
    ) -> dict:
        """Create a new version of a machine profile (earlier versions are kept).
        Example changes: {"tau_d_s": 14} after checking opposite passes in QGIS.
        Run the chain afterwards to apply it."""
        return guard(store.update_machine_profile, farm, machine_id, changes, reason, actor)

    @server.tool(annotations=write)
    def upload_raw(
        farm: str, field: str, year: int, kind: str, source_path: str, name: str | None = None
    ) -> dict:
        """Copy a local file into the immutable L0 store and record its sha256.
        kind: harvest_log, scale_tickets, drone_images, boundary or other."""
        return guard(store.upload_raw, farm, field, year, kind, source_path, actor, name)

    @server.tool(annotations=write)
    def add_scale_ticket(
        farm: str,
        field: str,
        year: int,
        mass_kg: float,
        machine_id: str | None = None,
        moisture_pct: float | None = None,
        ticket_ref: str = "",
    ) -> dict:
        """Append a weighed load (weighbridge ticket, grain-cart scale, dryer intake).
        Give moisture_pct if the weight is wet; it is converted to reference moisture."""
        return guard(
            store.add_scale_ticket,
            farm,
            field,
            year,
            mass_kg,
            actor,
            machine_id,
            moisture_pct,
            ticket_ref,
        )

    @server.tool(annotations=write)
    def make_terrain(
        farm: str, field: str, dem_path: str | None = None, buffer_m: float = 200.0
    ) -> dict:
        """Terrain covariates (elevation, slope, topographic position, wetness index) on the
        field's yield grid; use the returned path as a make_zones covariate. Without
        dem_path the NLS 2 m elevation model (Finland) is fetched for the field plus the
        buffer; needs NLS_API_KEY. A given DEM should extend ~200 m beyond the field."""
        return guard(store.make_terrain, farm, field, dem_path, actor, buffer_m)

    @server.tool(annotations=write)
    def make_zones(
        farm: str,
        field: str,
        years: list[int] | None = None,
        covariates: list[str] | None = None,
        k_max: int = 6,
    ) -> dict:
        """Management zones from the field's multi-year yield maps (L5 where available,
        else L4) plus optional covariate rasters (paths relative to the field folder):
        relative yield per year, MULTISPATI-PCA, fuzzy k-means with k chosen by FPI/NCE,
        and temporal stability classes. Writes GeoTIFF, GeoPackage and a summary."""
        result = guard(store.make_zones, farm, field, actor, years, covariates, k_max)
        result["summary"].pop("spatial_eigenvalues", None)
        return result

    @server.tool(annotations=public)
    def get_boundary_by_parcel_id(
        farm: str, field: str, year: int, parcel_ids: list[str] | None = None
    ) -> dict:
        """Field boundary from parcel numbers (peruslohkotunnus), fetched from the parcel
        register WFS (default: Ruokavirasto INSPIRE WFS, the layer of this year). Stores
        the boundary in L0, sets it in field.yaml and enables FIELD_EDGE. Reports which
        layer and id attribute were used."""
        return guard(store.boundary_from_parcel_ids, farm, field, year, actor, parcel_ids)

    @server.tool(annotations=public)
    def webodm_submit(
        farm: str,
        field: str,
        year: int,
        images: str,
        date: str,
        multispectral_only: bool = True,
        options: dict | None = None,
    ) -> dict:
        """Send a drone flight (folder or zip, e.g. raw/drone_images/flight.zip after
        upload_raw) to the configured WebODM (WEBODM_URL + credentials). Mavic 3M
        multispectral frames and radiometric calibration by default."""
        return guard(
            store.webodm_submit,
            farm,
            field,
            year,
            images,
            date,
            actor,
            None,
            multispectral_only,
            options,
        )

    @server.tool(annotations=read)
    def webodm_status(farm: str, field: str, year: int) -> dict:
        """Status of this field-year's WebODM tasks (queued/running/completed/failed)."""
        return {"tasks": guard(store.webodm_status, farm, field, year)}

    @server.tool(annotations=write)
    def import_orthomosaic(
        farm: str,
        field: str,
        year: int,
        date: str,
        task_id: str | None = None,
        source_path: str | None = None,
        sensor: str = "mavic3m",
    ) -> dict:
        """Store a reflectance orthomosaic (from a completed WebODM task or a local file)
        in L0 and add it as drone imagery; the next L5 run harmonises it to Sentinel-2."""
        return guard(
            store.import_orthomosaic, farm, field, year, date, actor, task_id, source_path, sensor
        )

    @server.tool(annotations=write)
    def approve_qa(farm: str, field: str, year: int, decision: str, note: str = "") -> dict:
        """Record a human QA decision: approved, rejected or needs_data."""
        return guard(store.approve_qa, farm, field, year, decision, note, actor)

    @server.tool(annotations=write)
    def run_chain(
        farm: str, field: str, year: int, to_level: str = "L4", notify_slack: bool = False
    ) -> dict:
        """Run the yield-map chain for a field-year and return the run summary
        (k factors, flagged share, mass check, output product ids). With notify_slack,
        post the summary (numbers and ids only) to the configured Slack channel."""
        from .pipeline import run

        cfg = guard(store.load_config, farm, field, year)
        summary = run(cfg, guard(Level.parse, to_level))
        ydir = store.year_dir(farm, field, year)
        result = {
            "ok": summary.ok,
            "error": summary.error,
            "product_class": summary.product_class.value,
            "completed": [lv.value for lv in summary.completed],
            "k_factors": summary.k_factors,
            "flagged_pct": summary.flagged_pct,
            "mass_ok": summary.mass_ok,
            "products": [
                f"{farm}/{field}/{year}/{Path(p).name}"
                for p in summary.outputs.values()
                if Path(p).parent == ydir / "output"
            ],
            "machine_profiles": {m: p.version for m, p in cfg.machines.items()},
        }
        if notify_slack:
            from .config import SlackSettings
            from .notify.slack import SlackNotifier

            try:
                SlackNotifier(cfg.slack or SlackSettings()).notify_run(summary)
                result["slack"] = "sent"
            except Exception as exc:  # noqa: BLE001 - report, the run itself succeeded
                result["slack"] = f"not sent: {exc}"
        store.record_run(
            farm,
            field,
            year,
            actor,
            {k: result[k] for k in ("ok", "error", "completed", "k_factors")},
        )
        return result

    return server


def main(store_root: str | Path | None = None) -> None:  # pragma: no cover - stdio loop
    root = store_root or os.environ.get("YIELDSUITE_STORE")
    if not root:
        raise SystemExit("give --store or set YIELDSUITE_STORE")
    build_server(FarmStore(root)).run("stdio")
