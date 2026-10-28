"""Agent hook logic for Claude Code Stop hook and Gemini CLI AfterAgent hook (§9.3).

State lives in ~/.workbench/state.json:

    repos[<root commit>]    = {last_seen_commit, last_seen_fingerprint, focus, updated_at}
    sessions[<session id>] = {prompted_at, prompted_head, prompted_fingerprint, root_commit}

Decision when an agent finishes a message (first match wins):

    stop_hook_active / not a git repo / no commits / any error   -> allow (fail open)
    HEAD != last_seen_commit                                     -> block: workbench-review
    no last_seen_fingerprint stored (pre-fingerprint state.json) -> record it, allow
    fingerprint != last_seen_fingerprint                         -> block: workbench-update
    otherwise                                                    -> allow

A session is never blocked twice in a row for the same fingerprint: an agent that ignores
the prompt is asked again only once the working state changes again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from workbench.config import get_workbench_dir
from workbench.gitops import (
    get_head_commit,
    get_repo_toplevel,
    get_root_commit,
    git_output_bytes,
    is_git_repo,
)

REVIEW_REASON = (
    "Before finishing, run the workbench-review skill: a new commit landed, "
    "so reconcile this project's Workbench entry{focus}."
)
UPDATE_REASON = (
    "Before finishing, run the workbench-update skill: record what this message changed "
    "in Workbench{focus}, or run `workbench hook-check --mark-seen` if nothing worth "
    "recording changed."
)
SESSION_EXPIRY_SECONDS = 7 * 24 * 3600


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------


def _state_path(home_dir: Path | None, env: dict[str, str] | None) -> Path:
    return get_workbench_dir(home_dir=home_dir, env=env) / "state.json"


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"sessions": {}, "repos": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"sessions": {}, "repos": {}}
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        if not isinstance(data.get("repos"), dict):
            data["repos"] = {}
        return data
    except Exception:
        return {"sessions": {}, "repos": {}}


def _save_state(state_path: Path, state: dict[str, Any], now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    # Prune expired session entries (> 7 days old)
    sessions = state.get("sessions", {})
    expired = [
        sid
        for sid, info in sessions.items()
        if isinstance(info, dict) and (ts - float(info.get("prompted_at", 0))) > SESSION_EXPIRY_SECONDS
    ]
    for sid in expired:
        del sessions[sid]

    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name so a hook and a CLI command writing at once never share a temp file
    tmp_path = state_path.with_name(f"{state_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def _repo_entry(state: dict[str, Any], root_sha: str) -> dict[str, Any]:
    entry = state["repos"].get(root_sha)
    if not isinstance(entry, dict):
        entry = {}
        state["repos"][root_sha] = entry
    return entry


# ---------------------------------------------------------------------------
# Repo identity and working-state fingerprint
# ---------------------------------------------------------------------------


def _repo_ids(cwd: Path, env: dict[str, str] | None) -> tuple[Path, str, str] | None:
    """(top-level dir, root commit, HEAD) of the repo containing cwd, or None."""
    if not is_git_repo(cwd, env=env):
        return None
    top = get_repo_toplevel(cwd, env=env) or cwd
    root_sha = get_root_commit(top, env=env)
    head_sha = get_head_commit(top, "HEAD", env=env)
    if not root_sha or not head_sha:
        return None
    return top, root_sha, head_sha


def compute_fingerprint(repo_top: Path, head_sha: str, env: dict[str, str] | None = None) -> str:
    """HEAD plus a hash of the working state: status, diff against HEAD, untracked file stats.

    Deterministic for an unchanged tree. `--no-optional-locks` keeps status from taking
    index.lock, so the hook never races the agent's own git commands.
    """
    status = git_output_bytes(
        ["--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        repo_top,
        env,
    )
    diff = git_output_bytes(
        ["--no-optional-locks", "diff", "HEAD", "--no-color", "--no-ext-diff", "--binary"],
        repo_top,
        env,
    )
    h = hashlib.sha256()
    h.update(status or b"")
    h.update(b"\0--diff--\0")
    h.update(diff or b"")
    # Untracked files are not in `git diff`; their size and mtime catch later edits to them.
    for entry in (status or b"").split(b"\0"):
        if entry.startswith(b"?? "):
            rel = entry[3:].decode("utf-8", errors="surrogateescape")
            try:
                st = (repo_top / rel).stat()
                h.update(f"{rel}\0{st.st_size}\0{st.st_mtime_ns}\0".encode("utf-8", "surrogateescape"))
            except OSError:
                pass
    return f"{head_sha}:{h.hexdigest()[:32]}"


# ---------------------------------------------------------------------------
# Public helpers used by the CLI
# ---------------------------------------------------------------------------


def mark_repo_seen(
    cwd: Path,
    home_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Record HEAD and the working-state fingerprint as seen for the git repo at cwd (§9.3)."""
    try:
        ids = _repo_ids(cwd, env)
        if ids is None:
            return False
        top, root_sha, head_sha = ids
        state_path = _state_path(home_dir, env)
        state = _load_state(state_path)
        entry = _repo_entry(state, root_sha)
        entry["last_seen_commit"] = head_sha
        entry["last_seen_fingerprint"] = compute_fingerprint(top, head_sha, env)
        entry["updated_at"] = time.time()
        _save_state(state_path, state)
        return True
    except Exception:
        return False


def get_repo_focus(
    cwd: Path, home_dir: Path | None = None, env: dict[str, str] | None = None
) -> tuple[bool, str | None]:
    """(cwd is a git repo with commits, focus "project[/item]" or None)."""
    ids = _repo_ids(cwd, env)
    if ids is None:
        return False, None
    entry = _load_state(_state_path(home_dir, env))["repos"].get(ids[1])
    focus = entry.get("focus") if isinstance(entry, dict) else None
    return True, focus if isinstance(focus, str) and focus else None


def set_repo_focus(
    cwd: Path,
    focus: str | None,
    home_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Set (or clear, with None) the focus for the git repo at cwd; False outside a repo."""
    ids = _repo_ids(cwd, env)
    if ids is None:
        return False
    state_path = _state_path(home_dir, env)
    state = _load_state(state_path)
    entry = _repo_entry(state, ids[1])
    if focus:
        entry["focus"] = focus
    else:
        entry.pop("focus", None)
    _save_state(state_path, state)
    return True


# ---------------------------------------------------------------------------
# Hook decision
# ---------------------------------------------------------------------------


def _focus_hint(entry: dict[str, Any]) -> str:
    focus = entry.get("focus")
    return f" (focus: {focus})" if isinstance(focus, str) and focus else ""


def _already_prompted(
    sess: Any, root_sha: str, head_sha: str, fingerprint: str, ts: float, reviewing: bool
) -> bool:
    """This session was last blocked for exactly this state (so do not loop on it)."""
    if not isinstance(sess, dict):
        return False
    if (ts - float(sess.get("prompted_at", 0))) > SESSION_EXPIRY_SECONDS:
        return False
    if sess.get("root_commit") not in (None, root_sha):
        return False
    prompted_fp = sess.get("prompted_fingerprint")
    if prompted_fp is None:
        # Entry written before fingerprints existed: it could only have been a review prompt
        # at the HEAD it recorded, so it covers a review at that HEAD and nothing else.
        return reviewing and sess.get("prompted_head") == head_sha
    return prompted_fp == fingerprint


def evaluate_hook_input(
    stdin_text: str,
    home_dir: Path | None = None,
    env: dict[str, str] | None = None,
    now: float | None = None,
) -> str | None:
    """Evaluate hook JSON input and return stdout JSON string (to block) or None (to allow).

    Never raises: any internal error returns None (allow) per §9.3.
    """
    try:
        if not stdin_text or not stdin_text.strip():
            return None
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict):
            return None
        if payload.get("stop_hook_active") is True:
            return None

        session_id = str(payload.get("session_id") or "").strip()
        cwd = Path(str(payload.get("cwd") or os.getcwd()).strip())

        ids = _repo_ids(cwd, env)
        if ids is None:
            # Outside a git repo, or a repo with no commits yet
            return None
        repo_top, root_sha, head_sha = ids

        state_path = _state_path(home_dir, env)
        ts = now if now is not None else time.time()
        state = _load_state(state_path)
        raw_entry = state["repos"].get(root_sha)
        entry: dict[str, Any] = raw_entry if isinstance(raw_entry, dict) else {}
        fingerprint = compute_fingerprint(repo_top, head_sha, env)

        reviewing = entry.get("last_seen_commit") != head_sha
        if reviewing:
            reason = REVIEW_REASON.format(focus=_focus_hint(entry))
        else:
            seen_fp = entry.get("last_seen_fingerprint")
            if seen_fp is None:
                # Upgrade path: state.json from before fingerprints. HEAD was already seen, so
                # take the current working state as the baseline instead of prompting right
                # after the upgrade; later edits are then compared against it.
                _repo_entry(state, root_sha)["last_seen_fingerprint"] = fingerprint
                _save_state(state_path, state, now=ts)
                return None
            if seen_fp == fingerprint:
                return None
            reason = UPDATE_REASON.format(focus=_focus_hint(entry))

        if session_id:
            sess = state["sessions"].get(session_id)
            if _already_prompted(sess, root_sha, head_sha, fingerprint, ts, reviewing):
                return None
            state["sessions"][session_id] = {
                "prompted_at": ts,
                "prompted_head": head_sha,
                "prompted_fingerprint": fingerprint,
                "root_commit": root_sha,
            }
            _save_state(state_path, state, now=ts)
        return json.dumps({"decision": "block", "reason": reason})
    except Exception:
        # Never block when anything fails (§9.3)
        return None
