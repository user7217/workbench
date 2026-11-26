"""Restricted agent API for the claude.ai connector, served on the agent socket (agent_socket.py).

The connector may read the dashboard and make a few kinds of edits, nothing else: item status,
item notes/summary, adding a phase/topic or component, and filing a suggestion in a project's
inbox. Every write goes through the dashboard's own write functions (validation, privacy scan,
semantic diff, commit and push) and is committed as `web(<scope>): <summary>`. There is no
delete, move, parent link, project/learning-path status or fields, or Today's Work here; any
path not listed below is a 404.

Responses stay small (no snapshot): writes return {"ok": true, "commit": <sha or null>, ...};
errors are {"detail": "..."} with the dashboard's status codes (400/403/404/409/415/422).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from workbench.server.http_checks import (
    read_json_object,
    require_workbench_header,
    require_write_headers,
)
from workbench.server.sync import ServerState
from workbench.server.writes import (
    WriteError,
    add_project_item,
    add_suggestion,
    update_learning_item_fields,
    update_learning_item_status,
    update_project_item_fields,
    update_project_item_status,
)

PREFIX = "/agent/v1"
# Commit kind of every write made through this API
KIND = "web"

STATUS_KEYS: tuple[str, ...] = ("status",)
ITEM_FIELD_KEYS: tuple[str, ...] = ("notes", "summary")
ADD_ITEM_KEYS: tuple[str, ...] = ("parent_id", "name", "summary", "status")
SUGGESTION_KEYS: tuple[str, ...] = ("name", "summary", "under")


async def _write_body(
    request: Request, allowed: tuple[str, ...], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Check write headers and return the JSON body; 400 for keys outside `allowed` or missing ones."""
    require_write_headers(request)
    body = await read_json_object(request)
    unknown = sorted(str(k) for k in body if k not in allowed)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported field(s): {', '.join(unknown)} (allowed: {', '.join(allowed)})",
        )
    missing = [k for k in required if k not in body]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing field(s): {', '.join(missing)}")
    return body


async def _call_write[T](write_fn: Callable[..., T], *args: Any) -> T:
    """Run a dashboard write function in a thread as a `web` write; WriteError -> HTTP error."""
    try:
        return await asyncio.to_thread(lambda: write_fn(*args, kind=KIND))
    except WriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


def _ok(snapshot: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"ok": True, "commit": snapshot.get("commit"), **extra}


def _find(state: ServerState, section: str, entity_id: str, label: str) -> dict[str, Any]:
    for entry in state.get_snapshot_dict().get(section) or []:
        if isinstance(entry, dict) and entry.get("id") == entity_id:
            return entry
    raise HTTPException(status_code=404, detail=f"{label} '{entity_id}' not found")


def _project_summary(project: dict[str, Any]) -> dict[str, Any]:
    out = {k: project.get(k) for k in ("id", "name", "status")}
    if project.get("parent"):
        out["parent"] = project["parent"]
    return out


def create_agent_app(state: ServerState) -> FastAPI:
    """The agent API: only the routes below, every one requiring X-Workbench: 1."""
    app = FastAPI(
        title="Workbench agent API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(require_workbench_header)],
    )

    @app.get(f"{PREFIX}/index")
    async def get_index() -> dict[str, Any]:
        snapshot = state.get_snapshot_dict()
        return {
            "projects": [
                _project_summary(p) for p in snapshot.get("projects") or [] if isinstance(p, dict)
            ],
            "learning": [
                {k: path.get(k) for k in ("id", "name", "status")}
                for path in snapshot.get("learning") or []
                if isinstance(path, dict)
            ],
        }

    @app.get(f"{PREFIX}/projects/{{project_id}}")
    async def get_project(project_id: str) -> dict[str, Any]:
        return _find(state, "projects", project_id, "Project")

    @app.get(f"{PREFIX}/learning/{{path_id}}")
    async def get_learning_path(path_id: str) -> dict[str, Any]:
        return _find(state, "learning", path_id, "Learning path")

    @app.post(f"{PREFIX}/projects/{{project_id}}/items/{{item_id}}/status")
    async def post_project_item_status(
        project_id: str, item_id: str, request: Request
    ) -> dict[str, Any]:
        body = await _write_body(request, STATUS_KEYS, required=STATUS_KEYS)
        snapshot = await _call_write(
            update_project_item_status, state, project_id, item_id, body["status"]
        )
        return _ok(snapshot, item_id=item_id)

    @app.post(f"{PREFIX}/projects/{{project_id}}/items/{{item_id}}/fields")
    async def post_project_item_fields(
        project_id: str, item_id: str, request: Request
    ) -> dict[str, Any]:
        body = await _write_body(request, ITEM_FIELD_KEYS)
        snapshot = await _call_write(update_project_item_fields, state, project_id, item_id, body)
        return _ok(snapshot, item_id=item_id)

    @app.post(f"{PREFIX}/projects/{{project_id}}/items")
    async def post_project_item_add(project_id: str, request: Request) -> dict[str, Any]:
        body = await _write_body(request, ADD_ITEM_KEYS, required=("name",))
        snapshot, item_id = await _call_write(add_project_item, state, project_id, body)
        return _ok(snapshot, item_id=item_id)

    @app.post(f"{PREFIX}/projects/{{project_id}}/suggestions")
    async def post_suggestion(project_id: str, request: Request) -> dict[str, Any]:
        body = await _write_body(request, SUGGESTION_KEYS, required=("name",))
        snapshot, suggestion_id = await _call_write(add_suggestion, state, project_id, body)
        return _ok(snapshot, suggestion_id=suggestion_id)

    @app.post(f"{PREFIX}/learning/{{path_id}}/items/{{item_id}}/status")
    async def post_learning_item_status(
        path_id: str, item_id: str, request: Request
    ) -> dict[str, Any]:
        body = await _write_body(request, STATUS_KEYS, required=STATUS_KEYS)
        snapshot = await _call_write(
            update_learning_item_status, state, path_id, item_id, body["status"]
        )
        return _ok(snapshot, item_id=item_id)

    @app.post(f"{PREFIX}/learning/{{path_id}}/items/{{item_id}}/fields")
    async def post_learning_item_fields(
        path_id: str, item_id: str, request: Request
    ) -> dict[str, Any]:
        body = await _write_body(request, ITEM_FIELD_KEYS)
        snapshot = await _call_write(update_learning_item_fields, state, path_id, item_id, body)
        return _ok(snapshot, item_id=item_id)

    return app
