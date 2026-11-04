"""GrainSense cloud REST API (GS Flow / combine sessions, GS Go measurements).

Based on "GrainSense public documentation" (last updated 16/10/2024):

- login:  POST {base}/v1/login/  {"email", "password"} -> JWT
- Flow (combine): GET  {base}/flow/v1/measurementSessions/?ordering=-started_at
                  GET  {base}/flow/v1/measurementSession/<uuid>
                  POST {base}/flow/v1/measurementSessions/export/ {"measurement_sessions", "format"}
- Go (handheld):  GET  {base}/v3/measurements/ ; POST {base}/v1/measurements/export/
- calibration bases: GET {base}/v1/applicationData/?area=FI

Credentials come from the environment (GRAINSENSE_EMAIL, GRAINSENSE_PASSWORD) or a
token (GRAINSENSE_TOKEN); they are never written to the store or the repository.
Response field names beyond those in the documentation are not assumed: session
JSON and exports are saved as received (L0) and read through the generic,
reported column recognition.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://commercial.grainsense.com/api"


def nutrient_percentages(
    milligrams: dict, basis: dict, nutrients=("protein", "carbohydrate", "oil")
) -> dict:
    """Percentages from a measurement's milligrams and the calibration basis.

    As documented: dry = protein + carbohydrate + oil, total = moisture + dry;
    ``<nutrient>_moisture_basis`` "dry" divides by dry, "wet" by total.
    "fixed" is not implemented (the documented formula is incomplete).
    Moisture is returned on a wet basis.
    """
    mg = {
        k: float(milligrams.get(k, 0.0) or 0.0)
        for k in ("protein", "carbohydrate", "oil", "moisture")
    }
    dry = mg["protein"] + mg["carbohydrate"] + mg["oil"]
    total = mg["moisture"] + dry
    if total <= 0:
        raise ValueError("measurement has no milligrams")
    out = {"moisture_pct": 100.0 * mg["moisture"] / total}
    for n in nutrients:
        b = basis.get(f"{n}_moisture_basis")
        if b == "dry":
            out[f"{n}_pct"] = 100.0 * mg[n] / dry if dry > 0 else float("nan")
        elif b == "wet":
            out[f"{n}_pct"] = 100.0 * mg[n] / total
        elif b is None:
            continue
        else:
            raise NotImplementedError(
                f"{n}: moisture basis {b!r} not supported (documentation incomplete); "
                "use the percentages from a GrainSense export instead"
            )
        out[f"{n}_basis"] = b
    return out


class GrainSenseClient:
    def __init__(self, base_url: str = DEFAULT_BASE, token: str | None = None, opener=None):
        self.base = base_url.rstrip("/")
        self.token = token or os.environ.get("GRAINSENSE_TOKEN")
        self._open = opener or urllib.request.urlopen

    # --- transport --------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        raw: bool = False,
    ):
        url = f"{self.base}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "yieldsuite",
        }
        if self.token:
            headers["Authorization"] = f"JWT {self.token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with self._open(req, timeout=60) as resp:  # noqa: S310 - configured URL
            content = resp.read()
        return content if raw else json.loads(content.decode() or "null")

    # --- endpoints --------------------------------------------------------------

    def login(self, email: str | None = None, password: str | None = None) -> str:
        email = email or os.environ.get("GRAINSENSE_EMAIL")
        password = password or os.environ.get("GRAINSENSE_PASSWORD")
        if not (email and password):
            raise RuntimeError("set GRAINSENSE_EMAIL and GRAINSENSE_PASSWORD (or GRAINSENSE_TOKEN)")
        res = self._request("POST", "v1/login/", body={"email": email, "password": password})
        token = res.get("token") or res.get("access") or res.get("jwt")
        if not token:
            raise RuntimeError(f"login response has no token field (keys: {sorted(res)})")
        self.token = token
        return token

    def flow_sessions(
        self,
        start: str | None = None,
        end: str | None = None,
        calibration_uuid: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """All combine (Flow) measurement sessions, newest first, following pagination."""
        out, offset = [], 0
        while True:
            page = self._request(
                "GET",
                "flow/v1/measurementSessions/",
                params={
                    "ordering": "-started_at",
                    "limit": limit,
                    "offset": offset,
                    "measured_at__gte": start,
                    "measured_at__lte": end,
                    "calibration__uuid__in": calibration_uuid,
                },
            )
            items = page.get("results", page) if isinstance(page, dict) else page
            out.extend(items)
            if not isinstance(page, dict) or not page.get("next") or not items:
                return out
            offset += len(items)

    def flow_session(self, uuid: str) -> dict:
        return self._request("GET", f"flow/v1/measurementSession/{uuid}")

    def export_flow_sessions(self, uuids: list[str], fmt: str = "xlsx") -> bytes:
        return self._request(
            "POST",
            "flow/v1/measurementSessions/export/",
            raw=True,
            body={"measurement_sessions": list(uuids), "send_email": "false", "format": fmt},
        )

    def export_measurements(
        self, uuids: list[str], fmt: str = "csv", timezone: str = "UTC"
    ) -> bytes:
        return self._request(
            "POST",
            "v1/measurements/export/",
            raw=True,
            body={
                "measurements": list(uuids),
                "send_email": False,
                "format": fmt,
                "timezone": timezone,
            },
        )

    def application_data(self, area: str = "FI") -> dict:
        """Calibration bases per calibration for an area (needed for percentages)."""
        return self._request("GET", "v1/applicationData/", params={"area": area.upper()})
