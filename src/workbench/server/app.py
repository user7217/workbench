"""FastAPI application serving the Workbench JSON API, background sync loop, and static UI (§10.2)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from workbench.discovery.github import run_github_discovery
from workbench.server.sync import ServerState
from workbench.server.writes import (
    WriteError,
    update_learning_item_status,
    update_learning_path_status,
    update_project_item_status,
    update_project_status,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_WEB_DIST = REPO_ROOT / "web" / "dist"


def _require_write_headers(request: Request) -> None:
    """Enforce X-Workbench: 1 and Content-Type: application/json on write endpoints (§10.2)."""
    if request.headers.get("x-workbench") != "1":
        raise HTTPException(status_code=403, detail="Missing or invalid X-Workbench header")
    ct = request.headers.get("content-type", "")
    if not ct.lower().startswith("application/json"):
        raise HTTPException(
            status_code=415, detail="Content-Type must be application/json"
        )


async def _read_status_body(request: Request) -> Any:
    """Return body["status"] from a JSON object body, or raise 400."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict) or "status" not in body:
        raise HTTPException(status_code=400, detail="Missing 'status' field in body")
    return body["status"]


async def _run_status_write(
    request: Request,
    state: ServerState,
    write_fn: Callable[..., dict[str, Any]],
    *ids: str,
) -> JSONResponse:
    """Shared handler for status write endpoints: headers, body, threaded write, error mapping."""
    _require_write_headers(request)
    new_status = await _read_status_body(request)
    try:
        updated_snapshot = await asyncio.to_thread(write_fn, state, *ids, new_status)
    except WriteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))
    return JSONResponse(
        content={
            "ok": True,
            "snapshot": updated_snapshot,
            "status": state.get_status_dict(),
        }
    )


def create_app(
    state: ServerState | None = None,
    poll_seconds: float | None = None,
    web_dist: Path | None = None,
    enable_background_loop: bool = True,
) -> FastAPI:
    """Create and configure the Workbench FastAPI server application."""
    if state is None:
        remote_url = os.environ.get("WORKBENCH_DATA_REMOTE", "")
        branch = os.environ.get("WORKBENCH_BRANCH", "main")
        state_dir = Path(os.environ.get("WORKBENCH_STATE_DIR", "/tmp/workbench-server-state"))
        owners_env = os.environ.get("WORKBENCH_GITHUB_OWNERS", "")
        owners = [o.strip() for o in owners_env.split(",") if o.strip()]
        gh_token = os.environ.get("WORKBENCH_GITHUB_READ_TOKEN") or None
        gh_base = os.environ.get("WORKBENCH_GITHUB_API_BASE", "https://api.github.com")
        state = ServerState(
            remote_url=remote_url,
            state_dir=state_dir,
            branch=branch,
            github_owners=owners,
            github_read_token=gh_token,
            github_api_base=gh_base,
        )

    interval = (
        poll_seconds
        if poll_seconds is not None
        else float(os.environ.get("WORKBENCH_POLL_SECONDS", "600"))
    )
    dist_dir = web_dist if web_dist is not None else DEFAULT_WEB_DIST

    @contextlib.asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        # Step 6 (§10.1): On startup, load snapshot.json if present, then sync immediately
        state.load_snapshot_from_disk()
        if state.remote_url:
            await asyncio.to_thread(state.sync_once)

        stop_event = asyncio.Event()
        bg_task: asyncio.Task | None = None

        def _run_discovery_locked() -> None:
            with state._repo_lock:
                run_github_discovery(
                    state.repo_dir,
                    state.github_owners,
                    state.github_read_token,
                    state.branch,
                    state.github_api_base,
                    state.work_root,
                    state.env,
                )

        async def _poll_loop() -> None:
            last_gh_discovery = 0.0
            loop = asyncio.get_running_loop()
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                    break
                except asyncio.TimeoutError:
                    pass
                if not state.remote_url:
                    continue
                # One failed iteration must never end the loop, or the server silently stops syncing.
                try:
                    await asyncio.to_thread(state.sync_once)
                    now = loop.time()
                    if state.github_owners and (now - last_gh_discovery) >= 86400:
                        last_gh_discovery = now
                        await asyncio.to_thread(_run_discovery_locked)
                        await asyncio.to_thread(state.sync_once)
                except Exception:
                    logger.exception("Workbench poll iteration failed; will retry next interval")

        if enable_background_loop and interval > 0:
            bg_task = asyncio.create_task(_poll_loop())

        try:
            yield
        finally:
            stop_event.set()
            if bg_task:
                bg_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bg_task

    # Note: No CORSMiddleware is added (§10.2: "no CORS is enabled")
    app = FastAPI(title="Workbench Server", lifespan=lifespan)
    app.state.workbench = state

    @app.get("/api/snapshot")
    async def get_snapshot() -> dict[str, Any]:
        return state.get_snapshot_dict()

    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return state.get_status_dict()

    @app.post("/api/sync")
    async def trigger_sync(request: Request) -> dict[str, Any]:
        _require_write_headers(request)
        ok = await asyncio.to_thread(state.sync_once, True)
        return {
            "ok": ok,
            "status": state.get_status_dict(),
        }

    @app.post("/api/projects/{project_id}/status")
    async def post_project_status(project_id: str, request: Request) -> JSONResponse:
        return await _run_status_write(request, state, update_project_status, project_id)

    @app.post("/api/projects/{project_id}/items/{item_id}/status")
    async def post_project_item_status(
        project_id: str, item_id: str, request: Request
    ) -> JSONResponse:
        return await _run_status_write(
            request, state, update_project_item_status, project_id, item_id
        )

    @app.post("/api/learning/{path_id}/status")
    async def post_learning_path_status(path_id: str, request: Request) -> JSONResponse:
        return await _run_status_write(request, state, update_learning_path_status, path_id)

    @app.post("/api/learning/{path_id}/items/{item_id}/status")
    async def post_learning_item_status(
        path_id: str, item_id: str, request: Request
    ) -> JSONResponse:
        return await _run_status_write(
            request, state, update_learning_item_status, path_id, item_id
        )

    # Serve built UI static files if web/dist exists
    if dist_dir.exists() and (dist_dir / "index.html").exists():
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}")
        async def serve_spa(full_path: str) -> FileResponse:
            candidate = (dist_dir / full_path).resolve()
            if (
                full_path
                and candidate.is_file()
                and dist_dir.resolve() in candidate.parents
            ):
                return FileResponse(str(candidate))
            return FileResponse(str(dist_dir / "index.html"))

    return app


app = create_app()
