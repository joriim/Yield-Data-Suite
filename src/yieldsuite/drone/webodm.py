"""WebODM REST API client for multispectral drone flights (e.g. DJI Mavic 3M).

Endpoints (WebODM API): ``POST /api/token-auth/`` (JWT), ``/api/projects/``,
``/api/projects/{p}/tasks/`` with partial upload (``partial=true``, then
``.../upload/`` in batches and ``.../commit/``), task status, and
``.../download/{asset}`` for the results (``orthophoto.tif``, ``dsm.tif``).

Credentials come from WEBODM_URL and WEBODM_TOKEN or WEBODM_USERNAME /
WEBODM_PASSWORD; they are never stored.

The processing options for Mavic 3M are a starting point: check them against
the ODM version of your WebODM node (option names change between versions).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# WebODM task status codes
STATUS = {10: "queued", 20: "running", 30: "failed", 40: "completed", 50: "canceled"}

MAVIC3M_OPTIONS: dict = {
    "radiometric-calibration": "camera+sun",  # reflectance from the sunlight sensor
    "orthophoto-resolution": 5,  # cm/pixel
    "dsm": True,
    "auto-boundary": True,
}

# DJI Mavic 3M names its multispectral frames ..._MS_G.TIF, _MS_R, _MS_RE, _MS_NIR
MS_SUFFIXES = ("_MS_G.TIF", "_MS_R.TIF", "_MS_RE.TIF", "_MS_NIR.TIF")


def select_images(folder: str | Path, multispectral_only: bool = True) -> list[Path]:
    """Image files of a flight; by default only the Mavic 3M multispectral bands."""
    files = sorted(
        p
        for p in Path(folder).rglob("*")
        if p.is_file() and p.suffix.lower() in (".tif", ".tiff", ".jpg", ".jpeg")
    )
    if multispectral_only:
        files = [p for p in files if p.name.upper().endswith(MS_SUFFIXES)]
    if not files:
        raise ValueError(f"no {'multispectral ' if multispectral_only else ''}images in {folder}")
    return files


class WebODMClient:
    def __init__(self, url: str | None = None, token: str | None = None, session=None):
        self.url = (url or os.environ.get("WEBODM_URL") or "").rstrip("/")
        if not self.url:
            raise RuntimeError("set WEBODM_URL (e.g. https://webodm.example.org)")
        self.token = token or os.environ.get("WEBODM_TOKEN")
        if session is None:
            try:
                import requests
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise ImportError("WebODM needs requests: pip install yieldsuite[drone]") from exc
            session = requests.Session()
        self.http = session

    # --- transport ----------------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"JWT {self.token}"} if self.token else {}

    def _check(self, resp, what: str):
        if resp.status_code >= 400:
            raise RuntimeError(f"WebODM {what} failed: HTTP {resp.status_code} {resp.text[:200]}")
        return resp

    def login(self, username: str | None = None, password: str | None = None) -> str:
        username = username or os.environ.get("WEBODM_USERNAME")
        password = password or os.environ.get("WEBODM_PASSWORD")
        if not (username and password):
            raise RuntimeError("set WEBODM_USERNAME and WEBODM_PASSWORD (or WEBODM_TOKEN)")
        r = self._check(
            self.http.post(
                f"{self.url}/api/token-auth/", data={"username": username, "password": password}
            ),
            "login",
        )
        self.token = r.json()["token"]
        return self.token

    def ensure_token(self):
        if not self.token:
            self.login()

    # --- projects and tasks -------------------------------------------------------

    def project(self, name: str) -> int:
        """Id of the project with this name, created if missing."""
        self.ensure_token()
        r = self._check(
            self.http.get(
                f"{self.url}/api/projects/", params={"name": name}, headers=self._headers()
            ),
            "project list",
        )
        items = r.json()
        items = items.get("results", items) if isinstance(items, dict) else items
        for p in items:
            if p.get("name") == name:
                return int(p["id"])
        r = self._check(
            self.http.post(
                f"{self.url}/api/projects/", data={"name": name}, headers=self._headers()
            ),
            "project create",
        )
        return int(r.json()["id"])

    def submit(
        self,
        project_id: int,
        images: list[Path],
        name: str,
        options: dict | None = None,
        batch: int = 50,
        progress=None,
    ) -> str:
        """Create a task, upload images in batches, start processing. Returns the task id."""
        self.ensure_token()
        opts = [{"name": k, "value": v} for k, v in (options or MAVIC3M_OPTIONS).items()]
        base = f"{self.url}/api/projects/{project_id}/tasks/"
        r = self._check(
            self.http.post(
                base,
                data={"name": name, "partial": "true", "options": json.dumps(opts)},
                headers=self._headers(),
            ),
            "task create",
        )
        task_id = r.json()["id"]
        for i in range(0, len(images), batch):
            chunk = images[i : i + batch]
            files = [("images", (p.name, open(p, "rb"))) for p in chunk]  # noqa: SIM115
            try:
                self._check(
                    self.http.post(
                        f"{base}{task_id}/upload/", files=files, headers=self._headers()
                    ),
                    "image upload",
                )
            finally:
                for _, (_, fh) in files:
                    fh.close()
            if progress:
                progress(min(i + batch, len(images)), len(images))
        self._check(
            self.http.post(f"{base}{task_id}/commit/", headers=self._headers()), "task commit"
        )
        return task_id

    def status(self, project_id: int, task_id: str) -> dict:
        self.ensure_token()
        r = self._check(
            self.http.get(
                f"{self.url}/api/projects/{project_id}/tasks/{task_id}/", headers=self._headers()
            ),
            "task status",
        )
        t = r.json()
        code = t.get("status")
        return {
            "task_id": task_id,
            "status": STATUS.get(code, str(code)),
            "progress": t.get("running_progress"),
            "error": t.get("last_error"),
            "assets": t.get("available_assets", []),
            "images": t.get("images_count"),
        }

    def wait(
        self,
        project_id: int,
        task_id: str,
        poll_s: float = 30,
        timeout_s: float = 6 * 3600,
        sleep=time.sleep,
    ) -> dict:
        waited = 0.0
        while True:
            st = self.status(project_id, task_id)
            if st["status"] in ("completed", "failed", "canceled"):
                return st
            if waited >= timeout_s:
                raise TimeoutError(f"task {task_id} still {st['status']} after {timeout_s} s")
            sleep(poll_s)
            waited += poll_s

    def download(self, project_id: int, task_id: str, asset: str, out: str | Path) -> Path:
        self.ensure_token()
        url = f"{self.url}/api/projects/{project_id}/tasks/{task_id}/download/{asset}"
        r = self._check(self.http.get(url, headers=self._headers(), stream=True), "download")
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
        return out
