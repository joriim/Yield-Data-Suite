"""ISO 11783-10 (ISOXML) TaskData TimeLog reader.

Reads TASKDATA.XML, finds the TimeLogs (TLG) of every task and decodes the
binary TLGxxxxx.BIN files described by their TLGxxxxx.XML headers:

    record = TimeStart (uint32 ms since midnight, uint16 days since 1980-01-01)
             + the position fields present in <PTN> (north/east int32 1e-7 deg,
               up int32 mm, status uint8, PDOP/HDOP uint16, satellites uint8,
               GPS time uint32, GPS date uint16)
             + uint8 count + count x (uint8 DLV index, int32 value)

DLV values are logged only when they change, so they are forward-filled.

Status: experimental, tested on synthetic files written to the same layout.
Check the first real TaskData against the vendor software's totals. DDIs and
their scaling are in ``DDI_MAP`` and can be overridden per harvest log.
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

EPOCH_1980 = datetime(1980, 1, 1, tzinfo=timezone.utc)

# DDI (decimal) -> (canonical column, unit token for ingest.units)
DDI_MAP: dict[int, tuple[str, str]] = {
    87: ("mass_flow", "mg/s"),  # mass per time yield, wet
    182: ("dry_mass_flow", "mg/s"),  # dry mass per time yield at reference moisture
    99: ("moisture", "ppm"),  # harvest moisture
    67: ("swath", "mm"),  # actual working width
    397: ("speed", "mm/s"),  # actual speed
    141: ("header", ""),  # actual work state (1 = on)
}

_PTN_FIELDS = [  # attribute, struct code, column, scale
    ("A", "i", "lat", 1e-7),
    ("B", "i", "lon", 1e-7),
    ("C", "i", "elevation", 1e-3),
    ("D", "B", "gnss_status", 1),
    ("E", "H", "pdop", 0.1),
    ("F", "H", "hdop", 0.1),
    ("G", "B", "satellites", 1),
    ("H", "I", "gps_time_ms", 1),
    ("I", "H", "gps_date", 1),
]
# ISOXML PositionStatus -> NMEA-like quality (4 = RTK fixed, 5 = RTK float)
_STATUS = {0: 0, 1: 1, 2: 2, 3: 2, 4: 4, 5: 5, 6: 0, 7: 0, 8: 0}


class _Source:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.zip = None
        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self.zip = zipfile.ZipFile(self.path)
            names = {n.upper().rsplit("/", 1)[-1]: n for n in self.zip.namelist()}
            self.names = names
        else:
            base = self.path if self.path.is_dir() else self.path.parent
            self.names = {p.name.upper(): str(p) for p in base.iterdir() if p.is_file()}

    def read(self, name: str) -> bytes:
        key = name.upper()
        if key not in self.names:
            raise FileNotFoundError(f"ISOXML file {name} not found in {self.path}")
        if self.zip:
            return self.zip.read(self.names[key])
        return Path(self.names[key]).read_bytes()


def is_isoxml(path: Path) -> bool:
    path = Path(path)
    if path.is_dir():
        return any(p.name.upper() == "TASKDATA.XML" for p in path.iterdir())
    if path.name.upper() == "TASKDATA.XML":
        return True
    if path.suffix.lower() == ".zip" and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return any(n.upper().endswith("TASKDATA.XML") for n in z.namelist())
    return False


def _decode_timelog(header: bytes, binary: bytes) -> pd.DataFrame:
    root = ET.fromstring(header)
    tim = root if root.tag == "TIM" else root.find("TIM")
    ptn = tim.find("PTN")
    present = [
        (a, code, col, scale)
        for a, code, col, scale in _PTN_FIELDS
        if ptn is not None and ptn.get(a) == ""
    ]
    constants = {
        col: float(ptn.get(a)) * scale
        for a, _, col, scale in _PTN_FIELDS
        if ptn is not None and ptn.get(a) not in (None, "")
    }
    dlvs = [int(d.get("A"), 16) for d in tim.findall("DLV")]

    rows = []
    pos = 0
    n = len(binary)
    while pos + 6 <= n:
        ms, days = struct.unpack_from("<IH", binary, pos)
        pos += 6
        rec = {"t": (EPOCH_1980 + timedelta(days=days, milliseconds=ms)).timestamp()}
        for _, code, col, scale in present:
            (val,) = struct.unpack_from("<" + code, binary, pos)
            pos += struct.calcsize(code)
            rec[col] = val * scale
        (count,) = struct.unpack_from("<B", binary, pos)
        pos += 1
        for _ in range(count):
            idx, val = struct.unpack_from("<Bi", binary, pos)
            pos += 5
            if idx < len(dlvs):
                rec.setdefault(f"DDI_{dlvs[idx]}", val)
        rows.append(rec)
    if pos != n:
        raise ValueError(f"TimeLog binary has {n - pos} trailing bytes; header does not match")
    df = pd.DataFrame(rows)
    for col, val in constants.items():
        df[col] = val
    ddi_cols = [c for c in df.columns if c.startswith("DDI_")]
    df[ddi_cols] = df[ddi_cols].ffill()  # values are logged on change only
    return df


def read_isoxml(path: str | Path, ddi_map: dict[int, tuple[str, str]] | None = None):
    """Return (frame with canonical columns, preset units) for all TimeLogs."""
    src = _Source(Path(path))
    task = ET.fromstring(src.read("TASKDATA.XML"))
    tlgs = [t.get("A") for t in task.iter("TLG") if t.get("A")]
    if not tlgs:
        raise ValueError("TASKDATA.XML contains no TimeLog (TLG) references")
    frames = []
    for name in tlgs:
        df = _decode_timelog(src.read(f"{name}.XML"), src.read(f"{name}.BIN"))
        df["task_log"] = name
        frames.append(df)
    raw = pd.concat(frames, ignore_index=True).sort_values("t", ignore_index=True)

    mapping = {**DDI_MAP, **(ddi_map or {})}
    out = pd.DataFrame({"time": raw["t"], "lat": raw.get("lat"), "lon": raw.get("lon")})
    units: dict[str, str] = {}
    for ddi, (col, unit) in mapping.items():
        key = f"DDI_{ddi}"
        if key in raw:
            out[col] = raw[key]
            if unit:
                units[col] = unit
    if "header" in out:
        out["header"] = (out["header"].fillna(0) > 0).astype(int)
    if "gnss_status" in raw:
        out["gnss_quality"] = raw["gnss_status"].map(_STATUS).astype(float)
    if "elevation" in raw:
        out["elevation"] = raw["elevation"]
    return out, units


def write_timelog(df: pd.DataFrame, ddis: list[int]) -> tuple[bytes, bytes]:
    """Write a TimeLog header and binary (used by tests and for exporting).

    ``df`` needs columns t (s since epoch), lat, lon, status and DDI_<n>.
    Values are written only when they change, as real terminals do.
    """
    header = (
        '<TIM A="" D="4"><PTN A="" B="" D=""/>'
        + "".join(f'<DLV A="{d:04X}" B="" C="DET-1"/>' for d in ddis)
        + "</TIM>"
    ).encode()
    buf = bytearray()
    last: dict[int, int] = {}
    for row in df.itertuples(index=False):
        dt = datetime.fromtimestamp(row.t, timezone.utc) - EPOCH_1980
        days = dt.days
        ms = int(round(dt.seconds * 1000 + dt.microseconds / 1000))
        buf += struct.pack("<IH", ms, days)
        buf += struct.pack(
            "<iiB", int(round(row.lat * 1e7)), int(round(row.lon * 1e7)), int(row.status)
        )
        changed = []
        for i, d in enumerate(ddis):
            v = int(round(getattr(row, f"DDI_{d}")))
            if last.get(i) != v:
                changed.append((i, v))
                last[i] = v
        buf += struct.pack("<B", len(changed))
        for i, v in changed:
            buf += struct.pack("<Bi", i, v)
    return header, bytes(buf)


__all__ = ["DDI_MAP", "is_isoxml", "read_isoxml", "write_timelog"]
