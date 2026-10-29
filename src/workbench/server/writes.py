"""Dashboard write path on the server: status edits committed to the data repo (§10.2).

Every edit goes through `_commit_edit`, which holds the repo lock for the whole
operation, edits one YAML file in a fresh worktree cut from origin/<branch>,
validates it (structure, privacy, semantic diff against origin), commits, and
pushes with up to three fetch/rebase/validate/push attempts before resyncing.
"""

from __future__ import annotations

import datetime
import secrets
from pathlib import Path
from typing import Any, Callable, Literal, get_args

from workbench import edits
from workbench.commitmsg import build_message, describe_item_change
from workbench.edits import (
    EditError,
    child_list as _child_list,
    collect_item_ids as _collect_item_ids,
    field_order as _field_order,
    iter_component_nodes as _iter_component_nodes,
    locate_project_item as _locate_project_item,
    require_position as _require_position,
    check_position as _check_position,
    require_slug as _require_slug,
    require_status as _require_status,
    subtree_ids as _subtree_ids,
)
from workbench.gitops import (
    commit_all,
    create_worktree,
    fetch_origin,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
    show_file_at_ref,
)
from workbench.models import (
    Component,
    LearningItemStatus,
    LearningPath,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
)
from workbench.semantic import check_semantic_diff
from workbench.server.sync import ServerState
from workbench.validate import validate_data_repo, validate_learning_file, validate_project_file
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    parse_iso_date_for_yaml,
    to_plain_python,
)

# Kept for existing imports; the learning item status set is LearningItemStatus.
AllowedLearningStatus = LearningItemStatus

_PROJECT_STATUSES: tuple[str, ...] = get_args(ProjectStatus)
_PHASE_STATUSES: tuple[str, ...] = get_args(PhaseStatus)
_LEARNING_ITEM_STATUSES: tuple[str, ...] = get_args(LearningItemStatus)
_MAX_PUSH_ATTEMPTS = 3

EntityKind = Literal["project", "learning"]
Mutation = Callable[[Any], None]
RepoCheck = Callable[[Path], None]
CommitMessage = str | Callable[[], str]

# A dashboard write failed; status_code is the HTTP status to report. The shared document
# edits (workbench.edits) raise the same type, so their 400/404/422 codes pass straight through.
WriteError = EditError

# Backwards-compatible name for the original learning-only error type.
LearningWriteError = WriteError


def _edit_message(summary: str, scope: str) -> str:
    """Standard commit subject for a dashboard edit: edit(<scope>): <summary>."""
    return build_message("edit", summary, scope)


# ---------------------------------------------------------------------------
# Shared edit pipeline
# ---------------------------------------------------------------------------


def _entity_rel_path(kind: EntityKind, entity_id: str) -> str:
    if kind == "project":
        return f"projects/{entity_id}/project.yaml"
    return f"learning/{entity_id}/path.yaml"


def _validate_edited_file(kind: EntityKind, path: Path, entity_id: str) -> dict[str, Any] | None:
    """Structural + privacy validation of the edited file; returns its plain data."""
    if kind == "project":
        vres, _, plain = validate_project_file(path, expected_dir_id=entity_id)
    else:
        vres, _, plain = validate_learning_file(path, expected_dir_id=entity_id)
    if not vres.ok:
        raise WriteError(f"Validation failed: {'; '.join(vres.errors)}", status_code=422)
    return plain


def _check_against_origin(
    state: ServerState,
    rel_path: str,
    new_plain: dict[str, Any],
    allow_removals: bool = False,
    allow_text_removal: bool = False,
) -> None:
    """Semantic diff vs origin. allow_text_removal skips only the mass-deletion guard: ids stay protected."""
    old_text = show_file_at_ref(
        state.repo_dir, f"origin/{state.branch}", rel_path, env=state.env
    )
    old_plain = to_plain_python(load_yaml_str_roundtrip(old_text)) if old_text else None
    old_dict = old_plain if isinstance(old_plain, dict) else None
    sres = check_semantic_diff(
        old_dict, new_plain, allow_removals=allow_removals or allow_text_removal
    )
    errors = list(sres.errors)
    if allow_text_removal and not allow_removals and old_dict is not None:
        lost = sorted(_collect_item_ids(old_dict) - _collect_item_ids(new_plain))
        if lost:
            errors.append(f"Removed IDs are not allowed: {', '.join(lost)}")
    if errors:
        raise WriteError(f"Semantic check failed: {'; '.join(errors)}", status_code=422)


def _push_with_retries(state: ServerState, wt_dir: Path) -> None:
    """Fetch, rebase onto origin, validate the whole repo, and push; retry on push races."""
    target_ref = f"origin/{state.branch}"
    for _attempt in range(_MAX_PUSH_ATTEMPTS):
        fetch_origin(state.repo_dir, branch=state.branch, env=state.env)
        if not rebase_onto(wt_dir, target_ref=target_ref, env=state.env):
            raise WriteError("Rebase conflict while saving; reload and try again", status_code=409)
        repo_res = validate_data_repo(wt_dir)
        if not repo_res.ok:
            raise WriteError(
                f"Repository validation failed after rebase: {'; '.join(repo_res.errors)}",
                status_code=422,
            )
        if push_head_to_branch(wt_dir, branch=state.branch, env=state.env):
            return
    raise WriteError("Failed to push update to remote", status_code=500)


def _commit_edit(
    state: ServerState,
    kind: EntityKind,
    entity_id: str,
    mutate: Mutation,
    commit_message: CommitMessage,
    *,
    allow_removals: bool = False,
    allow_text_removal: bool = False,
    check_repo: RepoCheck | None = None,
) -> dict[str, Any]:
    """Apply `mutate` to one project/learning YAML file and push it to origin/<branch>.

    `mutate` receives the round-trip YAML document and edits it in place; it raises
    WriteError (e.g. 404) when the target item does not exist. `check_repo` runs on the
    worktree after the edit for cross-file checks. `commit_message` may be a callable
    evaluated after `mutate` (for ids chosen during the edit). allow_removals lets ids
    disappear (delete only); allow_text_removal only lifts the mass-deletion guard.
    Returns the snapshot after a forced resync.
    """
    rel_path = _entity_rel_path(kind, entity_id)
    label = "Project" if kind == "project" else "Learning path"

    with state._repo_lock:
        state.ensure_repo_cloned()
        fetch_origin(state.repo_dir, branch=state.branch, env=state.env)

        wt_dir = state.work_root / f"write-{entity_id}-{secrets.token_hex(4)}"
        create_worktree(state.repo_dir, wt_dir, ref=f"origin/{state.branch}", env=state.env)
        try:
            target = wt_dir / rel_path
            if not target.exists():
                raise WriteError(f"{label} '{entity_id}' not found", status_code=404)

            doc = load_yaml_roundtrip(target)
            mutate(doc)
            doc["updated"] = parse_iso_date_for_yaml(datetime.date.today().isoformat())
            dump_yaml_roundtrip(doc, target)

            plain = _validate_edited_file(kind, target, entity_id)
            if plain is not None:
                _check_against_origin(
                    state, rel_path, plain, allow_removals, allow_text_removal
                )
            if check_repo is not None:
                check_repo(wt_dir)

            message = commit_message() if callable(commit_message) else commit_message
            commit_all(wt_dir, message, env=state.env)
            _push_with_retries(state, wt_dir)
        finally:
            remove_worktree(state.repo_dir, wt_dir, env=state.env)

        state.sync_once(force=True)
        return state.get_snapshot_dict()




# ---------------------------------------------------------------------------
# Public write operations
# ---------------------------------------------------------------------------


def update_project_item_status(
    state: ServerState, project_id: str, item_id: str, new_status: str
) -> dict[str, Any]:
    """Set the status of a phase or (nested) component in projects/<id>/project.yaml."""
    _require_slug(project_id, "project id")
    _require_slug(item_id, "item id")
    status = _require_status(new_status, _PHASE_STATUSES)

    def mutate(doc: Any) -> None:
        edits.set_project_item_status(doc, project_id, item_id, status)

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        _edit_message(describe_item_change(status=status), f"{project_id}/{item_id}"),
    )


def update_project_status(state: ServerState, project_id: str, new_status: str) -> dict[str, Any]:
    """Set a project's top-level status (archive/unarchive, plan/activate a child project)."""
    _require_slug(project_id, "project id")
    status = _require_status(new_status, _PROJECT_STATUSES)

    def mutate(doc: Any) -> None:
        doc["status"] = status

    return _commit_edit(
        state, "project", project_id, mutate, _edit_message(f"status {status}", project_id)
    )


def update_learning_path_status(state: ServerState, path_id: str, new_status: str) -> dict[str, Any]:
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

    def mutate(doc: Any) -> None:
        edits.set_learning_item_status(doc, path_id, item_id, status)

    return _commit_edit(
        state,
        "learning",
        path_id,
        mutate,
        _edit_message(f"status {status}", f"learning/{path_id}/{item_id}"),
    )


# ---------------------------------------------------------------------------
# Text field edits
# ---------------------------------------------------------------------------

_PROJECT_FIELDS: tuple[str, ...] = ("name", "description")
_PROJECT_ITEM_FIELDS: tuple[str, ...] = ("name", "summary", "notes", "description", "items")
_LEARNING_FIELDS: tuple[str, ...] = ("name", "description")
_LEARNING_ITEM_FIELDS: tuple[str, ...] = ("name", "summary", "notes", "description")


def _require_body(body: object) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise WriteError("Body must be a JSON object", status_code=400)
    return body


def _reject_unknown_keys(body: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(str(k) for k in body if k not in allowed)
    if unknown:
        raise WriteError(
            f"Unsupported field(s): {', '.join(unknown)} (allowed: {', '.join(allowed)})",
            status_code=400,
        )


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


# ---------------------------------------------------------------------------
# Project structure edits: add, move, delete, parent link
# ---------------------------------------------------------------------------


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
        loc.seq.pop(loc.index)
        target_seq.insert(position, loc.node)
        if not same_list:
            _drop_if_empty(loc, doc)

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
        raise WriteError("Deleting requires {\"confirm\": true}", status_code=400)

    removed: set[str] = set()

    def mutate(doc: Any) -> None:
        loc = edits.require_project_item(doc, project_id, item_id)
        ids = _subtree_ids(loc.node)
        loc.seq.pop(loc.index)
        _drop_if_empty(loc, doc)
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


# ---------------------------------------------------------------------------
# Suggestions inbox: keep (add to the tree) or dismiss
# ---------------------------------------------------------------------------


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
