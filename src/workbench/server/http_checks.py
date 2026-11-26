"""Request checks shared by the dashboard API and the agent API (workbench.server.agent_api)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request


def require_workbench_header(request: Request) -> None:
    """Enforce X-Workbench: 1 (403 otherwise)."""
    if request.headers.get("x-workbench") != "1":
        raise HTTPException(status_code=403, detail="Missing or invalid X-Workbench header")


def require_write_headers(request: Request) -> None:
    """Enforce X-Workbench: 1 and Content-Type: application/json on write endpoints."""
    require_workbench_header(request)
    ct = request.headers.get("content-type", "")
    if not ct.lower().startswith("application/json"):
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")


async def read_json_object(request: Request) -> dict[str, Any]:
    """Return the request body as a JSON object, or raise 400."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body
