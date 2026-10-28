"""Agent hook logic for Claude Code Stop hook and Gemini CLI AfterAgent hook (§9.3)."""

from __future__ import annotations

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
    get_uncommitted_changed_lines,
    is_git_repo,
)

HOOK_REASON = (
    "Before finishing, run the workbench skill: record what this conversation built, "
    "finished, and left to do in the project's Workbench metadata, if anything changed."
)
SESSION_EXPIRY_SECONDS = 7 * 24 * 3600


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {"sessions": {}, "repos": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"sessions": {}, "repos": {}}
        data.setdefault("sessions", {})
        data.setdefault("repos", {})
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
    tmp_path = state_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(state_path)


def mark_repo_seen(
    cwd: Path,
    home_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Record last_seen_commit = HEAD for the git repository at cwd (§9.3)."""
    try:
        if not is_git_repo(cwd, env=env):
            return False
        root_sha = get_root_commit(cwd, env=env)
        head_sha = get_head_commit(cwd, "HEAD", env=env)
        if not root_sha or not head_sha:
            return False
        wb_dir = get_workbench_dir(home_dir=home_dir, env=env)
        state_path = wb_dir / "state.json"
        state = _load_state(state_path)
        state["repos"][root_sha] = {
            "last_seen_commit": head_sha,
            "updated_at": time.time(),
        }
        _save_state(state_path, state)
        return True
    except Exception:
        return False


def _block_and_record(
    state: dict[str, Any],
    state_path: Path,
    session_id: str,
    root_sha: str,
    head_sha: str,
    ts: float,
) -> str:
    """Record that this session was prompted at head_sha and return the blocking hook output."""
    if session_id:
        state["sessions"][session_id] = {
            "prompted_at": ts,
            "prompted_head": head_sha,
            "root_commit": root_sha,
        }
        _save_state(state_path, state, now=ts)
    return json.dumps({"decision": "block", "reason": HOOK_REASON})


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

        # 1. Allow if stop_hook_active is true
        if payload.get("stop_hook_active") is True:
            return None

        session_id = str(payload.get("session_id") or "").strip()
        cwd_str = str(payload.get("cwd") or os.getcwd()).strip()
        cwd = Path(cwd_str)

        # 2. Allow if cwd is not inside a git repo
        if not is_git_repo(cwd, env=env):
            return None

        repo_top = get_repo_toplevel(cwd, env=env) or cwd
        root_sha = get_root_commit(repo_top, env=env)
        head_sha = get_head_commit(repo_top, "HEAD", env=env)
        if not root_sha or not head_sha:
            # Ignore repos with no commits
            return None

        wb_dir = get_workbench_dir(home_dir=home_dir, env=env)
        state_path = wb_dir / "state.json"
        ts = now if now is not None else time.time()
        state = _load_state(state_path)

        repo_entry = state["repos"].get(root_sha, {})
        last_seen = repo_entry.get("last_seen_commit") if isinstance(repo_entry, dict) else None
        has_new_commits = last_seen != head_sha

        # 3. A session already prompted (within 7 days) is re-prompted only when HEAD moved past
        #    both the last push/mark-seen and the HEAD it was last prompted at, so later work in a
        #    long session is captured without nagging on every stop.
        if session_id:
            sess_entry = state["sessions"].get(session_id)
            if isinstance(sess_entry, dict):
                prompted_at = float(sess_entry.get("prompted_at", 0))
                if (ts - prompted_at) <= SESSION_EXPIRY_SECONDS:
                    if not has_new_commits or sess_entry.get("prompted_head") == head_sha:
                        return None
                    return _block_and_record(state, state_path, session_id, root_sha, head_sha, ts)

        # 4. Compare repo HEAD with last_seen_commit in state.json, or >50 changed lines uncommitted
        changed_lines = 0 if has_new_commits else get_uncommitted_changed_lines(repo_top, env=env)

        if has_new_commits or changed_lines > 50:
            return _block_and_record(state, state_path, session_id, root_sha, head_sha, ts)

        return None
    except Exception:
        # Never block when anything fails (§9.3)
        return None
