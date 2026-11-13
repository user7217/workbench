"""Status and text-field writes for projects, learning paths and their items."""

from __future__ import annotations

from typing import Any

from workbench import edits
from workbench.commitmsg import describe_item_change
from workbench.edits import field_order as _field_order
from workbench.edits import require_slug as _require_slug
from workbench.edits import require_status as _require_status
from workbench.models import LearningPath, Project
from workbench.server.sync import ServerState
from workbench.server.writes._transaction import (
    _LEARNING_ITEM_STATUSES,
    _PHASE_STATUSES,
    _PROJECT_STATUSES,
    WriteError,
    _commit_edit,
    _edit_message,
    _reject_unknown_keys,
    _require_body,
)

# ---------------------------------------------------------------------------
# Status edits
# ---------------------------------------------------------------------------


def update_project_item_status(
    state: ServerState, project_id: str, item_id: str, new_status: str
) -> dict[str, Any]:
    """Set the status of a phase or (nested) component in projects/<id>/project.yaml."""
    _require_slug(project_id, "project id")
    _require_slug(item_id, "item id")
    status = _require_status(new_status, _PHASE_STATUSES)

    rolled: edits.RollUp = []

    def mutate(doc: Any) -> None:
        rolled[:] = edits.set_project_item_status(doc, project_id, item_id, status)

    def message() -> str:
        summary = describe_item_change(status=status) + edits.describe_roll_up(rolled)
        return _edit_message(summary, f"{project_id}/{item_id}")

    return _commit_edit(state, "project", project_id, mutate, message)


def update_project_status(state: ServerState, project_id: str, new_status: str) -> dict[str, Any]:
    """Set a project's top-level status (archive/unarchive, plan/activate a child project)."""
    _require_slug(project_id, "project id")
    status = _require_status(new_status, _PROJECT_STATUSES)

    def mutate(doc: Any) -> None:
        doc["status"] = status

    return _commit_edit(
        state, "project", project_id, mutate, _edit_message(f"status {status}", project_id)
    )


def update_learning_path_status(
    state: ServerState, path_id: str, new_status: str
) -> dict[str, Any]:
    """Set a learning path's top-level status."""
    _require_slug(path_id, "learning path id")
    status = _require_status(new_status, _PROJECT_STATUSES)

    def mutate(doc: Any) -> None:
        doc["status"] = status

    return _commit_edit(
        state, "learning", path_id, mutate, _edit_message(f"status {status}", f"learning/{path_id}")
    )


def update_learning_item_status(
    state: ServerState, path_id: str, item_id: str, new_status: str
) -> dict[str, Any]:
    """Set the status of a topic or subtopic in learning/<path_id>/path.yaml."""
    _require_slug(path_id, "learning path id")
    _require_slug(item_id, "item id")
    status = _require_status(new_status, _LEARNING_ITEM_STATUSES)

    rolled: edits.RollUp = []

    def mutate(doc: Any) -> None:
        rolled[:] = edits.set_learning_item_status(doc, path_id, item_id, status)

    def message() -> str:
        summary = f"status {status}" + edits.describe_roll_up(rolled)
        return _edit_message(summary, f"learning/{path_id}/{item_id}")

    return _commit_edit(state, "learning", path_id, mutate, message)


# ---------------------------------------------------------------------------
# Text field edits
# ---------------------------------------------------------------------------

_PROJECT_FIELDS: tuple[str, ...] = ("name", "description")
_PROJECT_ITEM_FIELDS: tuple[str, ...] = ("name", "summary", "notes", "description", "items")
_LEARNING_FIELDS: tuple[str, ...] = ("name", "description")
_LEARNING_ITEM_FIELDS: tuple[str, ...] = ("name", "summary", "notes", "description")


def _parse_text_fields(body: object, allowed: tuple[str, ...]) -> dict[str, Any]:
    """Check a fields body: only allowed keys, strings (items: list of strings), at least one key."""
    fields = _require_body(body)
    _reject_unknown_keys(fields, allowed)
    if not fields:
        raise WriteError("No fields to update", status_code=400)
    for key, value in fields.items():
        if key == "items":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise WriteError("'items' must be a list of strings", status_code=400)
        elif not isinstance(value, str):
            raise WriteError(f"'{key}' must be a string", status_code=400)
    return fields


def update_project_fields(state: ServerState, project_id: str, body: object) -> dict[str, Any]:
    """Edit a project's name/description."""
    _require_slug(project_id, "project id")
    fields = _parse_text_fields(body, _PROJECT_FIELDS)

    def mutate(doc: Any) -> None:
        edits.apply_text_fields(doc, fields, _field_order(Project))

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        _edit_message(describe_item_change(fields=fields), project_id),
        allow_text_removal=True,
    )


def update_project_item_fields(
    state: ServerState, project_id: str, item_id: str, body: object
) -> dict[str, Any]:
    """Edit text fields of a phase/topic group or a component at any depth."""
    _require_slug(project_id, "project id")
    _require_slug(item_id, "item id")
    fields = _parse_text_fields(body, _PROJECT_ITEM_FIELDS)

    def mutate(doc: Any) -> None:
        edits.set_project_item_fields(doc, project_id, item_id, fields)

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        _edit_message(describe_item_change(fields=fields), f"{project_id}/{item_id}"),
        allow_text_removal=True,
    )


def update_learning_fields(state: ServerState, path_id: str, body: object) -> dict[str, Any]:
    """Edit a learning path's name/description."""
    _require_slug(path_id, "learning path id")
    fields = _parse_text_fields(body, _LEARNING_FIELDS)

    def mutate(doc: Any) -> None:
        edits.apply_text_fields(doc, fields, _field_order(LearningPath))

    return _commit_edit(
        state,
        "learning",
        path_id,
        mutate,
        _edit_message(describe_item_change(fields=fields), f"learning/{path_id}"),
        allow_text_removal=True,
    )


def update_learning_item_fields(
    state: ServerState, path_id: str, item_id: str, body: object
) -> dict[str, Any]:
    """Edit text fields of a learning topic or subtopic."""
    _require_slug(path_id, "learning path id")
    _require_slug(item_id, "item id")
    fields = _parse_text_fields(body, _LEARNING_ITEM_FIELDS)

    def mutate(doc: Any) -> None:
        edits.set_learning_item_fields(doc, path_id, item_id, fields)

    return _commit_edit(
        state,
        "learning",
        path_id,
        mutate,
        _edit_message(describe_item_change(fields=fields), f"learning/{path_id}/{item_id}"),
        allow_text_removal=True,
    )
