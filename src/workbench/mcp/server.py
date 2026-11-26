"""The MCP server: a few small tools over the agent API, behind the GitHub login.

`build_server` makes the FastMCP server (tests use it without auth); `create_app` adds the auth
and returns the ASGI app; `main` reads the environment and runs it, refusing to start when the
auth settings are incomplete.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthProvider
from pydantic import Field

from workbench import __version__
from workbench.mcp.client import AgentApiError, AgentClient
from workbench.mcp.config import MCP_PATH, ConfigError, McpConfig, load_config
from workbench.mcp.outline import format_index, format_learning_path, format_project
from workbench.models import SLUG_PATTERN

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Workbench is the owner's dashboard of software projects and learning paths. These tools read it
and record progress from this chat: an item's status, its current notes, a new item, or a
suggestion for the owner to review. Look up ids with list_projects and show_project (or
show_learning_path) first and use them exactly as printed. Record only what the conversation
actually established, make the smallest change that says it, and never put secrets, local file
paths, email addresses or personal information into names, summaries or notes."""

Id = Annotated[str, Field(pattern=SLUG_PATTERN, max_length=48)]
Name = Annotated[
    str, Field(min_length=1, max_length=80, description="Short name, at most 80 characters")
]
Summary = Annotated[
    str | None,
    Field(max_length=300, description="One line on what the item is, at most 300 characters"),
]
Notes = Annotated[
    str | None,
    Field(
        max_length=1000,
        description=(
            "The item's current state (what works, what is blocking, the next step), at most "
            "1000 characters. Replaces the existing notes; an empty string clears them."
        ),
    ),
]
ProjectStatus = Literal["not_started", "in_progress", "done", "parked"]
LearningStatus = Literal["not_started", "in_progress", "done"]

_READ = {"readOnlyHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}


def _committed(result: dict[str, Any]) -> str:
    commit = result.get("commit")
    return f"commit {str(commit)[:7]}" if commit else "nothing changed, so no commit"


def _call(fn: Callable[[], Any]) -> Any:
    """Run an agent API call, turning its errors into tool errors the model can read."""
    try:
        return fn()
    except AgentApiError as exc:
        raise ToolError(str(exc)) from exc


def build_server(client: AgentClient, auth: AuthProvider | None = None) -> FastMCP:
    """The FastMCP server with the Workbench tools. Production always passes `auth`."""
    mcp = FastMCP(
        "Workbench",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=auth,
        mask_error_details=True,
    )

    @mcp.tool(annotations=_READ)
    def list_projects() -> str:
        """List the dashboard's projects and learning paths: id, name and status of each.

        Projects that are part of a bigger project show their parent. Start here to find the
        id of the project or learning path the conversation is about.
        """
        return format_index(_call(client.index))

    @mcp.tool(annotations=_READ)
    def show_project(project_id: Id) -> str:
        """Show one project as an outline: its phases (or topics) and the items under them.

        Each line gives an item's status, name and id, then a short summary, its notes and
        what it depends on. Items nest: a part of an item is indented under it. Use the ids
        from this outline with the other tools.
        """
        return format_project(_call(lambda: client.project(project_id)))

    @mcp.tool(annotations=_READ)
    def show_learning_path(path_id: Id) -> str:
        """Show one learning path as an outline: its topics and subtopics with status and id."""
        return format_learning_path(_call(lambda: client.learning(path_id)))

    @mcp.tool(annotations={**_WRITE, "idempotentHint": True})
    def set_item_status(
        project_id: Id, item_id: Id, status: ProjectStatus, notes: Notes = None
    ) -> str:
        """Set the status of one project item, and optionally replace its notes in the same call.

        Statuses: done only when it is built and working (tests pass or the owner saw it work);
        in_progress when partly built; not_started for planned work; parked when the owner put
        it on hold. Set the item that was worked on, not its parents: a parent becomes done by
        itself when its last part does.
        """
        result = _call(lambda: client.set_item_status(project_id, item_id, status))
        message = f"Set {project_id}/{item_id} to {status} ({_committed(result)})."
        if notes is None:
            return message
        try:
            fields = client.set_item_fields(project_id, item_id, notes=notes, summary=None)
        except AgentApiError as exc:
            raise ToolError(f"{message} But the notes were not saved: {exc}") from exc
        return f"{message} Notes updated ({_committed(fields)})."

    @mcp.tool(annotations={**_WRITE, "idempotentHint": True})
    def update_item_notes(
        project_id: Id, item_id: Id, notes: Notes = None, summary: Summary = None
    ) -> str:
        """Replace a project item's notes (its current state) and/or its one-line summary.

        Use notes for what changed: what now works, what is failing or blocking, the next step.
        Leave a field out to keep it as it is.
        """
        if notes is None and summary is None:
            raise ToolError("Nothing to update: give notes, summary or both.")
        result = _call(lambda: client.set_item_fields(project_id, item_id, notes, summary))
        return f"Updated {project_id}/{item_id} ({_committed(result)})."

    @mcp.tool(annotations=_WRITE)
    def add_item(
        project_id: Id,
        name: Name,
        parent_id: Annotated[
            str | None,
            Field(
                pattern=SLUG_PATTERN,
                max_length=48,
                description=(
                    "Id of the phase, topic or item this belongs under. Leave it out only to "
                    "create a new top-level phase (or topic)."
                ),
            ),
        ] = None,
        summary: Summary = None,
        status: ProjectStatus = "not_started",
    ) -> str:
        """Add an item to a project: a problem the conversation found or a next step the owner
        asked for or agreed to.

        Build the tree rather than a flat list: put a sub-task under the item it belongs to
        (parts of parts are fine). For your own ideas the owner has not agreed to, use
        suggest_item instead.
        """
        result = _call(lambda: client.add_item(project_id, name, parent_id, summary, status))
        where = f"under {parent_id}" if parent_id else "as a new top-level group"
        item_id = result.get("item_id", "?")
        return f"Added {name!r} to {project_id} {where} with id {item_id} ({_committed(result)})."

    @mcp.tool(annotations=_WRITE)
    def suggest_item(
        project_id: Id,
        name: Name,
        summary: Summary = None,
        under: Annotated[
            str | None,
            Field(
                pattern=SLUG_PATTERN,
                max_length=48,
                description="Id of the item it would belong under, if any",
            ),
        ] = None,
    ) -> str:
        """File a suggested next step in the project's inbox; the owner keeps or dismisses it.

        Use this for ideas the owner has not agreed to yet; it does not change the project tree.
        """
        result = _call(lambda: client.suggest(project_id, name, summary, under))
        return f"Filed {name!r} in the inbox of {project_id} ({_committed(result)})."

    @mcp.tool(annotations={**_WRITE, "idempotentHint": True})
    def set_learning_item_status(path_id: Id, item_id: Id, status: LearningStatus) -> str:
        """Set the status of a topic or subtopic in a learning path.

        done when the owner has learned it (can explain or use it); in_progress while studying;
        not_started otherwise.
        """
        result = _call(lambda: client.set_learning_status(path_id, item_id, status))
        return f"Set {path_id}/{item_id} to {status} ({_committed(result)})."

    @mcp.tool(annotations={**_WRITE, "idempotentHint": True})
    def update_learning_item_notes(
        path_id: Id, item_id: Id, notes: Notes = None, summary: Summary = None
    ) -> str:
        """Replace a learning topic's or subtopic's notes and/or its one-line summary.

        Notes hold what the owner now understands and what is still unclear. Leave a field out
        to keep it as it is.
        """
        if notes is None and summary is None:
            raise ToolError("Nothing to update: give notes, summary or both.")
        result = _call(lambda: client.set_learning_fields(path_id, item_id, notes, summary))
        return f"Updated {path_id}/{item_id} ({_committed(result)})."

    return mcp


def create_app(config: McpConfig) -> Any:
    """The ASGI app: Streamable HTTP at MCP_PATH plus the OAuth endpoints, all behind the login."""
    from workbench.mcp.auth import build_auth

    client = AgentClient(config.agent_socket)
    server = build_server(client, auth=build_auth(config))
    # Stateless: no MCP session lives in memory, so a restart does not break a connected chat
    return server.http_app(path=MCP_PATH, stateless_http=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        config = load_config()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None

    import uvicorn

    app = create_app(config)
    logger.info("Workbench MCP server: %s (agent API at %s)", config.mcp_url, config.agent_socket)
    uvicorn.run(app, host=config.host, port=config.port, proxy_headers=False, server_header=False)
