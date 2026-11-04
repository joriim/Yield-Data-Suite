"""Column recognition: map arbitrary export headers to canonical fields.

Synonyms cover English, Finnish, German and the field names of common
yield-monitor exports. Extend ``SYNONYMS`` (or map explicitly in the config)
when a new source appears; the inspection report shows what was matched.
"""

from __future__ import annotations

import re

SYNONYMS: dict[str, list[str]] = {
    "time": [
        "time",
        "measuredat",
        "mittausaika",
        "timestamp",
        "datetime",
        "isotime",
        "gpstime",
        "utctime",
        "logtime",
        "aika",
        "aikaleima",
        "zeit",
        "zeitstempel",
        "kellonaika",
        "uhrzeit",
    ],
    "date": ["date", "pvm", "paivamaara", "datum", "day", "gpsdate"],
    "lon": ["lon", "long", "lng", "longitude", "pituusaste", "laenge", "laengengrad", "lon84"],
    "lat": ["lat", "latitude", "leveysaste", "breite", "breitengrad", "lat84"],
    "x": ["x", "easting", "east", "ita", "rechtswert", "xcoord", "etrsx"],
    "y": ["y", "northing", "north", "pohjoinen", "hochwert", "ycoord", "etrsy"],
    "mass_flow": [
        "massflow",
        "flow",
        "cropflow",
        "cropflwm",
        "grainflow",
        "wetmassflow",
        "massflowwet",
        "massavirta",
        "massenstrom",
        "yieldflow",
        "flowrate",
    ],
    "dry_mass_flow": ["drymassflow", "dryflow", "massflowdry"],
    "yield_wet": [
        "yieldwet",
        "wetyield",
        "yldmassw",
        "yldmasswet",
        "wetmass",
        "satokostea",
        "kosteasato",
        "ertragfeucht",
        "yieldmasswet",
    ],
    "yield_dry": [
        "yield",
        "sato",
        "yldmassd",
        "yldmassdry",
        "vryieldmas",
        "yieldmass",
        "drymassyield",
        "yielddry",
        "dryyield",
        "ertrag",
        "ertragtrocken",
        "satokuiva",
        "kuivasato",
        "yldmass",
    ],
    "yield_volume": [
        "yldvoldr",
        "yldvoldry",
        "vryieldvol",
        "yieldvolume",
        "volumeyield",
        "yieldbu",
        "yieldbuac",
        "yldvol",
    ],
    "moisture": [
        "moisture",
        "moist",
        "kosteus",
        "grainmoisture",
        "feuchte",
        "feuchtigkeit",
        "harvestmoisture",
        "mc",
        "h2o",
    ],
    "swath": [
        "swath",
        "swathwidth",
        "swthwdth",
        "width",
        "headerwidth",
        "workingwidth",
        "workwidth",
        "tyoleveys",
        "leikkuuleveys",
        "arbeitsbreite",
        "actualwidth",
    ],
    "speed": [
        "speed",
        "vehiclespeed",
        "vehiclspeed",
        "groundspeed",
        "nopeus",
        "ajonopeus",
        "geschwindigkeit",
        "velocity",
    ],
    "distance": ["distance", "dist", "distancef", "matka", "strecke", "weg"],
    "duration": ["duration", "durations", "interval", "kesto", "dauer", "elapsed"],
    "heading": [
        "heading",
        "track",
        "trackdeg",
        "course",
        "direction",
        "suunta",
        "kurs",
        "bearing",
        "azimuth",
    ],
    "header": [
        "header",
        "headerstatus",
        "headerup",
        "workstate",
        "worksstatus",
        "cutting",
        "harvesting",
        "leikkuupoyta",
        "schneidwerk",
        "recording",
        "logging",
    ],
    "gnss_quality": [
        "gpsstatus",
        "gnssquality",
        "fix",
        "gpsfix",
        "fixquality",
        "rtk",
        "diffstatus",
        "gnssstatus",
        "quality",
        "gpsquality",
        "differential",
    ],
    "pass_id": ["pass", "passnum", "passnumber", "passid", "ajokaista", "lane"],
    "elevation": ["elevation", "altitude", "elev", "korkeus", "hoehe", "height"],
    "machine": [
        "machine",
        "machineid",
        "vehicle",
        "vehicleid",
        "serial",
        "serialnumber",
        "kone",
        "combine",
        "puimuri",
        "maschine",
        "logger",
    ],
    "crop": ["crop", "product", "grain", "graintype", "kasvi", "lajike", "frucht"],
}

_UNIT_TAIL = re.compile(
    r"(lbs?|kg|t|g|mg|bu|dt|dz|ac|ha|s|sec|h|min|ms|m|mm|cm|ft|in|mph|kmh|kph|ppm|pct|"
    r"deg|rad|percent|per|wet|dry)$"
)


def normalise_header(h: str) -> str:
    """Lowercase, drop bracketed units, Nordic letters to ASCII, keep letters/digits."""
    s = str(h).lower()
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", "", s)
    s = s.translate(str.maketrans({"ä": "a", "ö": "o", "å": "a", "ü": "u", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]", "", s)


def _strip_unit_tail(s: str) -> str:
    prev = None
    while prev != s and len(s) > 3:
        prev = s
        s = _UNIT_TAIL.sub("", s) if len(_UNIT_TAIL.sub("", s)) >= 3 else s
    return s


def match_columns(headers, explicit: dict[str, str] | None = None) -> dict[str, str]:
    """Return {canonical: header}. Explicit mappings win; each header is used once."""
    explicit = dict(explicit or {})
    out: dict[str, str] = {}
    used: set[str] = set()
    for canonical, header in explicit.items():
        if header in headers:
            out[canonical] = header
            used.add(header)
    norm = {h: normalise_header(h) for h in headers}
    stripped = {h: _strip_unit_tail(n) for h, n in norm.items()}

    # Pass 1: exact synonym (with or without unit tail); pass 2: synonym prefix.
    for pass_no in (1, 2):
        for canonical, syns in SYNONYMS.items():
            if canonical in out:
                continue
            for syn in syns:
                hit = None
                for h in headers:
                    if h in used:
                        continue
                    n, st = norm[h], stripped[h]
                    if pass_no == 1 and (n == syn or st == syn):
                        hit = h
                    elif (
                        pass_no == 2 and len(syn) >= 4 and (n.startswith(syn) or st.startswith(syn))
                    ):
                        hit = h
                    if hit:
                        break
                if hit:
                    out[canonical] = hit
                    used.add(hit)
                    break
    # "yield" alone is ambiguous between wet and dry; treat a lone yield as dry/standard.
    return out
