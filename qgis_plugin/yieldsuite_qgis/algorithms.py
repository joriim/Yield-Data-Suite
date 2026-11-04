# SPDX-License-Identifier: GPL-2.0-or-later
"""Processing algorithms: thin wrappers around yieldsuite.tasks.

Every algorithm passes plain values to the core and loads the files it writes,
so results are identical to the command line (`yieldsuite ...`).
"""

from pathlib import Path

from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingContext,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingOutputFolder,
    QgsProcessingOutputRasterLayer,
    QgsProcessingOutputString,
    QgsProcessingOutputVectorLayer,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterCrs,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
)

INSTALL_HINT = (
    "The yieldsuite library is not installed in QGIS's Python. Install it from the "
    "OSGeo4W shell (Windows) or a terminal: python -m pip install yieldsuite[geo]"
)


def _tasks():
    try:
        from yieldsuite import tasks
    except ImportError as exc:
        raise QgsProcessingException(INSTALL_HINT) from exc
    return tasks


def _load(context, path, name):
    if path and Path(path).exists():
        context.addLayerToLoadOnCompletion(
            str(path), QgsProcessingContext.LayerDetails(name, context.project(), name)
        )


class _Base(QgsProcessingAlgorithm):
    NAME = ""
    TITLE = ""
    GROUP = "Yield maps"
    GROUP_ID = "yieldmaps"
    HELP = ""

    def name(self):
        return self.NAME

    def displayName(self):  # noqa: N802
        return self.TITLE

    def group(self):
        return self.GROUP

    def groupId(self):  # noqa: N802
        return self.GROUP_ID

    def shortHelpString(self):  # noqa: N802
        return self.HELP

    def createInstance(self):  # noqa: N802
        return type(self)()

    def _call(self, fn, feedback, *args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except QgsProcessingException:
            raise
        except Exception as exc:  # noqa: BLE001 - shown to the user
            raise QgsProcessingException(f"{type(exc).__name__}: {exc}") from exc
        for line in result.get("message", "").splitlines():
            feedback.pushInfo(line)
        return result


def _config_param(name="CONFIG"):
    return QgsProcessingParameterFile(
        name, "Field-year config (field.yaml)", extension="yaml"
    )


class InspectFileAlgorithm(_Base):
    NAME, TITLE, GROUP, GROUP_ID = "inspect_file", "Inspect yield/machine data file", \
        "Data", "data"
    HELP = (
        "Detects format (CSV/TXT, Excel, Shapefile/GeoPackage/GeoJSON, ISOXML TaskData, "
        "Ag Leader), columns and units, and reports where each unit came from. Writes the "
        "harvest_logs entry for field.yaml. Ambiguous units stop with a message instead of "
        "guessing."
    )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterFile("INPUT", "Data file or TaskData zip"))
        self.addParameter(QgsProcessingParameterCrs("CRS", "Field CRS", "EPSG:3067"))
        self.addParameter(QgsProcessingParameterString("CROP", "Crop (needed for bu/ac)",
                                                       optional=True))
        self.addParameter(QgsProcessingParameterString("MACHINE", "Machine id", "machine-1"))
        self.addParameter(QgsProcessingParameterNumber(
            "SWATH", "Header width [m] if the file has none",
            QgsProcessingParameterNumber.Double, optional=True, minValue=0.5))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT_YAML", "Config entry", "YAML files (*.yaml)", optional=True))
        self.addOutput(QgsProcessingOutputString("REPORT", "Report"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        swath = parameters.get("SWATH")
        res = self._call(
            _tasks().inspect, feedback,
            self.parameterAsFile(parameters, "INPUT", context),
            self.parameterAsCrs(parameters, "CRS", context).authid(),
            crop=self.parameterAsString(parameters, "CROP", context) or None,
            machine_id=self.parameterAsString(parameters, "MACHINE", context) or "machine-1",
            default_swath_m=self.parameterAsDouble(parameters, "SWATH", context)
            if swath not in (None, "") else None,
        )
        out = self.parameterAsFileOutput(parameters, "OUTPUT_YAML", context)
        if out:
            Path(out).write_text(res["config_yaml"], encoding="utf-8")
        return {"REPORT": res["message"], "OUTPUT_YAML": out}


class RunChainAlgorithm(_Base):
    NAME, TITLE = "run_chain", "Run yield chain (L1-L4 / L5)"
    HELP = (
        "Runs the processing chain for one field-year: delay, antenna offset, moisture, "
        "effective area, quality flags, reconciliation to scale tickets, mass-conserving "
        "grid (L4) and, with Sentinel-2 configured, fusion (L5). Loads the products."
    )
    LEVELS = ["L4", "L5"]

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(_config_param())
        self.addParameter(QgsProcessingParameterEnum("TO_LEVEL", "Up to level", self.LEVELS,
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterBoolean("LOAD", "Load results", True))
        self.addOutput(QgsProcessingOutputRasterLayer("L4", "L4 yield grid"))
        self.addOutput(QgsProcessingOutputRasterLayer("L5", "L5 fused map"))
        self.addOutput(QgsProcessingOutputRasterLayer("Q4", "Q4 grain quality"))
        self.addOutput(QgsProcessingOutputVectorLayer("L3", "L3 records"))
        self.addOutput(QgsProcessingOutputFile("QA", "QA report"))
        self.addOutput(QgsProcessingOutputString("MESSAGE", "Summary"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        level = self.LEVELS[self.parameterAsEnum(parameters, "TO_LEVEL", context)]
        res = self._call(_tasks().run_chain, feedback,
                         self.parameterAsFile(parameters, "CONFIG", context), level)
        if not res["ok"]:
            raise QgsProcessingException(res["message"])
        out = res["outputs"]
        if self.parameterAsBool(parameters, "LOAD", context):
            for key, name in (("L3", "L3 records"), ("L4", "L4 yield"), ("L5", "L5 yield"),
                              ("Q4", "Q4 protein")):
                _load(context, out.get(key), name)
        return {"L4": out.get("L4"), "L5": out.get("L5"), "Q4": out.get("Q4"),
                "L3": out.get("L3"), "QA": out.get("qa"), "MESSAGE": res["message"]}


class EstimateDelayAlgorithm(_Base):
    NAME, TITLE = "estimate_delay", "Estimate grain-flow delay"
    HELP = (
        "Estimates each machine's flow delay from adjacent passes driven in opposite "
        "directions and compares it with the machine profile. Nothing is written."
    )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(_config_param())
        self.addOutput(QgsProcessingOutputString("MESSAGE", "Summary"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        res = self._call(_tasks().estimate_delay, feedback,
                         self.parameterAsFile(parameters, "CONFIG", context))
        return {"MESSAGE": res["message"]}


class FuseAlgorithm(_Base):
    NAME, TITLE = "fuse_sentinel2", "Fuse L4 with Sentinel-2 (L5)"
    HELP = (
        "Image model and regression-kriging gap fill on an L4 raster with Sentinel-2 "
        "features from openEO (or a cached feature table). Needs a Copernicus Data Space "
        "account on the first run."
    )
    METHODS = ["linear", "rf"]

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(_config_param())
        self.addParameter(QgsProcessingParameterRasterLayer("L4", "L4 yield raster"))
        self.addParameter(QgsProcessingParameterEnum("METHOD", "Model", self.METHODS,
                                                     defaultValue=0))
        self.addParameter(QgsProcessingParameterFile(
            "FEATURES", "Cached Sentinel-2 features (CSV)", extension="csv", optional=True))
        self.addOutput(QgsProcessingOutputRasterLayer("L5", "L5 fused map"))
        self.addOutput(QgsProcessingOutputString("MESSAGE", "Summary"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        l4 = self.parameterAsRasterLayer(parameters, "L4", context)
        res = self._call(
            _tasks().fuse, feedback, self.parameterAsFile(parameters, "CONFIG", context),
            l4.source(), self.METHODS[self.parameterAsEnum(parameters, "METHOD", context)],
            features_path=self.parameterAsFile(parameters, "FEATURES", context) or None,
        )
        _load(context, res["outputs"]["L5"], "L5 yield")
        return {"L5": res["outputs"]["L5"], "MESSAGE": res["message"]}


class ZonesAlgorithm(_Base):
    NAME, TITLE = "zones", "Management zones from multi-year maps (KM-sPC)"
    HELP = (
        "Relative yield per year (+ optional covariates such as elevation or ECa), "
        "MULTISPATI-PCA and fuzzy k-means; the number of zones is chosen by FPI and NCE. "
        "All rasters must share the grid origin and cell size."
    )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "LAYERS", "Yield maps, one per year (L4/L5)", QgsProcessing.TypeRaster))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            "COVARIATES", "Covariates", QgsProcessing.TypeRaster, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            "K_MAX", "Maximum number of zones", QgsProcessingParameterNumber.Integer, 6,
            minValue=2, maxValue=12))
        self.addParameter(QgsProcessingParameterFolderDestination("OUTPUT", "Output folder"))
        self.addOutput(QgsProcessingOutputRasterLayer("ZONES_RASTER", "Zones raster"))
        self.addOutput(QgsProcessingOutputVectorLayer("ZONES", "Zone polygons"))
        self.addOutput(QgsProcessingOutputFolder("FOLDER", "Folder"))
        self.addOutput(QgsProcessingOutputString("MESSAGE", "Summary"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        layers = [lyr.source() for lyr in self.parameterAsLayerList(parameters, "LAYERS",
                                                                    context)]
        covs = [lyr.source() for lyr in self.parameterAsLayerList(parameters, "COVARIATES",
                                                                  context) or []]
        folder = self.parameterAsString(parameters, "OUTPUT", context)
        res = self._call(_tasks().zones, feedback, layers, folder, covs,
                         self.parameterAsInt(parameters, "K_MAX", context))
        out = res["outputs"]
        _load(context, out.get("raster"), "Zones")
        _load(context, out.get("polygons"), "Zone polygons")
        return {"ZONES_RASTER": out.get("raster"), "ZONES": out.get("polygons"),
                "FOLDER": folder, "MESSAGE": res["message"]}


class WeatherAlgorithm(_Base):
    NAME, TITLE = "weather", "Field weather and agroclimate (ERA5)"
    HELP = (
        "ERA5 via Open-Meteo for the field: cloud-aware PAR, growing degree days, stage "
        "dates, water balance, heat and frost days. Only a location rounded to ~5 km is "
        "sent. Uses the config's location, else the L4 raster's centre."
    )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(_config_param())
        self.addParameter(QgsProcessingParameterRasterLayer("L4", "L4 raster (to locate the "
                                                            "field)", optional=True))
        self.addOutput(QgsProcessingOutputFile("SUMMARY", "Agroclimate summary"))
        self.addOutput(QgsProcessingOutputString("MESSAGE", "Summary"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        l4 = self.parameterAsRasterLayer(parameters, "L4", context)
        res = self._call(_tasks().weather, feedback,
                         self.parameterAsFile(parameters, "CONFIG", context),
                         l4.source() if l4 else None)
        return {"SUMMARY": res["outputs"].get("agroclimate"), "MESSAGE": res["message"]}


class ParcelBoundaryAlgorithm(_Base):
    NAME, TITLE, GROUP, GROUP_ID = "parcel_boundary", "Field boundary by parcel number", \
        "Data", "data"
    HELP = (
        "Fetches field parcels by number (peruslohkotunnus) from the parcel-register WFS "
        "(default: Finnish Food Authority INSPIRE WFS, the layer of the given year). The "
        "layer and id attribute are discovered from the service and reported. Use the result "
        "as `boundary:` in field.yaml."
    )

    def initAlgorithm(self, config=None):  # noqa: N802
        self.addParameter(QgsProcessingParameterString(
            "IDS", "Parcel numbers (comma separated)"))
        self.addParameter(QgsProcessingParameterNumber(
            "YEAR", "Register year", QgsProcessingParameterNumber.Integer, optional=True,
            minValue=2000, maxValue=2100))
        self.addParameter(QgsProcessingParameterString("URL", "WFS URL (empty = Ruokavirasto)",
                                                       optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            "OUTPUT", "Boundary", "GeoPackage (*.gpkg)"))
        self.addOutput(QgsProcessingOutputVectorLayer("BOUNDARY", "Boundary"))
        self.addOutput(QgsProcessingOutputString("MESSAGE", "Summary"))

    def processAlgorithm(self, parameters, context, feedback):  # noqa: N802
        ids = [v.strip() for v in self.parameterAsString(parameters, "IDS", context).split(",")
               if v.strip()]
        year = parameters.get("YEAR")
        out = self.parameterAsFileOutput(parameters, "OUTPUT", context)
        res = self._call(
            _tasks().parcel_boundary, feedback, ids, out,
            year=self.parameterAsInt(parameters, "YEAR", context) if year not in (None, "")
            else None,
            url=self.parameterAsString(parameters, "URL", context) or None,
        )
        _load(context, out, "Field boundary")
        return {"BOUNDARY": out, "OUTPUT": out, "MESSAGE": res["message"]}


ALGORITHMS = [InspectFileAlgorithm, ParcelBoundaryAlgorithm, RunChainAlgorithm, EstimateDelayAlgorithm, FuseAlgorithm,
              ZonesAlgorithm, WeatherAlgorithm]
