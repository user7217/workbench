"""Client for the dashboard's restricted agent API, served on a Unix socket.

The MCP server has no other way to the dashboard. Every id is checked against the slug pattern
before it goes into a URL, so a tool argument cannot reach any other route.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from workbench.models import SLUG_PATTERN

logger = logging.getLogger(__name__)

_SLUG = re.compile(SLUG_PATTERN)
_BASE_URL = "http://agent"
_PREFIX = "/agent/v1"
# Writes commit (and may push) on the dashboard side, so give them time
_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


class AgentApiError(Exception):
    """The dashboard refused a request, or could not be reached. `str()` is meant for the model."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _slug(value: str, what: str) -> str:
    if not isinstance(value, str) or len(value) > 48 or not _SLUG.fullmatch(value):
        raise AgentApiError(
            f"Invalid {what} {value!r}: ids are lowercase letters, digits and single hyphens "
            "(use the ids exactly as list_projects / show_project print them)."
        )
    return value


def _detail(response: httpx.Response) -> str:
    """The dashboard's error message: FastAPI's `detail`, a string or a list of field errors."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:300] or response.reason_phrase
    detail = body.get("detail") if isinstance(body, dict) else body
    if isinstance(detail, list):
        parts = []
        for err in detail:
            if isinstance(err, dict):
                loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
                msg = str(err.get("msg", err))
                parts.append(f"{loc}: {msg}" if loc else msg)
            else:
                parts.append(str(err))
        return "; ".join(parts)
    return str(detail) if detail is not None else response.reason_phrase


_STATUS_PREFIX = {
    400: "Rejected",
    403: "Not allowed",
    404: "Not found",
    409: "Conflict",
    422: "Invalid input",
}


class AgentClient:
    """Calls the agent API. Pass `transport` in tests; by default it talks to `socket_path`."""

    def __init__(
        self, socket_path: Path | str | None = None, transport: httpx.BaseTransport | None = None
    ) -> None:
        if transport is None:
            if socket_path is None:
                raise ValueError("socket_path or transport is required")
            transport = httpx.HTTPTransport(uds=str(socket_path))
        self._http = httpx.Client(
            transport=transport,
            base_url=_BASE_URL,
            headers={"X-Workbench": "1"},
            timeout=_TIMEOUT,
        )

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._http.request(method, _PREFIX + path, json=body)
        except httpx.HTTPError as exc:
            logger.warning("agent API %s %s failed: %r", method, path, exc)
            raise AgentApiError(
                "The Workbench dashboard is not reachable right now; nothing was changed. "
                "Try again later."
            ) from exc
        if response.is_success:
            try:
                return response.json()
            except ValueError as exc:
                raise AgentApiError("The dashboard sent an unreadable response.") from exc
        prefix = _STATUS_PREFIX.get(response.status_code, f"Dashboard error {response.status_code}")
        raise AgentApiError(f"{prefix}: {_detail(response)}", status=response.status_code)

    # reads

    def index(self) -> dict[str, Any]:
        return self._request("GET", "/index")

    def project(self, project_id: str) -> dict[str, Any]:
        return self._request("GET", f"/projects/{_slug(project_id, 'project id')}")

    def learning(self, path_id: str) -> dict[str, Any]:
        return self._request("GET", f"/learning/{_slug(path_id, 'learning path id')}")

    # project writes

    def set_item_status(self, project_id: str, item_id: str, status: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/projects/{_slug(project_id, 'project id')}/items/{_slug(item_id, 'item id')}/status",
            {"status": status},
        )

    def set_item_fields(
        self, project_id: str, item_id: str, notes: str | None, summary: str | None
    ) -> dict[str, Any]:
        body = {k: v for k, v in (("notes", notes), ("summary", summary)) if v is not None}
        return self._request(
            "POST",
            f"/projects/{_slug(project_id, 'project id')}/items/{_slug(item_id, 'item id')}/fields",
            body,
        )

    def add_item(
        self,
        project_id: str,
        name: str,
        parent_id: str | None,
        summary: str | None,
        status: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "parent_id": _slug(parent_id, "parent id") if parent_id is not None else None,
            "name": name,
            "status": status,
        }
        if summary:
            body["summary"] = summary
        return self._request("POST", f"/projects/{_slug(project_id, 'project id')}/items", body)

    def suggest(
        self, project_id: str, name: str, summary: str | None, under: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if summary:
            body["summary"] = summary
        if under is not None:
            body["under"] = _slug(under, "item id")
        return self._request(
            "POST", f"/projects/{_slug(project_id, 'project id')}/suggestions", body
        )

    # learning path writes

    def set_learning_status(self, path_id: str, item_id: str, status: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/learning/{_slug(path_id, 'learning path id')}/items/{_slug(item_id, 'item id')}/status",
            {"status": status},
        )

    def set_learning_fields(
        self, path_id: str, item_id: str, notes: str | None, summary: str | None
    ) -> dict[str, Any]:
        body = {k: v for k, v in (("notes", notes), ("summary", summary)) if v is not None}
        return self._request(
            "POST",
            f"/learning/{_slug(path_id, 'learning path id')}/items/{_slug(item_id, 'item id')}/fields",
            body,
        )
