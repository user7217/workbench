"""Suggestions inbox: keep (add to the tree) or dismiss."""

from __future__ import annotations

from typing import Any

from workbench import edits
from workbench.edits import require_slug as _require_slug
from workbench.server.sync import ServerState
from workbench.server.writes._transaction import (
    _commit_edit,
    _edit_message,
    _reject_unknown_keys,
    _require_body,
)


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
