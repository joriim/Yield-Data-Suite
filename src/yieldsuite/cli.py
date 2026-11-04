"""Command-line tool: ``yieldsuite``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import SlackSettings, load_config
from .levels import Level
from .pipeline import plan, run


def _cmd_validate(args) -> int:
    cfg = load_config(args.config)
    problems = cfg.validate()
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if not problems:
        print(f"{cfg.field_id}/{cfg.year}: OK, product class {cfg.product_class.value}")
    return 1 if problems else 0


def _cmd_plan(args) -> int:
    cfg = load_config(args.config)
    print(f"{cfg.field_id}/{cfg.year} · product class {cfg.product_class.value}")
    for stage in plan(cfg, Level.parse(args.to), Level.parse(args.start)):
        status = "ready" if stage.implemented else "not implemented"
        print(f"  {stage.target.value:4} {stage.description}  [{status}]")
    return 0


def _cmd_run(args) -> int:
    cfg = load_config(args.config)
    problems = cfg.validate()
    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        return 1
    summary = run(cfg, Level.parse(args.to), Level.parse(args.start))
    if summary.ok:
        print(f"done: {', '.join(lv.value for lv in summary.completed)}")
        for name, path in summary.outputs.items():
            print(f"  {name:10} {path}")
        if summary.k_factors:
            print("  k factors  " + ", ".join(f"{m}={k:.3f}" for m, k in summary.k_factors.items()))
        if summary.flagged_pct is not None:
            print(f"  flagged    {summary.flagged_pct:.1f} % of harvesting records")
        if summary.mass_ok is not None:
            print(f"  mass check {'OK' if summary.mass_ok else 'FAIL'}")
    else:
        print(f"error: {summary.error}", file=sys.stderr)
    if args.notify == "slack":
        from .notify.slack import SlackNotifier

        SlackNotifier(cfg.slack or SlackSettings()).notify_run(summary)
    return 0 if summary.ok else 2


def _cmd_fuse(args) -> int:
    from .l4.io import read_bands
    from .l5.run import run_sentinel2_fusion
    from .pipeline import RunSummary

    cfg = load_config(args.config)
    l4, grid, raster_crs = read_bands(args.l4)
    if raster_crs and raster_crs.upper() != cfg.crs.upper():
        print(f"warning: L4 raster CRS {raster_crs} differs from config {cfg.crs}", file=sys.stderr)
    summary = RunSummary(cfg.field_id, cfg.year, Level.L5, cfg.product_class)
    try:
        outputs = run_sentinel2_fusion(
            cfg,
            l4,
            grid,
            features_path=args.features,
            out_dir=args.out,
            method=args.method,
            residual=args.residual,
            batch=args.batch,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the user and to Slack
        summary.error = f"L5 failed: {exc}"
    else:
        card = outputs.result.model_card
        fit, cells = card["fit"], card["cells"]
        summary.completed.append(Level.L5)
        summary.notes = [
            f"{len(card['sentinel2']['dates_used'])} clear S2 dates",
            f"R² {fit['r2']:.2f}",
            f"CV RMSE {fit.get('cv_rmse', float('nan')):.2f} t/ha",
            f"filled {100 * cells['filled_share']:.0f}% of cells",
        ]
        print(f"wrote {outputs.raster}\n      {outputs.model_card}")
        print(" · ".join(summary.notes))
    if summary.error:
        print(f"error: {summary.error}", file=sys.stderr)
    if args.notify == "slack":
        from .notify.slack import SlackNotifier

        SlackNotifier(cfg.slack or SlackSettings()).notify_run(summary)
    return 0 if summary.ok else 2


def _cmd_demo(args) -> int:
    from .synthetic import write_demo

    path = write_demo(args.out_dir)
    print(f"wrote synthetic field-year to {path.parent}")
    print(f"next: yieldsuite run {path} --to L4")
    return 0


def _cmd_mcp(args) -> int:  # pragma: no cover - stdio loop
    from .mcp_server import main as mcp_main

    mcp_main(args.store)
    return 0


def _cmd_store_demo(args) -> int:
    from .store import init_demo_store

    root = init_demo_store(args.root)
    print(f"demo store at {root}: farm demo-farm, field synthetic-01, year 2026")
    print(f"next: yieldsuite mcp --store {root}")
    return 0


def _cmd_inspect(args) -> int:
    from .ingest.inspect import format_report, inspect_file

    kwargs = {
        k: v
        for k, v in {
            "format": args.format,
            "crop": args.crop,
            "machine_id": args.machine,
            "default_swath_m": args.swath,
            "position_crs": args.position_crs,
            "vendor_reference_moisture_pct": args.vendor_moisture,
            "units": dict(u.split("=", 1) for u in args.unit or []),
            "columns": dict(c.split("=", 1) for c in args.column or []),
        }.items()
        if v not in (None, {}, [])
    }
    try:
        result = inspect_file(args.path, crs=args.crs, **kwargs)
    except (ValueError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(format_report(result))
    return 0


def _cmd_weather(args) -> int:
    import json

    from .l4.io import read_bands
    from .weather.run import run_agroclimate, write_agroclimate

    cfg = load_config(args.config)
    grid = read_bands(args.l4)[1] if args.l4 else None
    try:
        result = run_agroclimate(cfg, grid, refresh=args.refresh)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    files = write_agroclimate(cfg, result, grid)
    print(json.dumps(result["summary"], indent=2, default=str))
    print(f"wrote {files['agroclimate']} (weather cache {result['weather_file']})")
    return 0


def _cmd_delay(args) -> int:
    from .pipeline import estimate_delays

    cfg = load_config(args.config)
    for mid, d in estimate_delays(cfg).items():
        print(
            f"{mid}: profile {d['profile_tau_d_s']} s, estimated {d['estimated_tau_d_s']} s "
            f"({d['opposite_pass_pairs']} opposite-pass pairs, contrast "
            f"{(d['contrast'] or 0):.2f}){'' if d['identifiable'] else ' - ' + d['reason']}"
        )
        curve = {float(k): v for k, v in d["curve"].items() if v is not None}
        if curve:
            lo, hi = min(curve.values()), max(curve.values())
            for tau in sorted(curve)[::2]:
                bar = "#" * int(1 + 40 * (curve[tau] - lo) / ((hi - lo) or 1))
                print(f"  {tau:5.1f} s {bar}")
    return 0


def _cmd_s2_search(args) -> int:
    from .l4.io import read_bands
    from .public.field import field_bbox_wgs84
    from .public.stac import search_sentinel2

    cfg = load_config(args.config)
    grid = read_bands(args.l4)[1] if args.l4 else None
    try:
        res = search_sentinel2(
            field_bbox_wgs84(cfg, grid),
            args.start or f"{cfg.year}-04-15",
            args.end or f"{cfg.year}-09-15",
            args.max_cloud,
            cfg.public_data.stac_url,
            cfg.public_data.s2_collection,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"{res['n_below_max_cloud']}/{res['n_scenes']} scenes <= {args.max_cloud} % cloud; "
        f"{len(res['dates'])} dates"
    )
    for d in res["dates"]:
        print(f"  {d['date']}  {d['cloud_cover']:5.1f} %  {d['tile']}")
    utm = res.get("utm_epsg") or {}
    if utm:
        several = len(utm["zones_seen"]) > 1
        note = f"; tiles of several zones: {utm['zones_seen']}" if several else ""
        print(
            f"grid CRS aligned with these Sentinel-2 pixels: {utm['epsg']} "
            f"(UTM zone {utm['zone']}){note}"
        )
    return 0


def _cmd_zones(args) -> int:
    import json

    from .l4.io import read_bands
    from .zones import make_zones, place, union_grid, write_zones

    layers, grids, crs = [], [], args.crs
    for p in args.layers:
        b, g, c = read_bands(p)
        layers.append(b["yield"])
        grids.append(g)
        crs = crs or c
    covs = []
    for p in args.covariate or []:
        b, g, _ = read_bands(p, require_yield=False)
        covs += [(arr, g) for arr in b.values()]
        grids.append(g)
    target = union_grid(grids)
    try:
        res = make_zones(
            [place(a, g, target) for a, g in zip(layers, grids, strict=False)],
            target,
            [place(a, g, target) for a, g in covs],
            k_range=range(2, args.k_max + 1),
            names=[Path(p).stem for p in args.layers],
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    files = write_zones(res, target, crs, args.out, args.stem)
    s = res.summary
    print(
        f"{s['k']} zones from {len(layers)} years ({s['spatial_components']} spatial PCs); "
        f"zones explain {100 * (s['variance_explained_by_zones'] or 0):.0f} % of the "
        "variance of mean relative yield"
    )
    for z in s["zones"]:
        print(f"  zone {z['zone']}: {z['area_ha']:.2f} ha, {z['mean_relative_yield_pct']:.0f} %")
    print(
        "  k scan: "
        + ", ".join(f"k={r['k']} FPI {r['fpi']:.3f} NCE {r['nce']:.3f}" for r in s["k_scan"])
    )
    print(json.dumps(files, indent=2))
    return 0


def _cmd_grainsense(args) -> int:
    import json

    from .quality.grainsense import DEFAULT_BASE, GrainSenseClient

    client = GrainSenseClient(args.base or DEFAULT_BASE)
    try:
        if not client.token:
            client.login()
        if args.action == "sessions":
            for sess in client.flow_sessions(start=args.start, end=args.end):
                print(json.dumps(sess, default=str)[:400])
        elif args.action == "export":
            if not args.uuid or not args.out:
                print("error: export needs --uuid and --out", file=sys.stderr)
                return 1
            data = client.export_flow_sessions(args.uuid, args.format)
            Path(args.out).write_bytes(data)
            print(f"wrote {args.out} ({len(data)} bytes); add it under quality.sources")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_webodm(args) -> int:
    import json

    from .drone.webodm import WebODMClient, select_images

    try:
        client = WebODMClient(args.url)
        if args.action == "submit":
            files = select_images(args.images, not args.all_images)
            pid = client.project(args.project)
            tid = client.submit(
                pid,
                files,
                args.name or Path(args.images).name,
                progress=lambda d, n: print(f"  uploaded {d}/{n}"),
            )
            print(json.dumps({"project_id": pid, "task_id": tid, "images": len(files)}))
        elif args.action == "status":
            print(json.dumps(client.status(args.project_id, args.task)))
        elif args.action == "download":
            out = client.download(args.project_id, args.task, args.asset, args.out)
            print(f"wrote {out}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_parcel(args) -> int:
    import json

    from .public.parcels import RUOKAVIRASTO_WFS, ParcelService

    service = ParcelService(args.url or RUOKAVIRASTO_WFS)
    try:
        choice = service.choose(args.year, args.layer, args.id_column)
        print(f"layer {choice.typename} ({choice.reason}); id attribute {choice.id_column}")
        fc = service.fetch(args.ids, choice)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = Path(args.out)
    if out.suffix.lower() == ".geojson":
        out.write_text(json.dumps(fc))
    else:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame.from_features(fc["features"], crs="EPSG:3067")
        gdf.to_file(out, driver="GPKG")
        for _, r in gdf.iterrows():
            print(f"  {r[choice.id_column]}: {r.geometry.area / 1e4:.2f} ha")
    print(f"wrote {out}; use it as `boundary:` in field.yaml")
    return 0


def _cmd_terrain(args) -> int:
    import json

    from .covariates.terrain import TERRAIN_BANDS, terrain_covariates
    from .l4.io import read_bands, write_cog

    _, grid, crs = read_bands(args.like, require_yield=False)
    try:
        dem = args.dem
        if dem is None:
            from .public.nls_dem import NLSElevation, grid_bbox

            if (args.crs or crs).upper() != "EPSG:3067":
                raise ValueError("the NLS DEM is fetched in EPSG:3067; give a DEM file instead")
            dem = Path(args.out).with_name("dem_nls_2m.tif")
            NLSElevation().fetch(grid_bbox(grid, args.buffer + 40), dem)
            print(f"fetched {dem} (National Land Survey of Finland, CC BY 4.0)")
        bands, report = terrain_covariates(dem, grid, args.crs or crs, args.buffer)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    out = write_cog(bands, grid, args.crs or crs, args.out, names=TERRAIN_BANDS)
    print(json.dumps(report, indent=2))
    print(f"wrote {out}; use it as a zones covariate (--covariate {out})")
    return 0


def _cmd_notify_test(args) -> int:
    from .notify.slack import SlackNotifier

    settings = load_config(args.config).slack if args.config else None
    SlackNotifier(settings or SlackSettings()).send(
        f":seedling: yieldsuite {__version__} can post to this channel"
    )
    print("sent")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=f"yieldsuite {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="check a field-year config")
    v.add_argument("config")
    v.set_defaults(func=_cmd_validate)

    for name, func, helptext in (
        ("plan", _cmd_plan, "show the stages a run would execute"),
        ("run", _cmd_run, "process a field-year"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("config")
        s.add_argument("--to", default="L4", help="last level to produce (default L4)")
        s.add_argument("--from", dest="start", default="L1A", help="first level to rebuild")
        if name == "run":
            s.add_argument("--notify", choices=["slack"], help="post a run summary")
        s.set_defaults(func=func)

    f = sub.add_parser("fuse", help="L4 -> L5: Sentinel-2 fusion via openEO")
    f.add_argument("config")
    f.add_argument("--l4", required=True, help="L4 GeoTIFF (band 'yield', optionally 'coverage')")
    f.add_argument("--features", help="cached feature CSV; fetched from openEO if missing")
    f.add_argument("--out", help="output directory (default: output_dir from config)")
    f.add_argument("--method", choices=["linear", "rf"], default="linear")
    f.add_argument("--residual", choices=["kriging", "none"], default="kriging")
    f.add_argument("--batch", action="store_true", help="run as an openEO batch job")
    f.add_argument("--notify", choices=["slack"], help="post a run summary")
    f.set_defaults(func=_cmd_fuse)

    d = sub.add_parser("demo", help="write a synthetic field-year to try the chain")
    d.add_argument("out_dir")
    d.set_defaults(func=_cmd_demo)

    i = sub.add_parser("inspect", help="detect format, columns and units of a data file")
    i.add_argument("path")
    i.add_argument("--crs", default="EPSG:3067", help="field CRS (default EPSG:3067)")
    i.add_argument("--format", default="auto")
    i.add_argument("--crop", help="crop name, needed for bu/ac yields")
    i.add_argument("--machine", help="machine id when the file has no machine column")
    i.add_argument("--swath", type=float, help="header width [m] when the file has none")
    i.add_argument("--position-crs", help="CRS of x/y columns if not the field CRS")
    i.add_argument(
        "--vendor-moisture",
        type=float,
        help="reference moisture [%%] of dry/standard yields in the file",
    )
    i.add_argument("--unit", action="append", help="confirm a unit, e.g. --unit speed=mph")
    i.add_argument("--column", action="append", help="map a column, e.g. --column yield_dry=Sato")
    i.set_defaults(func=_cmd_inspect)

    dl = sub.add_parser("delay", help="estimate the flow delay from opposite passes")
    dl.add_argument("config")
    dl.set_defaults(func=_cmd_delay)

    s2 = sub.add_parser("s2-search", help="list Sentinel-2 scenes over the field (STAC)")
    s2.add_argument("config")
    s2.add_argument("--l4", help="L4 raster to locate the field without boundary/location")
    s2.add_argument("--start")
    s2.add_argument("--end")
    s2.add_argument("--max-cloud", type=float, default=60.0)
    s2.set_defaults(func=_cmd_s2_search)

    z = sub.add_parser("zones", help="management zones from multi-year yield maps (KM-sPC)")
    z.add_argument("layers", nargs="+", help="L4/L5 GeoTIFFs, one per year, same grid origin")
    z.add_argument("--covariate", action="append", help="extra raster (elevation, ECa, ...)")
    z.add_argument("--out", default="zones")
    z.add_argument("--stem", default="zones")
    z.add_argument("--k-max", type=int, default=6)
    z.add_argument("--crs", help="CRS if the rasters do not carry one")
    z.set_defaults(func=_cmd_zones)

    gs = sub.add_parser("grainsense", help="list or export GrainSense Flow (combine) sessions")
    gs.add_argument("action", choices=["sessions", "export"])
    gs.add_argument("--start", help="ISO time, e.g. 2026-08-01T00:00:00Z")
    gs.add_argument("--end")
    gs.add_argument("--uuid", action="append", help="session uuid (repeat)")
    gs.add_argument("--format", default="xlsx", choices=["xlsx", "csv"])
    gs.add_argument("--out")
    gs.add_argument("--base", help="API base URL (default commercial.grainsense.com/api)")
    gs.set_defaults(func=_cmd_grainsense)

    wo = sub.add_parser("webodm", help="process drone flights with WebODM")
    wo.add_argument("action", choices=["submit", "status", "download"])
    wo.add_argument("--url", help="WebODM URL (default $WEBODM_URL)")
    wo.add_argument("--images", help="flight folder (submit)")
    wo.add_argument(
        "--all-images",
        action="store_true",
        help="upload all images, not only Mavic 3M multispectral frames",
    )
    wo.add_argument("--project", default="yieldsuite", help="project name (submit)")
    wo.add_argument("--name", help="task name (submit)")
    wo.add_argument("--project-id", type=int)
    wo.add_argument("--task")
    wo.add_argument("--asset", default="orthophoto.tif")
    wo.add_argument("--out", default="orthophoto.tif")
    wo.set_defaults(func=_cmd_webodm)

    pc = sub.add_parser("parcel", help="field boundary by parcel number (parcel register WFS)")
    pc.add_argument("ids", nargs="+", help="parcel numbers (peruslohkotunnus)")
    pc.add_argument("--year", type=int, help="register year (default: latest layer)")
    pc.add_argument("--out", default="boundary.gpkg")
    pc.add_argument("--url", help="WFS URL (default: Ruokavirasto INSPIRE WFS)")
    pc.add_argument("--layer", help="feature type, if discovery picks the wrong one")
    pc.add_argument("--id-column", help="parcel id attribute, if discovery picks the wrong one")
    pc.set_defaults(func=_cmd_parcel)

    te = sub.add_parser("terrain", help="terrain covariates (elevation, slope, TPI, TWI)")
    te.add_argument(
        "dem",
        nargs="?",
        help="elevation model GeoTIFF (projected CRS, metres); "
        "omit to fetch the NLS 2 m DEM (Finland, needs NLS_API_KEY)",
    )
    te.add_argument("--like", required=True, help="L4/L5 raster whose grid to use")
    te.add_argument("--out", default="terrain.tif")
    te.add_argument("--buffer", type=float, default=200.0, help="TWI catchment buffer [m]")
    te.add_argument("--crs")
    te.set_defaults(func=_cmd_terrain)

    w = sub.add_parser("weather", help="field weather (ERA5 via Open-Meteo) and agroclimate")
    w.add_argument("config")
    w.add_argument("--l4", help="L4 raster to locate the field when config has no location")
    w.add_argument("--refresh", action="store_true", help="fetch again even if cached")
    w.set_defaults(func=_cmd_weather)

    m = sub.add_parser("mcp", help="run the farm-data MCP server (stdio)")
    m.add_argument("--store", help="data store root (default: $YIELDSUITE_STORE)")
    m.set_defaults(func=_cmd_mcp)

    sd = sub.add_parser("store-demo", help="create a data store with a synthetic field")
    sd.add_argument("root")
    sd.set_defaults(func=_cmd_store_demo)

    n = sub.add_parser("notify-test", help="post a test message to Slack")
    n.add_argument("config", nargs="?")
    n.set_defaults(func=_cmd_notify_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
