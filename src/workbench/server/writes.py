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
from typing import Any, Callable, Literal, get_args

from workbench.gitops import (
    commit_all,
    create_worktree,
    fetch_origin,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
    show_file_at_ref,
)
from workbench.models import SLUG_PATTERN, LearningItemStatus, PhaseStatus, ProjectStatus
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


def _check_against_origin(state: ServerState, rel_path: str, new_plain: dict[str, Any]) -> None:
    old_text = show_file_at_ref(
        state.repo_dir, f"origin/{state.branch}", rel_path, env=state.env
    )
    old_plain = to_plain_python(load_yaml_str_roundtrip(old_text)) if old_text else None
    sres = check_semantic_diff(
        old_plain if isinstance(old_plain, dict) else None,
        new_plain,
        allow_removals=False,
    )
    if not sres.ok:
        raise WriteError(f"Semantic check failed: {'; '.join(sres.errors)}", status_code=422)


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
    commit_message: str,
) -> dict[str, Any]:
    """Apply `mutate` to one project/learning YAML file and push it to origin/<branch>.

    `mutate` receives the round-trip YAML document and edits it in place; it raises
    WriteError (e.g. 404) when the target item does not exist. Returns the snapshot
    after a forced resync.
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
                _check_against_origin(state, rel_path, plain)

            commit_all(wt_dir, commit_message, env=state.env)
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
