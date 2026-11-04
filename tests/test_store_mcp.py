"""Farm data store and the farm-data MCP server."""

import asyncio
import json

import pytest

from yieldsuite.store import FarmStore, StoreError, init_demo_store


@pytest.fixture
def store(tmp_path):
    return FarmStore(init_demo_store(tmp_path / "store"))


def test_read_layout(store):
    assert store.list_farms() == ["demo-farm"]
    assert store.list_fields("demo-farm") == [{"field_id": "synthetic-01", "years": [2026]}]
    prof = store.get_machine_profile("demo-farm", "combine-a")
    assert prof["profile"]["tau_d_s"] == 12.0 and prof["history"][0]["version"] == 1
    assert store.get_qa("demo-farm", "synthetic-01", 2026)["note"].startswith("no QA")
    cfg = store.load_config("demo-farm", "synthetic-01", 2026)
    assert cfg.validate() == [] and cfg.product_class.value == "absolute"


def test_ids_are_validated(store):
    for bad in ("../etc", "a/b", "", ".hidden"):
        with pytest.raises(StoreError):
            store.list_fields(bad)
    with pytest.raises(StoreError):
        store.get_product("demo-farm/synthetic-01/2026/../../audit.jsonl")


def test_profile_versions_are_append_only(store):
    out = store.update_machine_profile(
        "demo-farm", "combine-a", {"tau_d_s": 14}, "QGIS check", actor="tester"
    )
    assert out["version"] == 2
    prof = store.get_machine_profile("demo-farm", "combine-a")
    assert prof["profile"]["tau_d_s"] == 14
    assert store.get_machine_profile("demo-farm", "combine-a", 1)["profile"]["tau_d_s"] == 12
    assert store.load_config("demo-farm", "synthetic-01", 2026).machines["combine-a"].tau_d_s == 14
    with pytest.raises(StoreError):
        store.update_machine_profile("demo-farm", "combine-a", {"nope": 1}, "x", actor="t")
    with pytest.raises(StoreError):
        store.update_machine_profile("demo-farm", "combine-a", {"tau_d_s": 1}, "", actor="t")
    audit = [json.loads(line) for line in (store.root / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["action"] == "update_machine_profile" and audit[-1]["actor"] == "tester"


def test_l0_is_immutable(store, tmp_path):
    src = tmp_path / "flight.zip"
    src.write_bytes(b"images")
    entry = store.upload_raw("demo-farm", "synthetic-01", 2026, "drone_images", src, "t")
    assert len(entry["sha256"]) == 64
    with pytest.raises(StoreError, match="immutable"):
        store.upload_raw("demo-farm", "synthetic-01", 2026, "drone_images", src, "t")


def test_scale_tickets_append(store):
    store.add_scale_ticket("demo-farm", "synthetic-01", 2026, 1000.0, "t", "combine-a", 17.0)
    path = store.year_dir("demo-farm", "synthetic-01", 2026) / "raw" / "scale_tickets.csv"
    assert len(path.read_text().splitlines()) == 1 + 6 + 1
    with pytest.raises(StoreError):
        store.add_scale_ticket("demo-farm", "synthetic-01", 2026, -5, "t")


def _call(server, name, args=None):
    res = asyncio.run(server.call_tool(name, args or {}))
    assert not res.is_error, res.content
    return json.loads(res.content[0].text)


def test_mcp_tools(store):
    pytest.importorskip("mcp")
    from yieldsuite.mcp_server import build_server

    server = build_server(store, actor="claude-test")
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert {"list_fields", "get_qa", "run_chain", "update_machine_profile"} <= set(tools)
    assert tools["get_qa"].annotations.read_only_hint is True
    assert tools["run_chain"].annotations.read_only_hint is False

    plan = _call(server, "plan_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    assert plan["product_class"] == "absolute" and plan["config_problems"] == []

    run = _call(server, "run_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    assert run["ok"] and run["mass_ok"] and run["machine_profiles"] == {"combine-a": 1}
    assert abs(run["k_factors"]["combine-a"] - 1 / 1.07) < 0.005
    l4 = [p for p in run["products"] if p.endswith(".tif")][0]

    products = _call(
        server, "list_products", {"farm": "demo-farm", "field": "synthetic-01", "level": "L4"}
    )
    assert l4 in {p["product_id"] for p in products["products"]}
    assert _call(server, "list_farms") == {"farms": ["demo-farm"]}
    qa = _call(server, "get_qa", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    assert qa["L4_qa"]["mass_conservation"]["ok"]

    # Human decision -> new profile version -> re-run uses it.
    _call(
        server,
        "update_machine_profile",
        {
            "farm": "demo-farm",
            "machine_id": "combine-a",
            "changes": {"tau_d_s": 13},
            "reason": "test",
        },
    )
    rerun = _call(server, "run_chain", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})
    assert rerun["machine_profiles"] == {"combine-a": 2}
    _call(
        server,
        "approve_qa",
        {
            "farm": "demo-farm",
            "field": "synthetic-01",
            "year": 2026,
            "decision": "approved",
            "note": "ok",
        },
    )
    assert (
        _call(server, "get_qa", {"farm": "demo-farm", "field": "synthetic-01", "year": 2026})[
            "approvals"
        ][0]["actor"]
        == "claude-test"
    )


def test_mcp_errors_are_tool_errors(store):
    pytest.importorskip("mcp")
    from mcp.server.mcpserver.exceptions import ToolError

    from yieldsuite.mcp_server import build_server

    server = build_server(store)
    with pytest.raises(ToolError, match="invalid farm id"):
        asyncio.run(server.call_tool("list_fields", {"farm": "../x"}))
