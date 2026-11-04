"""Farm data store (local folder mode).

Layout::

    <root>/
      audit.jsonl                          every write: who, when, what, why
      <farm>/
        machines/<machine_id>.yaml         versioned machine profiles (append-only)
        <field>/<year>/
          field.yaml                       field-year config (paths relative to this folder)
          raw/                             L0, immutable; raw/manifest.json holds sha256
          raw/scale_tickets.csv            append-only
          approvals.jsonl                  human QA decisions
          output/                          products L3-L5 written by the chain

The server deployment replaces this with object storage + PostGIS + pgSTAC
behind the same methods.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from dataclasses import asdict, fields
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from .config import FieldYearConfig, load_config
from .levels import Level
from .registry import MachineProfile

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PRODUCT = re.compile(r"^(L[0-9][AB]?|Q[1-4])_.+")
RAW_KINDS = ("harvest_log", "scale_tickets", "drone_images", "boundary", "other")


class StoreError(ValueError):
    """A request the store refuses (bad id, missing item, overwrite of L0)."""


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID.match(value) or ".." in value:
        raise StoreError(f"invalid {what}: {value!r} (letters, digits, _ . - only)")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FarmStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise StoreError(f"store root does not exist: {self.root}")

    # --- paths -------------------------------------------------------------------

    def farm_dir(self, farm: str) -> Path:
        p = self.root / _check_id(farm, "farm id")
        if not p.is_dir():
            raise StoreError(f"unknown farm {farm!r}")
        return p

    def year_dir(self, farm: str, field: str, year: int) -> Path:
        p = self.farm_dir(farm) / _check_id(field, "field id") / str(int(year))
        if not (p / "field.yaml").exists():
            raise StoreError(f"no field-year {farm}/{field}/{year}")
        return p

    def _audit(self, actor: str, action: str, target: str, reason: str = "", **details) -> None:
        entry = {
            "time": _now(),
            "actor": actor or "unknown",
            "action": action,
            "target": target,
            "reason": reason,
            **details,
        }
        with open(self.root / "audit.jsonl", "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    # --- read --------------------------------------------------------------------

    def list_farms(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and _ID.match(p.name))

    def list_fields(self, farm: str) -> list[dict]:
        out = []
        for fdir in sorted(self.farm_dir(farm).iterdir()):
            if not fdir.is_dir() or fdir.name == "machines" or not _ID.match(fdir.name):
                continue
            years = sorted(
                int(y.name)
                for y in fdir.iterdir()
                if y.is_dir() and y.name.isdigit() and (y / "field.yaml").exists()
            )
            if years:
                out.append({"field_id": fdir.name, "years": years})
        return out

    def load_config(self, farm: str, field: str, year: int) -> FieldYearConfig:
        """Field-year config with machine profiles replaced by the store's latest versions."""
        ydir = self.year_dir(farm, field, year)
        cfg = load_config(ydir / "field.yaml")
        if cfg.year != int(year) or cfg.field_id != field:
            raise StoreError(
                f"{farm}/{field}/{year}/field.yaml says field {cfg.field_id!r} year {cfg.year}; "
                "fix the file so folder and config agree"
            )
        cfg.output_dir = ydir / "output"
        for mid in {log.machine_id for log in cfg.harvest_logs} | set(cfg.machines):
            versions = self._machine_versions(farm, mid)
            if versions:
                cfg.machines[mid] = MachineProfile.from_dict(versions[-1])
        return cfg

    def list_products(
        self, farm: str, field: str, year: int | None = None, level: str | None = None
    ) -> list[dict]:
        years = (
            [year]
            if year is not None
            else next((f["years"] for f in self.list_fields(farm) if f["field_id"] == field), [])
        )
        want = Level.parse(level).value if level else None
        out = []
        for y in years:
            odir = self.year_dir(farm, field, y) / "output"
            if not odir.is_dir():
                continue
            for p in sorted(odir.iterdir()):
                m = _PRODUCT.match(p.name)
                if not p.is_file() or not m or (want and m.group(1) != want):
                    continue
                out.append(
                    {
                        "product_id": f"{farm}/{field}/{y}/{p.name}",
                        "level": m.group(1),
                        "kind": _kind(p.name),
                        "year": y,
                        "path": str(p),
                        "size_bytes": p.stat().st_size,
                        "modified": datetime.fromtimestamp(
                            p.stat().st_mtime, timezone.utc
                        ).isoformat(timespec="seconds"),
                    }
                )
        return out

    def get_product(self, product_id: str) -> dict:
        parts = product_id.split("/")
        if len(parts) != 4:
            raise StoreError("product_id must be farm/field/year/file")
        farm, field, year, name = parts
        p = self.year_dir(farm, field, int(year)) / "output" / name
        if "/" in name or not _PRODUCT.match(name) or not p.is_file():
            raise StoreError(f"unknown product {product_id!r}")
        info = {"product_id": product_id, "path": str(p), "kind": _kind(name)}
        if p.suffix == ".json":
            info["content"] = json.loads(p.read_text())
        elif p.suffix == ".tif":
            info.update(_raster_summary(p))
        return info

    def get_qa(self, farm: str, field: str, year: int) -> dict:
        ydir = self.year_dir(farm, field, year)
        out: dict = {"field_id": field, "year": year}
        for name, key in (
            (f"L4_{field}_{year}_qa.json", "L4_qa"),
            (f"L5_{field}_{year}_model_card.json", "L5_model_card"),
        ):
            p = ydir / "output" / name
            if p.exists():
                out[key] = json.loads(p.read_text())
        appr = ydir / "approvals.jsonl"
        if appr.exists():
            out["approvals"] = [json.loads(line) for line in appr.read_text().splitlines() if line]
        if len(out) == 2:
            out["note"] = "no QA yet: run the chain first"
        return out

    def _machine_file(self, farm: str, machine_id: str) -> Path:
        return self.farm_dir(farm) / "machines" / f"{_check_id(machine_id, 'machine id')}.yaml"

    def _machine_versions(self, farm: str, machine_id: str) -> list[dict]:
        p = self._machine_file(farm, machine_id)
        if not p.exists():
            return []
        return (yaml.safe_load(p.read_text()) or {}).get("versions", [])

    def get_machine_profile(self, farm: str, machine_id: str, version: int | None = None) -> dict:
        versions = self._machine_versions(farm, machine_id)
        if not versions:
            raise StoreError(f"no profile for machine {machine_id!r} on farm {farm!r}")
        if version is None:
            current = versions[-1]
        else:
            match = [v for v in versions if v.get("version") == version]
            if not match:
                raise StoreError(f"machine {machine_id!r} has no version {version}")
            current = match[0]
        history = [
            {k: v.get(k) for k in ("version", "created", "actor", "reason")} for v in versions
        ]
        return {"profile": current, "history": history}

    # --- write (append-only, audited) -----------------------------------------------

    def update_machine_profile(
        self, farm: str, machine_id: str, changes: dict, reason: str, actor: str
    ) -> dict:
        """Append a new profile version; earlier versions are never modified."""
        if not reason:
            raise StoreError("a reason is required for every profile change")
        allowed = {f.name for f in fields(MachineProfile)} - {"machine_id", "version"}
        bad = set(changes) - allowed
        if bad:
            raise StoreError(f"unknown profile fields {sorted(bad)}; allowed: {sorted(allowed)}")
        versions = self._machine_versions(farm, machine_id)
        base = dict(versions[-1]) if versions else {"machine_id": machine_id, "version": 0}
        for meta in ("created", "actor", "reason"):
            base.pop(meta, None)
        new = {**base, **changes, "machine_id": machine_id, "version": base["version"] + 1}
        new.setdefault("valid_from", date.today().isoformat())
        profile = MachineProfile.from_dict(new)  # validates types
        record = {
            **json.loads(json.dumps(asdict(profile), default=str)),
            "created": _now(),
            "actor": actor,
            "reason": reason,
        }
        p = self._machine_file(farm, machine_id)
        p.parent.mkdir(exist_ok=True)
        p.write_text(yaml.safe_dump({"versions": [*versions, record]}, sort_keys=False))
        self._audit(
            actor,
            "update_machine_profile",
            f"{farm}/machines/{machine_id}",
            reason,
            version=record["version"],
            changes=changes,
        )
        return {
            "machine_id": machine_id,
            "version": record["version"],
            "param_hash": profile.param_hash(),
        }

    def upload_raw(
        self,
        farm: str,
        field: str,
        year: int,
        kind: str,
        source: str | Path,
        actor: str,
        name: str | None = None,
    ) -> dict:
        """Copy a file into L0. Existing L0 files are never overwritten."""
        if kind not in RAW_KINDS:
            raise StoreError(f"kind must be one of {RAW_KINDS}")
        src = Path(source).expanduser()
        if not src.is_file():
            raise StoreError(f"source file not found: {src}")
        name = _check_id(name or src.name, "file name")
        raw = self.year_dir(farm, field, year) / "raw"
        dest = raw / kind / name
        if dest.exists():
            raise StoreError(f"L0 is immutable: {kind}/{name} already exists")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        digest = _sha256(dest)
        manifest_path = raw / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
        entry = {
            "path": f"{kind}/{name}",
            "sha256": digest,
            "size_bytes": dest.stat().st_size,
            "uploaded": _now(),
            "actor": actor,
        }
        manifest_path.write_text(json.dumps([*manifest, entry], indent=2))
        self._audit(actor, "upload_raw", f"{farm}/{field}/{year}/raw/{kind}/{name}", sha256=digest)
        return entry

    def add_scale_ticket(
        self,
        farm: str,
        field: str,
        year: int,
        mass_kg: float,
        actor: str,
        machine_id: str | None = None,
        moisture_pct: float | None = None,
        ticket_ref: str = "",
    ) -> dict:
        if not mass_kg or mass_kg <= 0:
            raise StoreError("mass_kg must be positive")
        if moisture_pct is not None and not 0 <= moisture_pct < 100:
            raise StoreError("moisture_pct must be in [0, 100)")
        ydir = self.year_dir(farm, field, year)
        path = ydir / "raw" / "scale_tickets.csv"
        path.parent.mkdir(exist_ok=True)
        cols = ["machine_id", "mass_kg", "moisture_pct", "ticket_ref", "added", "actor"]
        new = not path.exists()
        if not new:
            with open(path) as f:
                header = next(csv.reader(f), [])
            if header != cols:
                raise StoreError(f"{path.name} has columns {header}; expected {cols}")
        row = [
            machine_id or "*",
            mass_kg,
            "" if moisture_pct is None else moisture_pct,
            ticket_ref,
            _now(),
            actor,
        ]
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(cols)
            w.writerow(row)
        self._set_config_key(farm, field, year, "scale_tickets", "raw/scale_tickets.csv")
        self._audit(
            actor,
            "add_scale_ticket",
            f"{farm}/{field}/{year}",
            mass_kg=mass_kg,
            machine_id=machine_id,
            moisture_pct=moisture_pct,
            ticket_ref=ticket_ref,
        )
        return dict(zip(cols, row, strict=True))

    def register_harvest_log(
        self, farm: str, field: str, year: int, source: str | Path, options: dict, actor: str
    ) -> dict:
        """Upload a data file to L0 and append it to the config's harvest_logs."""
        from .ingest.inspect import inspect_file

        cfg = self.load_config(farm, field, year)
        result = inspect_file(source, cfg.crs, **options)  # fails before anything is written
        src = Path(source)
        if src.is_dir():
            raise StoreError("upload a zip of the TaskData folder, not the folder")
        entry_raw = self.upload_raw(farm, field, year, "harvest_log", src, actor)
        entry = dict(result["config_entry"])
        entry["path"] = f"raw/{entry_raw['path']}"
        p = self.year_dir(farm, field, year) / "field.yaml"
        raw = yaml.safe_load(p.read_text()) or {}
        raw.setdefault("harvest_logs", []).append(entry)
        p.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
        self._audit(
            actor,
            "register_harvest_log",
            f"{farm}/{field}/{year}",
            file=entry["path"],
            warnings=result["report"]["warnings"],
        )
        return {
            "config_entry": entry,
            "warnings": result["report"]["warnings"],
            "sha256": entry_raw["sha256"],
        }

    def public_dir(self, farm: str, field: str, year: int) -> Path:
        d = self.year_dir(farm, field, year) / "public"
        d.mkdir(exist_ok=True)
        return d

    def import_boundary(
        self,
        farm: str,
        field: str,
        year: int,
        source: str | Path,
        actor: str,
        parcel_ids: list[str] | None = None,
        id_column: str | None = None,
    ) -> dict:
        """Store the field boundary in L0 (GeoPackage) and point the config at it.

        ``source`` is a vector file (e.g. parcels downloaded from the parcel
        register or the result of get_parcels in public/parcels.geojson).
        Without parcel ids the parcels overlapping the harvested area are used.
        """
        import geopandas as gpd

        from .public.boundary import select_parcels

        cfg = self.load_config(farm, field, year)
        src = Path(source)
        if not src.is_absolute():
            src = self.year_dir(farm, field, year) / src
        if not src.is_file():
            raise StoreError(f"boundary source not found: {source}")
        gdf = gpd.read_file(src)
        near = None
        if not parcel_ids:
            records = (
                self.year_dir(farm, field, year) / "output" / f"L3_{field}_{year}_records.parquet"
            )
            if records.exists():
                rec = gpd.read_parquet(records)
                near = rec[rec.area > 0].to_crs(cfg.crs).geometry.union_all().convex_hull
        sel = select_parcels(
            gdf, cfg.crs, parcel_ids, id_column or cfg.public_data.parcel_id_column, near
        )
        raw = self.year_dir(farm, field, year) / "raw" / "boundary"
        raw.mkdir(parents=True, exist_ok=True)
        n = 1
        while (raw / f"boundary_{n}.gpkg").exists():
            n += 1
        dest = raw / f"boundary_{n}.gpkg"
        sel.to_file(dest, driver="GPKG")
        rel = f"raw/boundary/{dest.name}"
        self._set_config_key(farm, field, year, "boundary", rel)
        area_ha = float(sel.geometry.area.sum() / 1e4)
        ids = (
            sel[id_column or cfg.public_data.parcel_id_column].astype(str).tolist()
            if (id_column or cfg.public_data.parcel_id_column) in sel.columns
            else []
        )
        self._audit(
            actor,
            "import_boundary",
            f"{farm}/{field}/{year}",
            file=rel,
            parcels=ids,
            area_ha=area_ha,
            sha256=_sha256(dest),
        )
        return {"boundary": rel, "parcels": ids, "n_parcels": len(sel), "area_ha": area_ha}

    def make_terrain(
        self,
        farm: str,
        field: str,
        dem_path: str | Path | None,
        actor: str,
        buffer_m: float = 200.0,
        dem_source=None,
    ) -> dict:
        """Terrain covariates on the field's grid (from its latest L4/L5 map), written to
        <farm>/<field>/covariates/terrain.tif for make_zones. Without ``dem_path`` the
        NLS 2 m elevation model is fetched for the field plus the buffer (NLS_API_KEY)."""
        from .covariates.terrain import TERRAIN_BANDS, terrain_covariates
        from .l4.io import read_bands, write_cog

        fdir = self.farm_dir(farm) / _check_id(field, "field id")
        years = next((f["years"] for f in self.list_fields(farm) if f["field_id"] == field), [])
        like = None
        for y in sorted(years, reverse=True):
            for level in ("L5", "L4"):
                p = self.year_dir(farm, field, y) / "output" / f"{level}_{field}_{y}.tif"
                if p.exists():
                    like = p
                    break
            if like:
                break
        if like is None:
            raise StoreError("run the chain to L4 first: the terrain grid follows the yield grid")
        _, grid, crs = read_bands(like)
        if dem_path is None:
            from .public.nls_dem import NLSElevation, grid_bbox

            bbox = grid_bbox(grid, buffer_m + 40)
            if crs.upper() not in ("EPSG:3067",):
                from pyproj import Transformer

                tr = Transformer.from_crs(crs, "EPSG:3067", always_xy=True)
                xs, ys = tr.transform(
                    [bbox[0], bbox[2], bbox[2], bbox[0]], [bbox[1], bbox[1], bbox[3], bbox[3]]
                )
                bbox = (min(xs), min(ys), max(xs), max(ys))
            dem = fdir / "covariates" / "dem_nls_2m.tif"
            prov = (dem_source or NLSElevation()).fetch(bbox, dem)
            dem_label = f"NLS {prov['coverage']}"
        else:
            dem = Path(dem_path)
            if not dem.is_absolute():
                dem = fdir / dem
            if not dem.is_file():
                raise StoreError(f"DEM not found: {dem_path}")
            dem_label = str(dem_path)
        bands, report = terrain_covariates(dem, grid, crs, buffer_m)
        out = write_cog(bands, grid, crs, fdir / "covariates" / "terrain.tif", names=TERRAIN_BANDS)
        rel = str(out.relative_to(fdir))
        self._audit(actor, "make_terrain", f"{farm}/{field}", dem=dem_label, out=rel)
        return {"covariate": rel, "grid_from": like.name, "report": report}

    def make_zones(
        self,
        farm: str,
        field: str,
        actor: str,
        years: list[int] | None = None,
        covariates: list[str] | None = None,
        k_max: int = 6,
    ) -> dict:
        """Zones from all (or the given) years of this field: L5 maps where they
        exist, else L4. Written to <farm>/<field>/zones/."""
        from .l4.io import read_bands
        from .zones import make_zones, place, union_grid, write_zones

        fdir = self.farm_dir(farm) / _check_id(field, "field id")
        available = next((f["years"] for f in self.list_fields(farm) if f["field_id"] == field), [])
        years = sorted(years or available)
        layers, grids, names, used = [], [], [], {}
        crs = None
        for y in years:
            out = self.year_dir(farm, field, y) / "output"
            for level in ("L5", "L4"):
                p = out / f"{level}_{field}_{y}.tif"
                if p.exists():
                    bands, grid, crs = read_bands(p)
                    layers.append(bands["yield"])
                    grids.append(grid)
                    names.append(f"{y}_{level}")
                    used[y] = level
                    break
        if len(layers) < 2:
            raise StoreError(f"zones need yield maps of at least two years; found {sorted(used)}")
        cov_arrays = []
        for c in covariates or []:
            cp = Path(c) if Path(c).is_absolute() else fdir / c
            if not cp.is_file():
                raise StoreError(f"covariate not found: {c}")
            b, g, _ = read_bands(cp, require_yield=False)
            cov_arrays += [(arr, g) for arr in b.values()]  # every band is a covariate
            grids.append(g)
        target = union_grid(grids)
        placed = [place(a, g, target) for a, g in zip(layers, grids, strict=False)]
        covs = [place(a, g, target) for a, g in cov_arrays]
        res = make_zones(placed, target, covs, k_range=range(2, k_max + 1), names=names)
        stem = f"zones_{field}_{years[0]}-{years[-1]}"
        files = write_zones(res, target, crs, fdir / "zones", stem)
        self._audit(
            actor,
            "make_zones",
            f"{farm}/{field}",
            years=list(used),
            k=res.k,
            covariates=covariates or [],
        )
        return {
            "files": {k: str(Path(v).relative_to(self.root)) for k, v in files.items()},
            "years_used": used,
            "summary": res.summary,
        }

    # --- drone (WebODM) ----------------------------------------------------------

    def _tasks_file(self, farm: str, field: str, year: int) -> Path:
        d = self.year_dir(farm, field, year) / "drone"
        d.mkdir(exist_ok=True)
        return d / "webodm_tasks.json"

    def webodm_submit(
        self,
        farm: str,
        field: str,
        year: int,
        images: str | Path,
        date: str,
        actor: str,
        client=None,
        multispectral_only: bool = True,
        options: dict | None = None,
        name: str | None = None,
    ) -> dict:
        """Send a flight's images (folder or zip; relative paths are inside the
        field-year folder, e.g. raw/drone_images/flight.zip) to WebODM."""
        import tempfile
        import zipfile

        from .drone.webodm import WebODMClient, select_images

        date = str(date)
        src = Path(images)
        if not src.is_absolute():
            src = self.year_dir(farm, field, year) / src
        if not src.exists():
            raise StoreError(f"images not found: {images}")
        client = client or WebODMClient()
        with tempfile.TemporaryDirectory() as tmp:
            folder = src
            if src.is_file() and zipfile.is_zipfile(src):
                with zipfile.ZipFile(src) as z:
                    z.extractall(tmp)
                folder = Path(tmp)
            files = select_images(folder, multispectral_only)
            project_id = client.project(f"{farm}/{field}")
            task_name = name or f"{field} {date}"
            task_id = client.submit(project_id, files, task_name, options)
        entry = {
            "task_id": task_id,
            "project_id": project_id,
            "date": date,
            "images": len(files),
            "name": task_name,
            "source": str(images),
            "submitted": _now(),
            "actor": actor,
        }
        path = self._tasks_file(farm, field, year)
        tasks = json.loads(path.read_text()) if path.exists() else []
        path.write_text(json.dumps([*tasks, entry], indent=2))
        self._audit(
            actor,
            "webodm_submit",
            f"{farm}/{field}/{year}",
            task_id=task_id,
            images=len(files),
            date=date,
        )
        return entry

    def webodm_status(self, farm: str, field: str, year: int, client=None) -> list[dict]:
        from .drone.webodm import WebODMClient

        path = self._tasks_file(farm, field, year)
        tasks = json.loads(path.read_text()) if path.exists() else []
        if not tasks:
            return []
        client = client or WebODMClient()
        return [{**t, **client.status(t["project_id"], t["task_id"])} for t in tasks]

    def import_orthomosaic(
        self,
        farm: str,
        field: str,
        year: int,
        date: str,
        actor: str,
        task_id: str | None = None,
        source_path: str | Path | None = None,
        sensor: str = "mavic3m",
        client=None,
    ) -> dict:
        """Store a reflectance orthomosaic in L0 and add it to the field's imagery.

        Either download it from a completed WebODM task or copy a local file.
        """
        date = str(date)
        raw = self.year_dir(farm, field, year) / "raw" / "drone_ortho"
        dest = raw / f"{date}_orthophoto.tif"
        if dest.exists():
            raise StoreError(f"L0 is immutable: {dest.name} already exists")
        raw.mkdir(parents=True, exist_ok=True)
        if task_id:
            from .drone.webodm import WebODMClient

            path = self._tasks_file(farm, field, year)
            tasks = json.loads(path.read_text()) if path.exists() else []
            task = next((t for t in tasks if t["task_id"] == task_id), None)
            if task is None:
                raise StoreError(f"unknown WebODM task {task_id}")
            client = client or WebODMClient()
            st = client.status(task["project_id"], task_id)
            if st["status"] != "completed":
                raise StoreError(f"WebODM task {task_id} is {st['status']}, not completed")
            client.download(task["project_id"], task_id, "orthophoto.tif", dest)
            origin = f"webodm task {task_id}"
        elif source_path:
            src = Path(source_path)
            if not src.is_file():
                raise StoreError(f"orthomosaic not found: {source_path}")
            shutil.copy2(src, dest)
            origin = str(source_path)
        else:
            raise StoreError("give task_id or source_path")
        rel = f"raw/drone_ortho/{dest.name}"
        p = self.year_dir(farm, field, year) / "field.yaml"
        cfg = yaml.safe_load(p.read_text()) or {}
        imagery = cfg.setdefault("imagery", [])
        imagery.append({"kind": "drone", "path": rel, "date": date, "sensor": sensor})
        p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
        digest = _sha256(dest)
        self._audit(
            actor,
            "import_orthomosaic",
            f"{farm}/{field}/{year}",
            file=rel,
            origin=origin,
            sha256=digest,
        )
        return {"path": rel, "date": date, "sensor": sensor, "sha256": digest, "origin": origin}

    def boundary_from_parcel_ids(
        self,
        farm: str,
        field: str,
        year: int,
        actor: str,
        parcel_ids: list[str] | None = None,
        service=None,
    ) -> dict:
        """Fetch parcels by number from the parcel-register WFS and set the boundary."""
        import json as _json

        from .public.parcels import ParcelService, normalise_parcel_id

        cfg = self.load_config(farm, field, year)
        ids = [normalise_parcel_id(p) for p in (parcel_ids or cfg.parcel_ids)]
        if not ids:
            raise StoreError("give parcel_ids or set parcel_ids in field.yaml")
        pd_cfg = cfg.public_data
        service = service or ParcelService(pd_cfg.parcels_wfs_url)
        choice = service.choose(year, pd_cfg.parcels_typename, pd_cfg.parcel_id_column)
        fc = service.fetch(ids, choice)
        path = self.public_dir(farm, field, year) / f"parcels_{'_'.join(ids)}.geojson"
        path.write_text(_json.dumps(fc))
        result = self.import_boundary(farm, field, year, path, actor, ids, choice.id_column)
        self._set_config_key(farm, field, year, "parcel_ids", ids)
        result["source"] = {
            "wfs": service.url,
            "layer": choice.typename,
            "layer_year": choice.year,
            "id_column": choice.id_column,
            "choice": choice.reason,
        }
        return result

    def approve_qa(
        self, farm: str, field: str, year: int, decision: str, note: str, actor: str
    ) -> dict:
        if decision not in ("approved", "rejected", "needs_data"):
            raise StoreError("decision must be approved, rejected or needs_data")
        ydir = self.year_dir(farm, field, year)
        qa = ydir / "output" / f"L4_{field}_{year}_qa.json"
        entry = {
            "time": _now(),
            "actor": actor,
            "decision": decision,
            "note": note,
            "qa_sha256": _sha256(qa) if qa.exists() else None,
        }
        with open(ydir / "approvals.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._audit(actor, "approve_qa", f"{farm}/{field}/{year}", note, decision=decision)
        return entry

    def record_run(self, farm: str, field: str, year: int, actor: str, summary: dict) -> None:
        self._audit(actor, "run_chain", f"{farm}/{field}/{year}", **summary)

    def _set_config_key(self, farm: str, field: str, year: int, key: str, value) -> None:
        p = self.year_dir(farm, field, year) / "field.yaml"
        raw = yaml.safe_load(p.read_text()) or {}
        if raw.get(key) != value:
            raw[key] = value
            p.write_text(yaml.safe_dump(raw, sort_keys=False))


def _kind(name: str) -> str:
    if name.endswith("_qa.json"):
        return "qa_report"
    if name.endswith("_model_card.json"):
        return "model_card"
    if name.endswith("_s2_features.csv"):
        return "sentinel2_features"
    if "_records." in name:
        return "records"
    if name.endswith(".tif"):
        return "raster"
    return "file"


def _raster_summary(path: Path) -> dict:
    try:
        from .l4.io import read_bands
    except ImportError:  # pragma: no cover
        return {}
    try:
        bands, grid, crs = read_bands(path)
    except ImportError:
        return {"note": "install yieldsuite[geo] for raster summaries"}
    import numpy as np

    y = bands["yield"]
    finite = np.isfinite(y)
    return {
        "crs": crs,
        "grid": {
            "xmin": grid.xmin,
            "ymax": grid.ymax,
            "resolution": grid.resolution,
            "nx": grid.nx,
            "ny": grid.ny,
        },
        "bands": list(bands),
        "yield_t_ha": {
            "cells": int(finite.sum()),
            "mean": float(np.nanmean(y)) if finite.any() else None,
            "p10": float(np.nanpercentile(y, 10)) if finite.any() else None,
            "p90": float(np.nanpercentile(y, 90)) if finite.any() else None,
        },
    }


def init_demo_store(root: str | Path, farm: str = "demo-farm") -> Path:
    """Create a store with one synthetic field-year (see ``yieldsuite.synthetic``)."""
    from . import synthetic

    root = Path(root)
    ydir = root / farm / "synthetic-01" / "2026"
    (ydir / "raw" / "harvest_log").mkdir(parents=True, exist_ok=True)
    f = synthetic.make_field(0)
    log_path = ydir / "raw" / "harvest_log" / "combine-a_2026.csv"
    f.log.to_csv(log_path, index=False)
    tickets = ydir / "raw" / "scale_tickets.csv"
    with open(tickets, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["machine_id", "mass_kg", "moisture_pct", "ticket_ref", "added", "actor"])
        for i, row in enumerate(f.tickets.itertuples(), 1):
            w.writerow([row.machine_id, row.mass_kg, row.moisture_pct, f"demo-{i}", _now(), "demo"])
    manifest = [
        {
            "path": "harvest_log/combine-a_2026.csv",
            "sha256": _sha256(log_path),
            "size_bytes": log_path.stat().st_size,
            "uploaded": _now(),
            "actor": "demo",
        }
    ]
    (ydir / "raw" / "manifest.json").write_text(json.dumps(manifest, indent=2))

    cfg = yaml.safe_load(synthetic.write_demo(root / ".demo-tmp").read_text())
    shutil.rmtree(root / ".demo-tmp")
    machines = cfg.pop("machines")
    cfg["harvest_logs"][0]["path"] = "raw/harvest_log/combine-a_2026.csv"
    cfg["scale_tickets"] = "raw/scale_tickets.csv"
    cfg["output_dir"] = "output"
    cfg["notify"] = {"slack": {"channel": "#yield-data-suite"}}
    (ydir / "field.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    mdir = root / farm / "machines"
    mdir.mkdir(exist_ok=True)
    for m in machines:
        rec = {**m, "version": 1, "created": _now(), "actor": "demo", "reason": "initial profile"}
        (mdir / f"{m['machine_id']}.yaml").write_text(
            yaml.safe_dump({"versions": [rec]}, sort_keys=False)
        )
    return root
