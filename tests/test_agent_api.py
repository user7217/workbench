"""Tests for the restricted agent API (claude.ai connector) and its Unix socket server."""

from __future__ import annotations

import datetime
import os
import socket
import stat
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_server import (
    NESTED_PROJECT_YAML,
    WRITE_HEADERS,
    _find_component_in,
    _remote_head,
    _remote_yaml,
)

from workbench.demo import init_demo_environment
from workbench.gitops import commit_all, create_worktree, push_head_to_branch, remove_worktree
from workbench.server.agent_api import create_agent_app
from workbench.server.agent_socket import (
    AgentSocketSettings,
    agent_socket_settings_from_env,
    bind_agent_socket,
)
from workbench.server.app import create_app
from workbench.server.sync import ServerState

ROVER = "projects/demo-nested-rover/project.yaml"
DSP = "learning/demo-signal-processing/path.yaml"
P = "/agent/v1/projects/demo-nested-rover"
L = "/agent/v1/learning/demo-signal-processing"


def _seed_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ServerState, Path]:
    """Demo environment plus the nested rover project on the remote; returns (state, laptop data)."""
    info = init_demo_environment(tmp_path / "demo_agent")
    laptop_data = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(Path(info["laptop_home"])))

    wt = tmp_path / "wt_seed_nested"
    create_worktree(laptop_data, wt, ref="origin/main")
    proj_dir = wt / "projects" / "demo-nested-rover"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.yaml").write_text(NESTED_PROJECT_YAML, encoding="utf-8")
    commit_all(wt, "Add nested rover project")
    assert push_head_to_branch(wt, branch="main")
    remove_worktree(laptop_data, wt)

    state = ServerState(
        remote_url=str(Path(info["remote_bare"])),
        state_dir=tmp_path / "server_state_agent",
        branch="main",
    )
    return state, laptop_data


@pytest.fixture
def agent_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An agent API client on a synced ServerState (no socket), plus the laptop data clone."""
    state, laptop_data = _seed_state(tmp_path, monkeypatch)
    assert state.sync_once() is True
    yield TestClient(create_agent_app(state)), laptop_data


def _post(client: TestClient, url: str, body: object, headers: dict | None = WRITE_HEADERS):
    return client.post(url, headers=headers, json=body)


def _assert_ok(resp: httpx.Response, laptop_data: Path, subject: str | None = None) -> dict:
    """200 with a small body whose commit is the remote head; checks the commit subject if given."""
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "snapshot" not in body
    head_subject, sha = _remote_head(laptop_data)
    assert body["commit"] == sha
    if subject is not None:
        assert head_subject == subject
    return body


def test_index_and_reads(agent_env) -> None:
    client, _ = agent_env
    hdr = {"X-Workbench": "1"}

    index = client.get("/agent/v1/index", headers=hdr)
    assert index.status_code == 200, index.text
    data = index.json()
    assert set(data) == {"projects", "learning"}
    rover = next(p for p in data["projects"] if p["id"] == "demo-nested-rover")
    assert rover == {"id": "demo-nested-rover", "name": "Nested Rover", "status": "active"}
    # Archived projects are listed too, with their status
    assert {"id": "demo-old-game", "status": "archived"}.items() <= next(
        p for p in data["projects"] if p["id"] == "demo-old-game"
    ).items()
    dsp = next(lp for lp in data["learning"] if lp["id"] == "demo-signal-processing")
    assert dsp == {
        "id": "demo-signal-processing",
        "name": "Signal Processing Basics",
        "status": "active",
    }

    project = client.get(P, headers=hdr)
    assert project.status_code == 200
    assert project.json()["phases"][0]["id"] == "build"
    learning = client.get(L, headers=hdr)
    assert learning.status_code == 200
    assert learning.json()["topics"][0]["id"] == "sampling"

    assert client.get("/agent/v1/projects/nope", headers=hdr).status_code == 404
    assert client.get("/agent/v1/learning/nope", headers=hdr).status_code == 404
    # The X-Workbench header is required on reads too
    assert client.get("/agent/v1/index").status_code == 403
    assert client.get(P).status_code == 403


def test_allowed_writes_commit_as_web(agent_env) -> None:
    client, laptop_data = agent_env
    today = datetime.date.today().isoformat()

    # Component status, with the roll-up suffix
    body = _assert_ok(
        _post(client, f"{P}/items/ekf-local/status", {"status": "done"}),
        laptop_data,
        "web(demo-nested-rover/ekf-local): status done; ekf done, navigation done, build done",
    )
    assert body["item_id"] == "ekf-local"
    remote = _remote_yaml(laptop_data, ROVER)
    assert remote["phases"][0]["status"] == "done"

    # Phase status
    _assert_ok(
        _post(client, f"{P}/items/build/status", {"status": "in_progress"}),
        laptop_data,
        "web(demo-nested-rover/build): status in_progress",
    )

    # Notes and summary
    body = _assert_ok(
        _post(
            client,
            f"{P}/items/navigation/fields",
            {"notes": "Tuned the EKF covariances.", "summary": "Localization stack"},
        ),
        laptop_data,
        "web(demo-nested-rover/navigation): notes, summary",
    )
    assert body["item_id"] == "navigation"
    nav = _find_component_in(
        _remote_yaml(laptop_data, ROVER)["phases"][0]["components"], "navigation"
    )
    assert nav["notes"] == "Tuned the EKF covariances."
    assert nav["summary"] == "Localization stack"

    # Add a phase (parent null) and a component under a phase
    body = _assert_ok(
        _post(client, f"{P}/items", {"parent_id": None, "name": "Deployment"}), laptop_data
    )
    phase_id = body["item_id"]
    assert _remote_head(laptop_data)[0] == f"web(demo-nested-rover): add phase {phase_id}"
    body = _assert_ok(
        _post(
            client,
            f"{P}/items",
            {
                "parent_id": "build",
                "name": "Wheel Odometry",
                "summary": "Encoders",
                "status": "in_progress",
            },
        ),
        laptop_data,
    )
    comp_id = body["item_id"]
    assert _remote_head(laptop_data)[0] == f"web(demo-nested-rover/build): add component {comp_id}"
    remote = _remote_yaml(laptop_data, ROVER)
    assert [p["id"] for p in remote["phases"]][-1] == phase_id
    comp = _find_component_in(remote["phases"][0]["components"], comp_id)
    assert comp["status"] == "in_progress" and comp["summary"] == "Encoders"

    # A suggestion in the project's inbox
    body = _assert_ok(
        _post(
            client, f"{P}/suggestions", {"name": "Try a UKF", "summary": "Compare", "under": "ekf"}
        ),
        laptop_data,
    )
    sug_id = body["suggestion_id"]
    assert _remote_head(laptop_data)[0] == f"web(demo-nested-rover): suggest {sug_id}"
    sug = next(s for s in _remote_yaml(laptop_data, ROVER)["suggestions"] if s["id"] == sug_id)
    assert sug == {
        "id": sug_id,
        "name": "Try a UKF",
        "summary": "Compare",
        "under": "ekf",
        "added": today,
    }

    # Learning item status and notes
    body = _assert_ok(
        _post(client, f"{L}/items/aliasing/status", {"status": "done"}),
        laptop_data,
        "web(learning/demo-signal-processing/aliasing): status done",
    )
    assert body["item_id"] == "aliasing"
    _assert_ok(
        _post(client, f"{L}/items/aliasing/fields", {"notes": "Folding diagram helps."}),
        laptop_data,
        "web(learning/demo-signal-processing/aliasing): notes",
    )
    topic = _remote_yaml(laptop_data, DSP)["topics"][0]
    aliasing = next(s for s in topic["subtopics"] if s["id"] == "aliasing")
    assert aliasing["status"] == "done" and aliasing["notes"] == "Folding diagram helps."


def test_disallowed_writes_and_routes_are_rejected(agent_env) -> None:
    client, laptop_data = agent_env
    _, head_before = _remote_head(laptop_data)

    rejected_bodies = [
        # Only notes and summary may be edited
        (f"{P}/items/navigation/fields", {"name": "Renamed"}, 400),
        (f"{P}/items/navigation/fields", {"notes": "x", "description": "y"}, 400),
        (f"{P}/items/navigation/fields", {}, 400),
        (f"{L}/items/aliasing/fields", {"name": "Renamed"}, 400),
        # No explicit id, position or dependencies when adding
        (f"{P}/items", {"parent_id": None, "name": "X", "id": "x"}, 400),
        (f"{P}/items", {"parent_id": "build", "name": "X", "depends_on": ["ekf"]}, 400),
        (f"{P}/items", {"parent_id": None, "name": "X", "position": 0}, 400),
        (f"{P}/items", {"parent_id": None}, 400),
        (f"{P}/items", {"parent_id": "no-such-item", "name": "X"}, 404),
        # Status bodies
        (f"{P}/items/build/status", {}, 400),
        (f"{P}/items/build/status", {"status": "done", "notes": "x"}, 400),
        (f"{P}/items/build/status", {"status": "finished"}, 400),
        (f"{L}/items/aliasing/status", {"status": "archived"}, 400),
        (f"{P}/items/no-such-item/status", {"status": "done"}, 404),
        ("/agent/v1/projects/no-such-project/items/build/status", {"status": "done"}, 404),
        # Suggestions
        (f"{P}/suggestions", {"name": "X", "status": "done"}, 400),
        (f"{P}/suggestions", {"summary": "no name"}, 400),
        (f"{P}/suggestions", {"name": "X", "under": "no-such-item"}, 404),
        # Not a JSON object
        (f"{P}/items/build/status", ["done"], 400),
    ]
    for url, body, code in rejected_bodies:
        resp = _post(client, url, body)
        assert resp.status_code == code, (url, body, resp.text)
        assert isinstance(resp.json()["detail"], str)

    # Headers: X-Workbench is required; writes must be JSON
    resp = _post(client, f"{P}/items/build/status", {"status": "done"}, headers=None)
    assert resp.status_code == 403
    resp = client.post(
        f"{P}/items/build/status",
        headers={"X-Workbench": "1", "Content-Type": "text/plain"},
        content='{"status": "done"}',
    )
    assert resp.status_code == 415

    # Every other dashboard write (and the dashboard API itself) does not exist here
    missing_routes = [
        f"{P}/items/ekf/delete",
        f"{P}/items/ekf/move",
        f"{P}/status",
        f"{P}/fields",
        f"{P}/parent",
        f"{P}/suggestions/x/keep",
        f"{P}/suggestions/x/dismiss",
        f"{L}/status",
        f"{L}/fields",
        f"{L}/items/aliasing/move",
        "/agent/v1/today",
        "/agent/v1/today/enabled",
        "/agent/v1/sync",
        "/api/projects/demo-nested-rover/items/build/status",
        "/api/sync",
    ]
    for url in missing_routes:
        assert _post(client, url, {"status": "done"}).status_code == 404, url
    assert client.delete(f"{P}/items/ekf", headers=WRITE_HEADERS).status_code == 404
    for url in ("/api/snapshot", "/docs", "/openapi.json", "/"):
        assert client.get(url, headers={"X-Workbench": "1"}).status_code == 404, url

    assert _remote_head(laptop_data)[1] == head_before


def test_settings_from_env() -> None:
    assert agent_socket_settings_from_env({}) is None
    assert agent_socket_settings_from_env({"WORKBENCH_AGENT_SOCKET": " "}) is None
    settings = agent_socket_settings_from_env(
        {
            "WORKBENCH_AGENT_SOCKET": "/agent/agent.sock",
            "WORKBENCH_AGENT_SOCKET_UID": "1001",
            "WORKBENCH_AGENT_SOCKET_GID": "",
        }
    )
    assert settings == AgentSocketSettings(Path("/agent/agent.sock"), uid=1001, gid=None)
    with pytest.raises(ValueError, match="WORKBENCH_AGENT_SOCKET_GID"):
        agent_socket_settings_from_env(
            {"WORKBENCH_AGENT_SOCKET": "/a.sock", "WORKBENCH_AGENT_SOCKET_GID": "mcpsvc"}
        )


@pytest.fixture
def short_dir():
    """A directory with a short path: Unix socket paths are limited to about 100 bytes."""
    with tempfile.TemporaryDirectory(prefix="wba-") as d:
        yield Path(d)


def test_bind_refuses_to_replace_a_non_socket(short_dir: Path) -> None:
    path = short_dir / "agent.sock"
    path.write_text("not a socket", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a socket"):
        bind_agent_socket(AgentSocketSettings(path))
    assert path.read_text(encoding="utf-8") == "not a socket"


def test_server_serves_agent_api_on_unix_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, short_dir: Path
) -> None:
    state, laptop_data = _seed_state(tmp_path, monkeypatch)
    sock_path = short_dir / "agent" / "agent.sock"
    sock_path.parent.mkdir()
    # A socket file left behind by an earlier run is replaced
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()
    assert sock_path.exists()

    monkeypatch.setenv("WORKBENCH_AGENT_SOCKET", str(sock_path))
    monkeypatch.setenv("WORKBENCH_AGENT_SOCKET_UID", str(os.getuid()))
    monkeypatch.setenv("WORKBENCH_AGENT_SOCKET_GID", str(os.getgid()))
    app = create_app(state=state, enable_background_loop=False)

    with TestClient(app) as dashboard:
        st = sock_path.lstat()
        assert stat.S_ISSOCK(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert (st.st_uid, st.st_gid) == (os.getuid(), os.getgid())

        transport = httpx.HTTPTransport(uds=str(sock_path))
        with httpx.Client(transport=transport, base_url="http://agent", timeout=60) as agent:
            index = agent.get("/agent/v1/index", headers={"X-Workbench": "1"})
            assert index.status_code == 200, index.text
            assert any(p["id"] == "demo-nested-rover" for p in index.json()["projects"])
            assert agent.get("/api/snapshot", headers={"X-Workbench": "1"}).status_code == 404

            resp = agent.post(
                f"{P}/items/build/status", headers=WRITE_HEADERS, json={"status": "in_progress"}
            )
            _assert_ok(resp, laptop_data, "web(demo-nested-rover/build): status in_progress")

        # The dashboard sees the agent's write in its one shared snapshot
        snap = dashboard.get("/api/snapshot").json()
        rover = next(p for p in snap["projects"] if p["id"] == "demo-nested-rover")
        assert rover["phases"][0]["status"] == "in_progress"

    assert not sock_path.exists()


def test_no_socket_when_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state, _ = _seed_state(tmp_path, monkeypatch)
    app = create_app(state=state, enable_background_loop=False, web_dist=tmp_path / "no-web")
    with TestClient(app) as dashboard:
        assert dashboard.get("/api/status").json()["last_error"] is None
        # The agent routes are never part of the dashboard app itself
        assert dashboard.get("/agent/v1/index", headers={"X-Workbench": "1"}).status_code == 404
    assert not any(state.state_dir.rglob("*.sock"))
