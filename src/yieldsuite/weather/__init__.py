"""Field weather and agroclimate: ERA5 via Open-Meteo, PAR, GDD, RUE.

Weather is one value per field and day (ERA5 0.25°, ERA5-Land 0.1°), so it
does not explain variation inside a field. It explains the *year*: how much
light and warmth the crop had, when stages were reached, and how efficiently
the canopy turned intercepted light into yield (RUE). Sentinel-2 fAPAR adds
the within-field part, giving a per-cell RUE map.
"""

from .agro import (
    AgroSettings,
    clearness_index,
    extraterrestrial_radiation,
    growing_degree_days,
    par_from_shortwave,
    season_summary,
)
from .openmeteo import WeatherRequest, fetch_daily, parse_response
from .rue import fapar_from_ndvi, field_rue, rue_map

__all__ = [
    "AgroSettings",
    "WeatherRequest",
    "clearness_index",
    "extraterrestrial_radiation",
    "fapar_from_ndvi",
    "fetch_daily",
    "field_rue",
    "growing_degree_days",
    "par_from_shortwave",
    "parse_response",
    "rue_map",
    "season_summary",
]
