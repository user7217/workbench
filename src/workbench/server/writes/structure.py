"""Project structure edits: add, move, delete, parent link."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workbench import edits
from workbench.edits import check_position as _check_position
from workbench.edits import child_list as _child_list
from workbench.edits import field_order as _field_order
from workbench.edits import iter_component_nodes as _iter_component_nodes
from workbench.edits import locate_project_item as _locate_project_item
from workbench.edits import require_position as _require_position
from workbench.edits import require_slug as _require_slug
from workbench.edits import require_status as _require_status
from workbench.edits import subtree_ids as _subtree_ids
from workbench.models import Component, Phase
from workbench.server.sync import ServerState
from workbench.server.writes._transaction import (
    _PHASE_STATUSES,
    WriteError,
    _commit_edit,
    _edit_message,
    _reject_unknown_keys,
    _require_body,
)
from workbench.validate import validate_project_file
from workbench.yamlio import load_yaml_roundtrip, to_plain_python


def _parent_id(loc: edits.Location, doc: Any) -> str | None:
    """Id of the phase/component holding an item, or None for a phase/topic of the project."""
    if loc.owner is doc:
        return None
    owner_id = loc.owner.get("id")
    return owner_id if isinstance(owner_id, str) else None


def _drop_if_empty(loc: edits.Location, doc: Any) -> None:
    """Remove an emptied nested list key (top-level phases/topics stay)."""
    if loc.owner is not doc and isinstance(loc.seq, list) and not loc.seq:
        loc.owner.pop(loc.key, None)


_ADD_KEYS: tuple[str, ...] = ("parent_id", "name", "id", "status", "summary", "position")


def add_project_item(
    state: ServerState, project_id: str, body: object
) -> tuple[dict[str, Any], str]:
    """Add a group (parent_id null) or a component under a group/component. Returns (snapshot, new id)."""
    _require_slug(project_id, "project id")
    data = _require_body(body)
    _reject_unknown_keys(data, _ADD_KEYS)
    # Check the request before touching git; edits.add_project_item repeats these checks.
    parent_id = data.get("parent_id")
    if parent_id is not None:
        _require_slug(parent_id, "parent_id")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WriteError("'name' is required and must be a non-empty string", status_code=400)
    explicit_id = data.get("id")
    if explicit_id is not None:
        _require_slug(explicit_id, "id")
    status = _require_status(data.get("status", "not_started"), _PHASE_STATUSES)
    summary = data.get("summary", "")
    if not isinstance(summary, str):
        raise WriteError("'summary' must be a string", status_code=400)
    position = data.get("position")
    if position is not None:
        position = _require_position(position)

    created: dict[str, str] = {}

    def mutate(doc: Any) -> None:
        new_id, label = edits.add_project_item(
            doc,
            project_id,
            parent_id=parent_id,
            name=name,
            item_id=explicit_id,
            status=status,
            summary=summary,
            position=position,
        )
        created.update(id=new_id, label=label)

    def message() -> str:
        scope = f"{project_id}/{parent_id}" if parent_id else project_id
        return _edit_message(f"add {created['label']} {created['id']}", scope)

    snapshot = _commit_edit(state, "project", project_id, mutate, message)
    return snapshot, created["id"]


def move_project_item(
    state: ServerState, project_id: str, item_id: str, body: object
) -> dict[str, Any]:
    """Reorder an item in its list, or move a component under another group/component."""
    _require_slug(project_id, "project id")
    _require_slug(item_id, "item id")
    data = _require_body(body)
    _reject_unknown_keys(data, ("parent_id", "position"))
    if "position" not in data:
        raise WriteError("'position' is required", status_code=400)
    position = _require_position(data["position"])
    reparent = "parent_id" in data
    parent_id = data.get("parent_id")
    if parent_id is not None:
        _require_slug(parent_id, "parent_id")

    moved_under: dict[str, str] = {}

    def mutate(doc: Any) -> None:
        loc = edits.require_project_item(doc, project_id, item_id)
        target_seq = loc.seq
        if loc.is_group:
            if reparent and parent_id is not None:
                raise WriteError("Phases/topics can only be reordered among groups", 400)
        elif reparent:
            if parent_id is None:
                raise WriteError("A component must sit under a phase/topic or component", 400)
            if parent_id in _subtree_ids(loc.node):
                raise WriteError(
                    f"Cannot move '{item_id}' into itself or one of its own components", 400
                )
            parent = _locate_project_item(doc, parent_id)
            if parent is None:
                raise WriteError(
                    f"Parent item '{parent_id}' not found in project '{project_id}'",
                    status_code=404,
                )
            existing = parent.node.get("components")
            if existing is not loc.seq:
                order = _field_order(Phase if parent.is_group else Component)
                target_seq = _child_list(parent.node, order)
                moved_under["parent"] = parent_id

        same_list = target_seq is loc.seq
        limit = len(target_seq) - 1 if same_list else len(target_seq)
        _check_position(position, limit)
        old_parent = _parent_id(loc, doc)
        loc.seq.pop(loc.index)
        target_seq.insert(position, loc.node)
        if not same_list:
            _drop_if_empty(loc, doc)
            # The new parent gained a part and the old one lost one: both follow their parts
            edits.roll_up_project(doc, item_id)
            if old_parent is not None:
                edits.roll_up_project_from(doc, old_parent)

    def message() -> str:
        scope = f"{project_id}/{item_id}"
        if "parent" in moved_under:
            return _edit_message(f"move under {moved_under['parent']}", scope)
        return _edit_message(f"move to position {position}", scope)

    return _commit_edit(state, "project", project_id, mutate, message)


def _project_files(wt_dir: Path) -> dict[str, Path]:
    projects_dir = wt_dir / "projects"
    if not projects_dir.is_dir():
        return {}
    return {
        p.name: p / "project.yaml"
        for p in sorted(projects_dir.iterdir())
        if (p / "project.yaml").is_file()
    }


def _load_plain(path: Path) -> dict[str, Any]:
    try:
        data = to_plain_python(load_yaml_roundtrip(path))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def delete_project_item(
    state: ServerState, project_id: str, item_id: str, body: object
) -> dict[str, Any]:
    """Permanently remove a group/component and everything nested in it (needs confirm: true)."""
    _require_slug(project_id, "project id")
    _require_slug(item_id, "item id")
    data = _require_body(body)
    _reject_unknown_keys(data, ("confirm",))
    if data.get("confirm") is not True:
        raise WriteError('Deleting requires {"confirm": true}', status_code=400)

    removed: set[str] = set()

    def mutate(doc: Any) -> None:
        loc = edits.require_project_item(doc, project_id, item_id)
        ids = _subtree_ids(loc.node)
        parent = _parent_id(loc, doc)
        loc.seq.pop(loc.index)
        _drop_if_empty(loc, doc)
        if parent is not None:
            # Removing the last open part finishes the parent
            edits.roll_up_project_from(doc, parent)
        refs = sorted(
            f"{node.get('id')} -> {dep}"
            for node in _iter_component_nodes(doc)
            for dep in (node.get("depends_on") or [])
            if dep in ids
        )
        if refs:
            raise WriteError(
                f"Cannot delete '{item_id}': still referenced by depends_on ({', '.join(refs)})",
                status_code=422,
            )
        removed.update(ids)

    def check_children(wt_dir: Path) -> None:
        blocked = sorted(
            f"{pid} (under {plain.get('under')})"
            for pid, path in _project_files(wt_dir).items()
            if pid != project_id
            for plain in [_load_plain(path)]
            if plain.get("parent") == project_id and plain.get("under") in removed
        )
        if blocked:
            raise WriteError(
                f"Cannot delete '{item_id}': child project(s) sit under it: {', '.join(blocked)}",
                status_code=422,
            )

    def message() -> str:
        nested = len(removed) - 1
        extra = f" (and {nested} nested item(s))" if nested > 0 else ""
        return _edit_message(f"delete{extra}", f"{project_id}/{item_id}")

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        message,
        allow_removals=True,
        check_repo=check_children,
    )


def set_project_parent(state: ServerState, project_id: str, body: object) -> dict[str, Any]:
    """Attach a project under parent/under, or clear the link when both are null."""
    _require_slug(project_id, "project id")
    data = _require_body(body)
    _reject_unknown_keys(data, ("parent", "under"))
    missing = [k for k in ("parent", "under") if k not in data]
    if missing:
        raise WriteError(f"Missing field(s): {', '.join(missing)}", status_code=400)
    parent, under = data["parent"], data["under"]
    if (parent is None) != (under is None):
        raise WriteError("'parent' and 'under' must both be set or both be null", 400)
    if parent is not None:
        _require_slug(parent, "parent")
        _require_slug(under, "under")
        if parent == project_id:
            raise WriteError(f"Project '{project_id}' cannot be its own parent", 400)

    previous: dict[str, Any] = {}

    def mutate(doc: Any) -> None:
        previous["parent"] = doc.get("parent")
        if parent is None:
            doc.pop("parent", None)
            doc.pop("under", None)
        elif "parent" in doc:
            doc["parent"] = parent
            doc["under"] = under
        else:
            keys = list(doc.keys())
            pos = keys.index("status") + 1 if "status" in keys else len(keys)
            doc.insert(pos, "parent", parent)
            doc.insert(pos + 1, "under", under)

    def check_link(wt_dir: Path) -> None:
        if parent is None:
            return
        files = _project_files(wt_dir)
        if parent not in files:
            raise WriteError(f"Parent project '{parent}' does not exist", status_code=422)
        _, parent_proj, _ = validate_project_file(files[parent], expected_dir_id=parent)
        if parent_proj is None:
            raise WriteError(f"Parent project '{parent}' is invalid", status_code=422)
        item_ids = [g.id for g in list(parent_proj.phases) + list(parent_proj.topics)]
        item_ids += [c.id for c in parent_proj.all_components()]
        if under not in item_ids:
            raise WriteError(
                f"'{under}' is not a phase or component of project '{parent}' "
                f"(available: {', '.join(item_ids) or 'none'})",
                status_code=422,
            )
        chain = [project_id, parent]
        current = parent_proj.parent
        while current is not None and current not in chain[1:]:
            if current == project_id:
                break
            chain.append(current)
            current = _load_plain(files[current]).get("parent") if current in files else None
        if current == project_id:
            raise WriteError(
                f"Attaching would create a parent cycle: {' -> '.join(chain + [project_id])}",
                status_code=422,
            )

    def message() -> str:
        if parent is not None:
            return _edit_message(f"attach under {parent}/{under}", project_id)
        if previous.get("parent"):
            return _edit_message(f"detach from {previous['parent']}", project_id)
        return _edit_message("clear parent", project_id)

    return _commit_edit(state, "project", project_id, mutate, message, check_repo=check_link)
