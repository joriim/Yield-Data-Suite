"""Field-year configuration (one YAML file per field and harvest year)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .levels import ProductClass, classify_product
from .registry import MachineProfile

DEFAULT_SLACK_CHANNEL = "#yield-data-suite"


@dataclass
class HarvestLog:
    """One yield/machine data file. Most fields are optional: ``yieldsuite inspect``
    detects format, columns and units and prints what to put here to confirm them."""

    path: Path
    machine_id: str = "machine-1"  # used when the file has no machine column
    format: str = "auto"  # auto | table | excel | vector | isoxml | agleader_advanced
    # canonical name -> column in the file (time, date, x, y, lon, lat, mass_flow,
    # dry_mass_flow, yield_wet, yield_dry, yield_volume, moisture, swath, speed,
    # distance, duration, heading, header, gnss_quality, pass_id, machine, ...)
    columns: dict[str, str] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)  # canonical name -> unit token
    entry_level: str | None = None  # auto: L1B for mass flow, L2 for yield points
    position_crs: str | None = None  # CRS of x/y if not the field CRS
    crop: str | None = None  # needed for bu/ac yields
    bushel_weight_lb: float | None = None
    vendor_reference_moisture_pct: float | None = None  # moisture of dry/standard yields
    default_swath_m: float | None = None  # when the file has no width column
    layer: str | None = None  # GeoPackage layer
    sheet: str | int | None = None  # Excel sheet
    delimiter: str | None = None
    decimal: str | None = None
    ddi_map: dict[int, list[str]] = field(default_factory=dict)  # ISOXML overrides
    # legacy shortcuts, merged into ``units``
    mass_flow_unit: str | None = None
    speed_unit: str | None = None
    heading_unit: str | None = None

    def unit_overrides(self) -> dict[str, str]:
        legacy = {
            "mass_flow": self.mass_flow_unit,
            "speed": self.speed_unit,
            "heading": self.heading_unit,
        }
        return {**{k: v for k, v in legacy.items() if v}, **self.units}


@dataclass
class ImagerySource:
    kind: str  # sentinel2 | drone | planetscope
    path: Path | None = None  # local reflectance raster (drone orthomosaic, PlanetScope SR)
    date: str | None = None  # acquisition date of a local raster
    # local rasters: sensor preset (mavic3m | superdove | dove4), overrides, cloud mask
    sensor: str | None = None
    bands: dict[str, int] = field(default_factory=dict)  # green/red/rededge/nir -> band no.
    scale: float | None = None  # reflectance = DN x scale
    mask: Path | None = None  # e.g. PlanetScope UDM2; band ``mask_clear_band`` > 0 = clear
    mask_clear_band: int = 1
    # Sentinel-2 via openEO
    start: str | None = None  # ISO date; default <year>-04-15
    end: str | None = None  # ISO date, exclusive; default <year>-09-15
    indices: tuple[str, ...] | None = None
    max_cloud_cover: float = 80.0
    min_valid_share: float = 0.8  # drop dates where fewer cells are cloud-free
    backend: str = "openeo.dataspace.copernicus.eu"


@dataclass
class SlackSettings:
    channel: str = DEFAULT_SLACK_CHANNEL
    webhook_url_env: str = "YIELDSUITE_SLACK_WEBHOOK_URL"
    bot_token_env: str = "YIELDSUITE_SLACK_BOT_TOKEN"
    viewer_base_url: str | None = None


@dataclass
class CropSettings:
    name: str = "spring_cereal"
    sowing_date: str | None = None  # ISO date
    harvest_date: str | None = None  # ISO date; default: last harvesting record
    t_base_c: float = 5.0
    t_cap_c: float | None = None
    harvest_index: float = 0.45
    par_fraction: float = 0.48
    stages_gdd: dict[str, float] = field(default_factory=dict)  # e.g. {heading: 700}
    ptq_window: list[str] | None = None  # two stage names


@dataclass
class WeatherConfig:
    source: str = "open-meteo"
    model: str = "era5_seamless"  # era5 | era5_land | era5_seamless | best_match
    hourly: list[str] = field(default_factory=list)  # e.g. soil_moisture_0_to_7cm
    round_deg: float = 0.05  # location sent to the API is rounded to this


@dataclass
class PublicDataConfig:
    """Endpoints of public services (verify them; they change more often than code)."""

    stac_url: str = "https://stac.dataspace.copernicus.eu/v1"
    s2_collection: str = "sentinel-2-l2a"
    # Finnish Food Authority INSPIRE WFS (parcel register, one layer per year)
    parcels_wfs_url: str | None = "https://inspire.ruokavirasto-awsa.com/geoserver/wfs"
    parcels_typename: str | None = None  # default: discovered from the service by year
    parcel_id_column: str | None = None  # default: discovered (e.g. PERUSLOHKOTUNNUS)


@dataclass
class QualitySource:
    """One grain-quality analyzer export (e.g. a GrainSense Flow session export)."""

    path: Path
    machine_id: str = "machine-1"  # the combine carrying the analyzer
    format: str = "auto"
    columns: dict[str, str] = field(default_factory=dict)  # time, protein, moisture, oil ...
    sheet: str | int | None = None
    delay_s: float | None = None  # analyzer delay; default: the machine's tau_d
    max_gap_s: float | None = None  # default: 0.75 x measurement interval + 1 s


@dataclass
class QualityConfig:
    sources: list[QualitySource] = field(default_factory=list)
    protein_basis: str = "dry"  # basis of the analyzer's protein %: dry | wet
    n_factor: float = 6.25  # protein -> nitrogen (wheat 5.7)
    lab_samples: Path | None = None  # CSV: protein_lab_pct + start/end or time


@dataclass
class FieldYearConfig:
    field_id: str
    year: int
    boundary: Path | None = None
    crs: str = "EPSG:3067"
    grid_resolution_m: float = 20.0
    grid_origin: tuple[float, float] = (0.0, 0.0)
    reference_moisture_pct: float = 14.0
    harvest_logs: list[HarvestLog] = field(default_factory=list)
    scale_tickets: Path | None = None
    imagery: list[ImagerySource] = field(default_factory=list)
    machines: dict[str, MachineProfile] = field(default_factory=dict)
    output_dir: Path = Path("output")
    slack: SlackSettings | None = None
    crop: CropSettings | None = None
    location: tuple[float, float] | None = None  # (lat, lon) WGS84; else from the grid
    weather: WeatherConfig = field(default_factory=WeatherConfig)
    public_data: PublicDataConfig = field(default_factory=PublicDataConfig)
    edge_buffer_m: float | None = None  # FIELD_EDGE buffer; default: one header width
    local_outlier_radius_m: float = 20.0  # neighbourhood of the local Moran's I flag
    quality: QualityConfig | None = None
    parcel_ids: list[str] = field(default_factory=list)  # parcel numbers of this field

    @property
    def sentinel2(self) -> ImagerySource | None:
        return next((i for i in self.imagery if i.kind == "sentinel2"), None)

    @property
    def product_class(self) -> ProductClass:
        return classify_product(
            has_harvester=bool(self.harvest_logs),
            has_scale_weights=self.scale_tickets is not None,
            has_imagery=bool(self.imagery),
        )

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the config is usable."""
        problems: list[str] = []
        if self.grid_resolution_m <= 0:
            problems.append("grid_resolution_m must be positive")
        if not 0 <= self.reference_moisture_pct < 100:
            problems.append("reference_moisture_pct must be in [0, 100)")
        if not self.harvest_logs and not self.imagery:
            problems.append("need at least one harvest log or imagery source")
        for img in self.imagery:
            if img.kind not in ("sentinel2", "drone", "planetscope"):
                problems.append(f"unknown imagery kind {img.kind!r}")
            elif img.kind != "sentinel2" and (img.path is None or img.date is None):
                problems.append(f"{img.kind} imagery needs path and date")
        # A machine without a profile runs on defaults; the run's QA lists a warning.
        return problems


def _path(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


def load_config(path: str | Path) -> FieldYearConfig:
    path = Path(path)
    base = path.parent
    raw = yaml.safe_load(path.read_text()) or {}

    machines = {m["machine_id"]: MachineProfile.from_dict(m) for m in raw.get("machines", [])}
    logs = []
    for h in raw.get("harvest_logs", []):
        h = dict(h)
        h["path"] = _path(base, h["path"])
        h["columns"] = dict(h.get("columns") or {})
        h["units"] = dict(h.get("units") or {})
        logs.append(HarvestLog(**h))
    imagery = []
    for i in raw.get("imagery", []):
        i = dict(i)
        i["path"] = _path(base, i.get("path"))
        i["mask"] = _path(base, i.get("mask"))
        i["bands"] = dict(i.get("bands") or {})
        for key in ("date", "start", "end"):
            if i.get(key) is not None:
                i[key] = str(i[key])
        if i.get("indices") is not None:
            i["indices"] = tuple(i["indices"])
        imagery.append(ImagerySource(**i))
    slack_raw = (raw.get("notify") or {}).get("slack")
    slack = (
        SlackSettings(**slack_raw)
        if isinstance(slack_raw, dict)
        else (SlackSettings() if slack_raw else None)
    )
    grid = raw.get("grid", {})
    crop = None
    if raw.get("crop"):
        c = dict(raw["crop"])
        for key in ("sowing_date", "harvest_date"):
            if c.get(key) is not None:
                c[key] = str(c[key])
        crop = CropSettings(**c)
    loc = raw.get("location")
    if isinstance(loc, dict):
        loc = (float(loc["lat"]), float(loc["lon"]))
    elif loc is not None:
        loc = (float(loc[0]), float(loc[1]))
    return FieldYearConfig(
        field_id=str(raw["field_id"]),
        year=int(raw["year"]),
        boundary=_path(base, raw.get("boundary")),
        crs=raw.get("crs", "EPSG:3067"),
        grid_resolution_m=float(grid.get("resolution_m", 20.0)),
        grid_origin=tuple(float(v) for v in grid.get("origin", (0.0, 0.0))),
        reference_moisture_pct=float(raw.get("reference_moisture_pct", 14.0)),
        harvest_logs=logs,
        scale_tickets=_path(base, raw.get("scale_tickets")),
        imagery=imagery,
        machines=machines,
        output_dir=_path(base, raw.get("output_dir", "output")),
        slack=slack,
        crop=crop,
        location=loc,
        weather=WeatherConfig(**(raw.get("weather") or {})),
        public_data=PublicDataConfig(**(raw.get("public_data") or {})),
        edge_buffer_m=raw.get("edge_buffer_m"),
        local_outlier_radius_m=float(raw.get("local_outlier_radius_m", 20.0)),
        quality=_quality(raw.get("quality"), base),
        parcel_ids=[str(v) for v in raw.get("parcel_ids") or []],
    )


def _quality(raw: dict | None, base: Path) -> QualityConfig | None:
    if not raw:
        return None
    sources = []
    for src in raw.get("sources", []):
        src = dict(src)
        src["path"] = _path(base, src["path"])
        src["columns"] = dict(src.get("columns") or {})
        sources.append(QualitySource(**src))
    return QualityConfig(
        sources=sources,
        protein_basis=raw.get("protein_basis", "dry"),
        n_factor=float(raw.get("n_factor", 6.25)),
        lab_samples=_path(base, raw.get("lab_samples")),
    )
