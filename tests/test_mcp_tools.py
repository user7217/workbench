"""The claude.ai connector's MCP tools, against a fake agent API (no network, no dashboard)."""

from __future__ import annotations

import json
import socketserver
import tempfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client

from workbench.mcp.client import AgentApiError, AgentClient
from workbench.mcp.outline import format_index, format_learning_path, format_project
from workbench.mcp.server import build_server

PROJECT: dict[str, Any] = {
    "id": "robot-arm",
    "name": "Robot arm",
    "status": "active",
    "description": "A six-axis arm",
    "uses_phases": True,
    "phases": [
        {
            "id": "build",
            "name": "Build",
            "status": "in_progress",
            "components": [
                {
                    "id": "gripper",
                    "name": "Gripper",
                    "status": "done",
                    "summary": "Two-finger gripper",
                    "notes": "Works up to 2 kg; " + "x" * 400,
                    "depends_on": ["frame"],
                    "components": [
                        {"id": "gripper-pads", "name": "Gripper pads", "status": "not_started"}
                    ],
                },
                {"id": "frame", "name": "Frame", "status": "done"},
            ],
        }
    ],
    "topics": [],
    "suggestions": [{"id": "add-camera", "name": "Add a camera", "under": "build"}],
}
LEARNING: dict[str, Any] = {
    "id": "ros2",
    "name": "ROS 2",
    "status": "active",
    "topics": [
        {
            "id": "nodes",
            "name": "Nodes",
            "status": "done",
            "notes": "Can write a publisher",
            "subtopics": [{"id": "qos", "name": "QoS", "status": "in_progress"}],
        }
    ],
}
INDEX = {
    "projects": [
        {"id": "robot-arm", "name": "Robot arm", "status": "active"},
        {"id": "arm-sim", "name": "Arm sim", "status": "planned", "parent": "robot-arm"},
    ],
    "learning": [{"id": "ros2", "name": "ROS 2", "status": "active"}],
}


class FakeAgentApi:
    """Answers like the dashboard's agent API and records every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None, str | None]] = []
        self.fail: dict[str, tuple[int, Any]] = {}  # path suffix -> (status, body)

    def handle(self, method: str, path: str, body: dict[str, Any] | None, header: str | None):
        self.requests.append((method, path, body, header))
        for suffix, (status, payload) in self.fail.items():
            if path.endswith(suffix):
                return status, payload
        if method == "GET":
            if path == "/agent/v1/index":
                return 200, INDEX
            if path == "/agent/v1/projects/robot-arm":
                return 200, PROJECT
            if path == "/agent/v1/learning/ros2":
                return 200, LEARNING
            return 404, {"detail": f"Not found: {path.rsplit('/', 1)[-1]}"}
        extra = {"item_id": "new-item"} if path.endswith("/items") else {}
        return 200, {"ok": True, "commit": "abcdef1234567890", **extra}

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content) if request.content else None
            status, payload = self.handle(
                request.method, request.url.path, body, request.headers.get("X-Workbench")
            )
            return httpx.Response(status, json=payload)

        return httpx.MockTransport(handler)


@pytest.fixture
def api() -> FakeAgentApi:
    return FakeAgentApi()


@pytest.fixture
def mcp_client(api: FakeAgentApi):
    return Client(build_server(AgentClient(transport=api.transport())))


async def _call(client: Client, tool: str, **args: Any) -> tuple[bool, str]:
    async with client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    return result.is_error, result.content[0].text


# --- the tool list ---


async def test_only_the_intended_tools_are_exposed(mcp_client: Client) -> None:
    async with mcp_client:
        tools = {t.name: t for t in await mcp_client.list_tools()}
    assert set(tools) == {
        "list_projects",
        "show_project",
        "show_learning_path",
        "set_item_status",
        "update_item_notes",
        "add_item",
        "suggest_item",
        "set_learning_item_status",
        "update_learning_item_notes",
    }
    for tool in tools.values():
        assert tool.description and len(tool.description) > 40, tool.name
    assert tools["list_projects"].annotations.readOnlyHint is True
    assert tools["add_item"].annotations.readOnlyHint is False


# --- reads ---


async def test_list_projects(mcp_client: Client, api: FakeAgentApi) -> None:
    is_error, text = await _call(mcp_client, "list_projects")
    assert not is_error
    assert "- robot-arm: Robot arm [active]" in text
    assert "- arm-sim: Arm sim [planned] (part of robot-arm)" in text
    assert "- ros2: ROS 2 [active]" in text
    assert api.requests[0][3] == "1"  # the X-Workbench header the agent API requires


async def test_show_project_is_an_outline_not_raw_data(mcp_client: Client) -> None:
    is_error, text = await _call(mcp_client, "show_project", project_id="robot-arm")
    assert not is_error
    assert "- [in_progress] Build (build)" in text
    assert "  - [done] Gripper (gripper): Two-finger gripper" in text
    assert "    - [not_started] Gripper pads (gripper-pads)" in text
    assert "needs: frame" in text
    assert "1 suggestion(s)" in text and "Add a camera (add-camera) under build" in text
    assert "x" * 300 not in text  # long notes are clipped


async def test_show_learning_path(mcp_client: Client) -> None:
    is_error, text = await _call(mcp_client, "show_learning_path", path_id="ros2")
    assert not is_error
    assert "- [done] Nodes (nodes)" in text
    assert "  - [in_progress] QoS (qos)" in text
    assert "notes: Can write a publisher" in text


# --- writes ---


async def test_set_item_status_with_notes_makes_two_calls(
    mcp_client: Client, api: FakeAgentApi
) -> None:
    is_error, text = await _call(
        mcp_client,
        "set_item_status",
        project_id="robot-arm",
        item_id="gripper",
        status="done",
        notes="Grips 2 kg",
    )
    assert not is_error, text
    assert "Set robot-arm/gripper to done (commit abcdef1)" in text
    assert [(m, p, b) for m, p, b, _ in api.requests] == [
        ("POST", "/agent/v1/projects/robot-arm/items/gripper/status", {"status": "done"}),
        ("POST", "/agent/v1/projects/robot-arm/items/gripper/fields", {"notes": "Grips 2 kg"}),
    ]


async def test_set_item_status_reports_notes_failure_after_status(
    mcp_client: Client, api: FakeAgentApi
) -> None:
    api.fail["/fields"] = (422, {"detail": "notes: String should have at most 1000 characters"})
    is_error, text = await _call(
        mcp_client,
        "set_item_status",
        project_id="robot-arm",
        item_id="gripper",
        status="in_progress",
        notes="n",
    )
    assert is_error
    assert "Set robot-arm/gripper to in_progress" in text
    assert "notes were not saved" in text and "at most 1000 characters" in text


async def test_update_item_notes_needs_something(mcp_client: Client, api: FakeAgentApi) -> None:
    is_error, text = await _call(
        mcp_client, "update_item_notes", project_id="robot-arm", item_id="gripper"
    )
    assert is_error and "Nothing to update" in text
    assert api.requests == []

    is_error, text = await _call(
        mcp_client, "update_item_notes", project_id="robot-arm", item_id="gripper", summary="S"
    )
    assert not is_error
    assert api.requests[-1][2] == {"summary": "S"}


async def test_add_item(mcp_client: Client, api: FakeAgentApi) -> None:
    is_error, text = await _call(
        mcp_client,
        "add_item",
        project_id="robot-arm",
        name="Fix slipping",
        parent_id="gripper",
        summary="Pads slip when wet",
    )
    assert not is_error
    assert "with id new-item" in text and "under gripper" in text
    assert api.requests[-1][1:3] == (
        "/agent/v1/projects/robot-arm/items",
        {
            "parent_id": "gripper",
            "name": "Fix slipping",
            "status": "not_started",
            "summary": "Pads slip when wet",
        },
    )


async def test_add_top_level_item_sends_null_parent(mcp_client: Client, api: FakeAgentApi) -> None:
    is_error, _ = await _call(mcp_client, "add_item", project_id="robot-arm", name="Testing")
    assert not is_error
    assert api.requests[-1][2] == {"parent_id": None, "name": "Testing", "status": "not_started"}


async def test_suggest_item(mcp_client: Client, api: FakeAgentApi) -> None:
    is_error, text = await _call(
        mcp_client, "suggest_item", project_id="robot-arm", name="Add a camera", under="build"
    )
    assert not is_error and "inbox" in text
    assert api.requests[-1][1:3] == (
        "/agent/v1/projects/robot-arm/suggestions",
        {"name": "Add a camera", "under": "build"},
    )


async def test_learning_writes(mcp_client: Client, api: FakeAgentApi) -> None:
    is_error, _ = await _call(
        mcp_client, "set_learning_item_status", path_id="ros2", item_id="qos", status="done"
    )
    assert not is_error
    is_error, _ = await _call(
        mcp_client, "update_learning_item_notes", path_id="ros2", item_id="qos", notes="Got it"
    )
    assert not is_error
    assert [(p, b) for _, p, b, _ in api.requests] == [
        ("/agent/v1/learning/ros2/items/qos/status", {"status": "done"}),
        ("/agent/v1/learning/ros2/items/qos/fields", {"notes": "Got it"}),
    ]


async def test_null_commit_is_reported(mcp_client: Client, api: FakeAgentApi) -> None:
    api.fail["/status"] = (200, {"ok": True, "commit": None})
    _, text = await _call(
        mcp_client, "set_item_status", project_id="robot-arm", item_id="frame", status="done"
    )
    assert "no commit" in text


# --- validation and error mapping ---


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("show_project", {"project_id": "../etc"}),
        ("show_project", {"project_id": "Robot-Arm"}),
        (
            "set_item_status",
            {"project_id": "robot-arm", "item_id": "gripper", "status": "finished"},
        ),
        ("set_learning_item_status", {"path_id": "ros2", "item_id": "qos", "status": "parked"}),
        ("add_item", {"project_id": "robot-arm", "name": "x" * 81}),
        ("add_item", {"project_id": "robot-arm", "name": "ok", "parent_id": "a/b"}),
        ("update_item_notes", {"project_id": "robot-arm", "item_id": "g", "notes": "n" * 1001}),
        ("list_projects", {"command": "rm -rf /"}),
    ],
)
async def test_invalid_input_is_refused_before_any_call(
    mcp_client: Client, api: FakeAgentApi, tool: str, args: dict[str, Any]
) -> None:
    is_error, _ = await _call(mcp_client, tool, **args)
    assert is_error
    assert api.requests == []


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, {"detail": "Unknown item 'nope'"}, "Not found: Unknown item 'nope'"),
        (409, {"detail": "The project changed; reload"}, "Conflict: The project changed; reload"),
        (403, {"detail": "Learning paths are read-only here"}, "Not allowed: Learning paths"),
        (
            422,
            {"detail": [{"loc": ["body", "status"], "msg": "Input should be 'done'"}]},
            "Invalid input: status: Input should be 'done'",
        ),
        (500, "boom", "Dashboard error 500: boom"),
    ],
)
async def test_agent_api_errors_reach_the_model(
    mcp_client: Client, api: FakeAgentApi, status: int, body: Any, expected: str
) -> None:
    api.fail["/status"] = (status, body)
    is_error, text = await _call(
        mcp_client, "set_item_status", project_id="robot-arm", item_id="nope", status="done"
    )
    assert is_error
    assert expected in text


async def test_unreachable_dashboard_is_a_clear_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no socket", request=request)

    client = Client(build_server(AgentClient(transport=httpx.MockTransport(refuse))))
    is_error, text = await _call(client, "list_projects")
    assert is_error
    assert "not reachable" in text and "nothing was changed" in text


def test_client_checks_ids_itself() -> None:
    client = AgentClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(AgentApiError, match="Invalid project id"):
        client.project("../index")
    with pytest.raises(AgentApiError, match="Invalid item id"):
        client.set_item_status("robot-arm", "a%2Fb", "done")


# --- over a real Unix socket ---


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


@pytest.fixture
def socket_api() -> Iterator[tuple[Path, FakeAgentApi]]:
    fake = FakeAgentApi()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            status, payload = fake.handle(method, self.path, body, self.headers.get("X-Workbench"))
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def log_message(self, *args: Any) -> None:
            pass

    # Unix socket paths are limited to about 100 bytes, so not under pytest's tmp_path
    with tempfile.TemporaryDirectory(prefix="wbm-") as d:
        path = Path(d) / "agent.sock"
        server = _UnixHTTPServer(str(path), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield path, fake
        finally:
            server.shutdown()
            server.server_close()


async def test_tools_over_a_unix_socket(socket_api: tuple[Path, FakeAgentApi]) -> None:
    path, fake = socket_api
    client = Client(build_server(AgentClient(path)))
    is_error, text = await _call(client, "show_project", project_id="robot-arm")
    assert not is_error and "Gripper (gripper)" in text
    is_error, text = await _call(
        client, "set_item_status", project_id="robot-arm", item_id="frame", status="parked"
    )
    assert not is_error, text
    assert fake.requests[-1] == (
        "POST",
        "/agent/v1/projects/robot-arm/items/frame/status",
        {"status": "parked"},
        "1",
    )


# --- outline edge cases ---


def test_outlines_tolerate_sparse_data() -> None:
    assert "(none)" in format_index({})
    text = format_project({"id": "p", "name": "P", "status": "planned", "uses_phases": False})
    assert "Topics and their items" in text and "(none yet" in text
    assert "(none)" in format_learning_path({"id": "l", "name": "L", "status": "active"})
