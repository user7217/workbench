"""End to end: the MCP connector's tools change the dashboard through its real agent socket.

The dashboard server runs with WORKBENCH_AGENT_SOCKET set, and the connector's tools (without the
OAuth layer, which is covered in test_mcp_auth) talk to it only through that Unix socket, as they do
on the home server. Every change must land on the remote as a `web(...)` commit.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from test_agent_api import _seed_state
from test_server import _remote_head, _remote_yaml

from workbench.mcp.client import AgentClient
from workbench.mcp.server import build_server
from workbench.server.app import create_app

ROVER = "projects/demo-nested-rover/project.yaml"


@pytest.fixture
def short_dir() -> Iterator[Path]:
    """A directory with a short path: Unix socket paths are limited to about 100 bytes."""
    with tempfile.TemporaryDirectory(prefix="wbe-") as d:
        yield Path(d)


async def _call(client: Client, tool: str, **args: Any) -> tuple[bool, str]:
    async with client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    return result.is_error, result.content[0].text


async def test_connector_tools_change_the_dashboard_through_the_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_dir: Path
) -> None:
    state, laptop_data = _seed_state(tmp_path, monkeypatch)
    sock = short_dir / "agent.sock"
    monkeypatch.setenv("WORKBENCH_AGENT_SOCKET", str(sock))
    monkeypatch.setenv("WORKBENCH_AGENT_SOCKET_UID", str(os.getuid()))
    monkeypatch.setenv("WORKBENCH_AGENT_SOCKET_GID", str(os.getgid()))
    app = create_app(state=state, enable_background_loop=False)

    with TestClient(app) as dashboard:
        mcp = Client(build_server(AgentClient(sock)))

        is_error, text = await _call(mcp, "list_projects")
        assert not is_error and "demo-nested-rover" in text

        is_error, text = await _call(mcp, "show_project", project_id="demo-nested-rover")
        assert not is_error and "ekf-local" in text

        # Status plus notes: two commits, both labelled web(...); the roll-up names the parents
        is_error, text = await _call(
            mcp,
            "set_item_status",
            project_id="demo-nested-rover",
            item_id="ekf-local",
            status="done",
            notes="Converges in the field test.",
        )
        assert not is_error, text
        assert _remote_head(laptop_data)[0] == "web(demo-nested-rover/ekf-local): notes"

        is_error, text = await _call(
            mcp,
            "add_item",
            project_id="demo-nested-rover",
            name="Wheel slip detection",
            parent_id="navigation",
        )
        assert not is_error, text
        assert _remote_head(laptop_data)[0].startswith("web(demo-nested-rover/navigation): add")

        is_error, text = await _call(
            mcp, "suggest_item", project_id="demo-nested-rover", name="Battery telemetry"
        )
        assert not is_error, text
        assert _remote_head(laptop_data)[0].startswith("web(demo-nested-rover): suggest")

        # The dashboard's error reaches the model; nothing is committed
        head = _remote_head(laptop_data)[1]
        is_error, text = await _call(
            mcp, "set_item_status", project_id="demo-nested-rover", item_id="nope", status="done"
        )
        assert is_error and "not found" in text.lower()
        assert _remote_head(laptop_data)[1] == head

        # The dashboard itself sees every change in its one snapshot
        snap = dashboard.get("/api/snapshot").json()
        assert any(p["id"] == "demo-nested-rover" for p in snap["projects"])

    rover = _remote_yaml(laptop_data, ROVER)
    build = rover["phases"][0]
    navigation = build["components"][0]
    ekf_local = navigation["components"][0]["components"][0]
    assert ekf_local["status"] == "done"
    assert ekf_local["notes"] == "Converges in the field test."
    assert any(c["name"] == "Wheel slip detection" for c in navigation["components"])
    assert [s["name"] for s in rover.get("suggestions", [])] == ["Battery telemetry"]
    assert not sock.exists()
