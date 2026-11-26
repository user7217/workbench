"""Suggestions inbox: file a suggestion, keep it (add to the tree) or dismiss it."""

from __future__ import annotations

import datetime
from typing import Any

from workbench import edits
from workbench.edits import require_slug as _require_slug
from workbench.server.sync import ServerState
from workbench.server.writes._transaction import (
    WriteError,
    WriteKind,
    _commit_edit,
    _edit_message,
    _reject_unknown_keys,
    _require_body,
)

_ADD_SUGGESTION_KEYS: tuple[str, ...] = ("name", "summary", "under")


def add_suggestion(
    state: ServerState, project_id: str, body: object, *, kind: WriteKind = "edit"
) -> tuple[dict[str, Any], str]:
    """File a suggestion in the project's inbox. Returns (snapshot, new suggestion id).

    Body: {"name", "summary"?, "under"?}; `under` must name an existing phase/topic/component
    (null or absent means the suggestion would become a new phase/topic when kept).
    """
    _require_slug(project_id, "project id")
    data = _require_body(body)
    _reject_unknown_keys(data, _ADD_SUGGESTION_KEYS)
    # Check the request before touching git; edits.add_suggestion repeats the name check.
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WriteError("'name' is required and must be a non-empty string", status_code=400)
    summary = data.get("summary", "")
    if not isinstance(summary, str):
        raise WriteError("'summary' must be a string", status_code=400)
    under = data.get("under")
    if under is not None:
        _require_slug(under, "under")

    created: dict[str, str] = {}

    def mutate(doc: Any) -> None:
        created["id"] = edits.add_suggestion(
            doc,
            project_id,
            name=name,
            summary=summary,
            under=under,
            added=datetime.date.today().isoformat(),
        )

    snapshot = _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        lambda: _edit_message(f"suggest {created['id']}", project_id, kind),
    )
    return snapshot, created["id"]


def keep_suggestion(
    state: ServerState, project_id: str, suggestion_id: str, body: object
) -> tuple[dict[str, Any], str]:
    """Move a suggestion into the tree as a not-started item. Returns (snapshot, new item id)."""
    _require_slug(project_id, "project id")
    _require_slug(suggestion_id, "suggestion id")
    _reject_unknown_keys(_require_body(body), ())

    created: dict[str, str] = {}

    def mutate(doc: Any) -> None:
        new_id, label = edits.keep_suggestion(doc, project_id, suggestion_id)
        created.update(id=new_id, label=label)

    snapshot = _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        _edit_message(f"keep suggestion {suggestion_id}", project_id),
    )
    return snapshot, created["id"]


def dismiss_suggestion(
    state: ServerState, project_id: str, suggestion_id: str, body: object
) -> dict[str, Any]:
    """Drop a suggestion from the project's inbox."""
    _require_slug(project_id, "project id")
    _require_slug(suggestion_id, "suggestion id")
    _reject_unknown_keys(_require_body(body), ())

    def mutate(doc: Any) -> None:
        edits.dismiss_suggestion(doc, project_id, suggestion_id)

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        _edit_message(f"dismiss suggestion {suggestion_id}", project_id),
    )
