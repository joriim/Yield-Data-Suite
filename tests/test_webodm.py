"""WebODM client and the drone path through the store, against a fake WebODM."""

import asyncio
import json
import zipfile

import pytest
import yaml

from yieldsuite import synthetic
from yieldsuite.drone import MAVIC3M_OPTIONS, WebODMClient, select_images


class _Resp:
    def __init__(self, payload=None, status=200, content=b""):
        self.payload, self.status_code, self.content = payload, status, content
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield self.content


class FakeWebODM:
    """Records calls; one project, tasks complete after two status polls."""

    def __init__(self, ortho_bytes=b"tif"):
        self.calls, self.uploaded, self.polls = [], [], 0
        self.ortho = ortho_bytes
        self.options = None

    def post(self, url, data=None, files=None, headers=None):
        self.calls.append(("POST", url))
        if url.endswith("/api/token-auth/"):
            return _Resp({"token": "tok"})
        assert headers == {"Authorization": "JWT tok"}
        if url.endswith("/api/projects/"):
            return _Resp({"id": 7})
        if url.endswith("/tasks/"):
            assert data["partial"] == "true"
            self.options = json.loads(data["options"])
            return _Resp({"id": "task-1"})
        if url.endswith("/upload/"):
            self.uploaded += [name for _, (name, _) in files]
            return _Resp({"success": True})
        if url.endswith("/commit/"):
            return _Resp({"id": "task-1"})
        return _Resp({"detail": "not found"}, 404)

    def get(self, url, params=None, headers=None, stream=False):
        self.calls.append(("GET", url))
        if url.endswith("/api/projects/"):
            return _Resp([])
        if url.endswith("/tasks/task-1/"):
            self.polls += 1
            return _Resp(
                {
                    "status": 40 if self.polls >= 2 else 20,
                    "running_progress": 0.5,
                    "available_assets": ["orthophoto.tif"],
                    "images_count": 8,
                }
            )
        if url.endswith("/download/orthophoto.tif"):
            return _Resp(content=self.ortho)
        return _Resp({"detail": "not found"}, 404)


def _flight(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        for band in ("G", "R", "RE", "NIR"):
            (folder / f"DJI_2026063010{i:02d}_000{i}_MS_{band}.TIF").write_bytes(b"x")
        (folder / f"DJI_2026063010{i:02d}_000{i}_D.JPG").write_bytes(b"x")
    return folder


def test_select_images_keeps_multispectral_frames(tmp_path):
    files = select_images(_flight(tmp_path / "f"))
    assert len(files) == 8 and all("_MS_" in p.name for p in files)
    assert len(select_images(tmp_path / "f", multispectral_only=False)) == 10


def test_client_submit_status_download(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBODM_USERNAME", "u")
    monkeypatch.setenv("WEBODM_PASSWORD", "p")
    fake = FakeWebODM()
    c = WebODMClient("https://odm.example", session=fake)
    pid = c.project("farm/field")
    tid = c.submit(pid, select_images(_flight(tmp_path / "f")), "field 2026-06-30", batch=3)
    assert (pid, tid) == (7, "task-1") and len(fake.uploaded) == 8
    assert sum(1 for m, u in fake.calls if u.endswith("/upload/")) == 3  # 8 images in 3 batches
    assert {o["name"]: o["value"] for o in fake.options} == MAVIC3M_OPTIONS
    st = c.wait(pid, tid, poll_s=0, sleep=lambda s: None)
    assert st["status"] == "completed" and fake.polls == 2
    out = c.download(pid, tid, "orthophoto.tif", tmp_path / "o.tif")
    assert out.read_bytes() == b"tif"


def test_store_drone_round_trip_into_l5(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    pytest.importorskip("rasterio")
    pytest.importorskip("gstools")
    from yieldsuite.drone import webodm
    from yieldsuite.mcp_server import build_server
    from yieldsuite.store import FarmStore, init_demo_store

    ortho = synthetic.drone_ortho(tmp_path / "ortho.tif").read_bytes()
    fake = FakeWebODM(ortho)
    monkeypatch.setenv("WEBODM_URL", "https://odm.example")
    monkeypatch.setenv("WEBODM_TOKEN", "tok")
    real_init = webodm.WebODMClient.__init__

    def init(self, url=None, token=None, session=None):
        real_init(self, url, token, session or fake)

    monkeypatch.setattr(webodm.WebODMClient, "__init__", init)

    store = FarmStore(init_demo_store(tmp_path / "s"))
    server = build_server(store, actor="t")
    key = {"farm": "demo-farm", "field": "synthetic-01", "year": 2026}

    def call(name, args):
        return json.loads(asyncio.run(server.call_tool(name, {**key, **args})).content[0].text)

    flight = _flight(tmp_path / "flight")
    zpath = tmp_path / "flight.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for p in flight.iterdir():
            z.write(p, p.name)
    call("upload_raw", {"kind": "drone_images", "source_path": str(zpath)})
    sub = call("webodm_submit", {"images": "raw/drone_images/flight.zip", "date": "2026-06-30"})
    assert sub["images"] == 8 and sub["task_id"] == "task-1"
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="running"):  # first poll: still running
        asyncio.run(
            server.call_tool(
                "import_orthomosaic", {**key, "date": "2026-06-30", "task_id": "task-1"}
            )
        )
    imp = call("import_orthomosaic", {"date": "2026-06-30", "task_id": "task-1"})
    assert imp["path"] == "raw/drone_ortho/2026-06-30_orthophoto.tif"

    ydir = store.year_dir("demo-farm", "synthetic-01", 2026)
    cfg = yaml.safe_load((ydir / "field.yaml").read_text())
    assert cfg["imagery"] == [
        {"kind": "drone", "path": imp["path"], "date": "2026-06-30", "sensor": "mavic3m"}
    ]
    run = call("run_chain", {"to_level": "L5"})
    assert run["ok"], run["error"]
    card = json.loads((ydir / "output" / "L5_synthetic-01_2026_model_card.json").read_text())
    assert card["sources"]["used"] == ["drone"]
