"""Dashboard write path on the server: status edits committed to the data repo (§10.2).

Every edit goes through `_commit_edit`, which holds the repo lock for the whole
operation, edits one YAML file in a fresh worktree cut from origin/<branch>,
validates it (structure, privacy, semantic diff against origin), commits, and
pushes with up to three fetch/rebase/validate/push attempts before resyncing.
"""

from __future__ import annotations

import datetime
import re
import secrets
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Literal, get_args

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from workbench.gitops import (
    commit_all,
    create_worktree,
    fetch_origin,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
    show_file_at_ref,
)
from workbench.identity import slugify_name
from workbench.models import (
    SLUG_PATTERN,
    Component,
    LearningItemStatus,
    LearningPath,
    Phase,
    PhaseStatus,
    Project,
    ProjectStatus,
    Subtopic,
    Topic,
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

_SLUG_RE = re.compile(SLUG_PATTERN)
_PROJECT_STATUSES: tuple[str, ...] = get_args(ProjectStatus)
_PHASE_STATUSES: tuple[str, ...] = get_args(PhaseStatus)
_LEARNING_ITEM_STATUSES: tuple[str, ...] = get_args(LearningItemStatus)
_MAX_PUSH_ATTEMPTS = 3

EntityKind = Literal["project", "learning"]
Mutation = Callable[[Any], None]
RepoCheck = Callable[[Path], None]
CommitMessage = str | Callable[[], str]

# Lists that hold groups (top level) and nested items inside a project or learning file.
_GROUP_KEYS: tuple[str, ...] = ("phases", "topics")
_GROUP_CHILD_KEYS: tuple[str, ...] = ("components", "topics", "subtopics")
_ITEM_LIST_KEYS: tuple[str, ...] = ("phases", "topics", "subtopics", "components")


class WriteError(RuntimeError):
    """A dashboard write failed; status_code is the HTTP status to report."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# Backwards-compatible name for the original learning-only error type.
LearningWriteError = WriteError


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------


def _require_slug(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 48 or not _SLUG_RE.match(value):
        raise WriteError(f"Invalid {label}: {value!r}", status_code=400)
    return value


def _require_status(value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise WriteError(
            f"Invalid status: {value!r} (expected one of {', '.join(allowed)})",
            status_code=400,
        )
    return value


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


def _collect_item_ids(node: Any) -> set[str]:
    """Ids of every group/component/topic/subtopic nested anywhere under a plain or YAML node."""
    ids: set[str] = set()
    if not hasattr(node, "get"):
        return ids
    for key in _ITEM_LIST_KEYS:
        children = node.get(key)
        if not isinstance(children, list):
            continue
        for child in children:
            if hasattr(child, "get"):
                if isinstance(child.get("id"), str):
                    ids.add(child["id"])
                ids |= _collect_item_ids(child)
    return ids


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
# Item lookup inside a document
# ---------------------------------------------------------------------------


def _children(node: Any, keys: tuple[str, ...]) -> list[Any]:
    out: list[Any] = []
    for key in keys:
        for child in node.get(key) or []:
            if hasattr(child, "get"):
                out.append(child)
    return out


def _find_component(nodes: list[Any], item_id: str) -> Any | None:
    """Depth-first search through components and their nested `components`."""
    for node in nodes:
        if node.get("id") == item_id:
            return node
        found = _find_component(_children(node, ("components",)), item_id)
        if found is not None:
            return found
    return None


def _find_project_item(doc: Any, item_id: str) -> Any | None:
    """Find a phase (phases/topics groups) or a component at any nesting depth."""
    groups = _children(doc, ("phases", "topics"))
    for group in groups:
        if group.get("id") == item_id:
            return group
    for group in groups:
        found = _find_component(_children(group, ("components", "topics", "subtopics")), item_id)
        if found is not None:
            return found
    return None


def _find_learning_item(doc: Any, item_id: str) -> Any | None:
    for topic in _children(doc, ("topics",)):
        if topic.get("id") == item_id:
            return topic
        for sub in _children(topic, ("subtopics",)):
            if sub.get("id") == item_id:
                return sub
    return None


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
        item = _find_project_item(doc, item_id)
        if item is None:
            raise WriteError(
                f"Item '{item_id}' not found in project '{project_id}'", status_code=404
            )
        item["status"] = status

    return _commit_edit(
        state, "project", project_id, mutate, f"Mark {item_id} {status} via dashboard"
    )


def update_project_status(state: ServerState, project_id: str, new_status: str) -> dict[str, Any]:
    """Set a project's top-level status (archive/unarchive, plan/activate a child project)."""
    _require_slug(project_id, "project id")
    status = _require_status(new_status, _PROJECT_STATUSES)

    def mutate(doc: Any) -> None:
        doc["status"] = status

    return _commit_edit(
        state, "project", project_id, mutate, f"Set project {project_id} to {status} via dashboard"
    )


def update_learning_path_status(state: ServerState, path_id: str, new_status: str) -> dict[str, Any]:
    """Set a learning path's top-level status."""
    _require_slug(path_id, "learning path id")
    status = _require_status(new_status, _PROJECT_STATUSES)

    def mutate(doc: Any) -> None:
        doc["status"] = status

    return _commit_edit(
        state,
        "learning",
        path_id,
        mutate,
        f"Set learning path {path_id} to {status} via dashboard",
    )


def update_learning_item_status(
    state: ServerState, path_id: str, item_id: str, new_status: str
) -> dict[str, Any]:
    """Set the status of a topic or subtopic in learning/<path_id>/path.yaml."""
    _require_slug(path_id, "learning path id")
    _require_slug(item_id, "item id")
    status = _require_status(new_status, _LEARNING_ITEM_STATUSES)

    def mutate(doc: Any) -> None:
        item = _find_learning_item(doc, item_id)
        if item is None:
            raise WriteError(
                f"Item '{item_id}' not found in learning path '{path_id}'", status_code=404
            )
        item["status"] = status

    return _commit_edit(
        state, "learning", path_id, mutate, f"Mark {item_id} {status} via dashboard"
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


def _yaml_text(value: str) -> str:
    """Multi-line text is written as a YAML literal block (LF line endings) so the file stays readable."""
    value = value.replace("\r\n", "\n")
    return LiteralScalarString(value) if "\n" in value else value


def _set_key(node: Any, key: str, value: Any, order: list[str]) -> None:
    """Set node[key]; a new key goes after the keys that precede it in the model's field order."""
    if key in node:
        node[key] = value
        return
    rank = order.index(key) if key in order else len(order)
    pos = 0
    for i, existing in enumerate(node.keys()):
        if existing in order and order.index(existing) < rank:
            pos = i + 1
    node.insert(pos, key, value)


def _apply_text_fields(node: Any, fields: dict[str, Any], order: list[str]) -> None:
    """Write text fields; an empty value removes optional keys (models default them to "")."""
    for key, value in fields.items():
        if key == "name":
            # Required; an empty name fails model validation (422) rather than being dropped.
            _set_key(node, "name", _yaml_text(value), order)
        elif key == "items":
            if value:
                _set_key(node, "items", CommentedSeq(_yaml_text(v) for v in value), order)
            else:
                node.pop("items", None)
        elif value == "":
            node.pop(key, None)
        else:
            _set_key(node, key, _yaml_text(value), order)


def _field_order(model: type) -> list[str]:
    return list(model.model_fields)


def update_project_fields(state: ServerState, project_id: str, body: object) -> dict[str, Any]:
    """Edit a project's name/description."""
    _require_slug(project_id, "project id")
    fields = _parse_text_fields(body, _PROJECT_FIELDS)

    def mutate(doc: Any) -> None:
        _apply_text_fields(doc, fields, _field_order(Project))

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        f"Edit {project_id} fields via dashboard",
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
        loc = _locate_project_item(doc, item_id)
        if loc is None:
            raise WriteError(
                f"Item '{item_id}' not found in project '{project_id}'", status_code=404
            )
        if loc.is_group and "items" in fields:
            raise WriteError("'items' can only be set on components, not phases/topics", 400)
        _apply_text_fields(loc.node, fields, _field_order(Phase if loc.is_group else Component))

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        f"Edit {project_id}/{item_id} fields via dashboard",
        allow_text_removal=True,
    )


def update_learning_fields(state: ServerState, path_id: str, body: object) -> dict[str, Any]:
    """Edit a learning path's name/description."""
    _require_slug(path_id, "learning path id")
    fields = _parse_text_fields(body, _LEARNING_FIELDS)

    def mutate(doc: Any) -> None:
        _apply_text_fields(doc, fields, _field_order(LearningPath))

    return _commit_edit(
        state,
        "learning",
        path_id,
        mutate,
        f"Edit {path_id} fields via dashboard",
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
        is_topic = any(t.get("id") == item_id for t in _children(doc, ("topics",)))
        item = _find_learning_item(doc, item_id)
        if item is None:
            raise WriteError(
                f"Item '{item_id}' not found in learning path '{path_id}'", status_code=404
            )
        _apply_text_fields(item, fields, _field_order(Topic if is_topic else Subtopic))

    return _commit_edit(
        state,
        "learning",
        path_id,
        mutate,
        f"Edit {path_id}/{item_id} fields via dashboard",
        allow_text_removal=True,
    )


# ---------------------------------------------------------------------------
# Project structure edits: add, move, delete, parent link
# ---------------------------------------------------------------------------


@dataclass
class _Location:
    """Where an item sits: its node, the YAML list holding it, that list's key, and the list's owner."""

    node: Any
    seq: Any
    key: str
    owner: Any
    is_group: bool

    @property
    def index(self) -> int:
        return next(i for i, n in enumerate(self.seq) if n is self.node)


def _locate_in(owner: Any, keys: tuple[str, ...], item_id: str) -> _Location | None:
    for key in keys:
        seq = owner.get(key)
        if not isinstance(seq, list):
            continue
        for node in seq:
            if not hasattr(node, "get"):
                continue
            if node.get("id") == item_id:
                return _Location(node, seq, key, owner, is_group=False)
            found = _locate_in(node, ("components",), item_id)
            if found is not None:
                return found
    return None


def _locate_project_item(doc: Any, item_id: str) -> _Location | None:
    """Find a group (phases/topics) first, then a component at any depth."""
    for key in _GROUP_KEYS:
        seq = doc.get(key)
        if not isinstance(seq, list):
            continue
        for node in seq:
            if hasattr(node, "get") and node.get("id") == item_id:
                return _Location(node, seq, key, doc, is_group=True)
    for group in _children(doc, _GROUP_KEYS):
        found = _locate_in(group, _GROUP_CHILD_KEYS, item_id)
        if found is not None:
            return found
    return None


def _all_project_ids(doc: Any) -> set[str]:
    return _collect_item_ids(doc)


def _subtree_ids(node: Any) -> set[str]:
    return {node["id"]} | _collect_item_ids(node)


def _drop_if_empty(loc: _Location, doc: Any) -> None:
    """Remove an emptied nested list key (top-level phases/topics stay)."""
    if loc.owner is not doc and isinstance(loc.seq, list) and not loc.seq:
        loc.owner.pop(loc.key, None)


def _child_list(node: Any, order: list[str]) -> Any:
    """The node's `components` list, created in model field order if missing."""
    seq = node.get("components")
    if not isinstance(seq, list):
        seq = CommentedSeq()
        _set_key(node, "components", seq, order)
    return seq


def _require_position(value: object, label: str = "position") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WriteError(f"'{label}' must be a non-negative integer", status_code=400)
    return value


def _check_position(position: int, length: int) -> None:
    if position > length:
        raise WriteError(
            f"Position {position} is out of range (0..{length})", status_code=400
        )


def _unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: 48 - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
        n += 1


def _slug_for_item(name: str) -> str:
    # slugify_name falls back to "project"; an item with no usable characters is "item".
    return slugify_name(name) if re.search(r"[a-z0-9]", name.lower()) else "item"


_ADD_KEYS: tuple[str, ...] = ("parent_id", "name", "id", "status", "summary", "position")


def add_project_item(
    state: ServerState, project_id: str, body: object
) -> tuple[dict[str, Any], str]:
    """Add a group (parent_id null) or a component under a group/component. Returns (snapshot, new id)."""
    _require_slug(project_id, "project id")
    data = _require_body(body)
    _reject_unknown_keys(data, _ADD_KEYS)
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
        if parent_id is None:
            key = "phases" if doc.get("uses_phases", True) is not False else "topics"
            seq = doc.get(key)
            if not isinstance(seq, list):
                seq = CommentedSeq()
                _set_key(doc, key, seq, _field_order(Project))
            label = "phase" if key == "phases" else "topic"
        else:
            parent = _locate_project_item(doc, parent_id)
            if parent is None:
                raise WriteError(
                    f"Parent item '{parent_id}' not found in project '{project_id}'",
                    status_code=404,
                )
            seq = _child_list(parent.node, _field_order(Phase if parent.is_group else Component))
            label = "component"

        taken = _all_project_ids(doc)
        if explicit_id is not None:
            if explicit_id in taken:
                raise WriteError(
                    f"Id '{explicit_id}' is already used in project '{project_id}'",
                    status_code=422,
                )
            new_id = explicit_id
        else:
            new_id = _unique_id(_slug_for_item(name), taken)

        pos = len(seq) if position is None else position
        _check_position(pos, len(seq))
        node = CommentedMap()
        node["id"] = new_id
        node["name"] = name
        node["status"] = status
        if summary:
            node["summary"] = _yaml_text(summary)
        seq.insert(pos, node)
        created.update(id=new_id, label=label)

    snapshot = _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        lambda: f"Add {created['label']} {created['id']} to {project_id} via dashboard",
    )
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
        loc = _locate_project_item(doc, item_id)
        if loc is None:
            raise WriteError(
                f"Item '{item_id}' not found in project '{project_id}'", status_code=404
            )
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
        if "parent" in moved_under:
            return f"Move {item_id} under {moved_under['parent']} in {project_id} via dashboard"
        return f"Move {item_id} to position {position} in {project_id} via dashboard"

    return _commit_edit(state, "project", project_id, mutate, message)


def _iter_component_nodes(doc: Any):
    """Every component node (any depth) under the project's groups."""

    def walk(nodes: list[Any]):
        for node in nodes:
            yield node
            yield from walk(_children(node, ("components",)))

    for group in _children(doc, _GROUP_KEYS):
        yield from walk(_children(group, _GROUP_CHILD_KEYS))


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
        loc = _locate_project_item(doc, item_id)
        if loc is None:
            raise WriteError(
                f"Item '{item_id}' not found in project '{project_id}'", status_code=404
            )
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

    return _commit_edit(
        state,
        "project",
        project_id,
        mutate,
        f"Delete {item_id} from {project_id} via dashboard",
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
            return f"Attach {project_id} under {parent}/{under} via dashboard"
        if previous.get("parent"):
            return f"Detach {project_id} from {previous['parent']} via dashboard"
        return f"Clear parent of {project_id} via dashboard"

    return _commit_edit(state, "project", project_id, mutate, message, check_repo=check_link)
