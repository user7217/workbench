"""Shared write transaction: edit one YAML file in a fresh worktree, validate, commit, push."""

from __future__ import annotations

import datetime
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, get_args

from workbench.commitmsg import build_message
from workbench.edits import EditError
from workbench.edits import collect_item_ids as _collect_item_ids
from workbench.gitops import (
    commit_all,
    create_worktree,
    fetch_origin,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
    show_file_at_ref,
)
from workbench.models import LearningItemStatus, PhaseStatus, ProjectStatus
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
# Edits files in a worktree and returns the commit subject
WorktreeEdit = Callable[[Path], str]
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
    old_text = show_file_at_ref(state.repo_dir, f"origin/{state.branch}", rel_path, env=state.env)
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


def _run_transaction(state: ServerState, name: str, edit: WorktreeEdit) -> dict[str, Any]:
    """Run `edit` in a fresh worktree cut from origin/<branch>, then commit, push and resync.

    `edit` receives the worktree directory, changes and validates files in it, and returns
    the commit subject; it raises WriteError to abandon the write. The repo lock is held
    throughout and the worktree is always removed. Returns the snapshot after a forced resync.
    """
    with state._repo_lock:
        state.ensure_repo_cloned()
        fetch_origin(state.repo_dir, branch=state.branch, env=state.env)

        wt_dir = state.work_root / f"write-{name}-{secrets.token_hex(4)}"
        create_worktree(state.repo_dir, wt_dir, ref=f"origin/{state.branch}", env=state.env)
        try:
            message = edit(wt_dir)
            commit_all(wt_dir, message, env=state.env)
            _push_with_retries(state, wt_dir)
        finally:
            remove_worktree(state.repo_dir, wt_dir, env=state.env)

        state.sync_once(force=True)
        return state.get_snapshot_dict()


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

    def edit(wt_dir: Path) -> str:
        target = wt_dir / rel_path
        if not target.exists():
            raise WriteError(f"{label} '{entity_id}' not found", status_code=404)

        doc = load_yaml_roundtrip(target)
        mutate(doc)
        doc["updated"] = parse_iso_date_for_yaml(datetime.date.today().isoformat())
        dump_yaml_roundtrip(doc, target)

        plain = _validate_edited_file(kind, target, entity_id)
        if plain is not None:
            _check_against_origin(state, rel_path, plain, allow_removals, allow_text_removal)
        if check_repo is not None:
            check_repo(wt_dir)
        return commit_message() if callable(commit_message) else commit_message

    return _run_transaction(state, entity_id, edit)


# ---------------------------------------------------------------------------
# Request body checks
# ---------------------------------------------------------------------------


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
