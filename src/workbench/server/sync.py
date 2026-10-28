"""Server background sync, full-repo validation, and atomic snapshot swap (§10.1)."""

from __future__ import annotations

import datetime
import json
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workbench.gitops import (
    run_git,
    GitError,
    clone_repo,
    create_worktree,
    fetch_origin,
    get_head_commit,
    remove_worktree,
)
from workbench.validate import validate_data_repo
from workbench.yamlio import load_yaml_roundtrip, to_plain_python


@dataclass
class SyncErrorInfo:
    commit: str
    file: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "commit": self.commit,
            "file": self.file,
            "message": self.message,
        }


@dataclass
class ServerState:
    remote_url: str
    state_dir: Path
    branch: str = "main"
    env: dict[str, str] | None = None
    github_owners: list[str] = field(default_factory=list)
    github_read_token: str | None = None
    github_api_base: str = "https://api.github.com"

    snapshot: dict[str, Any] = field(
        default_factory=lambda: {
            "projects": [],
            "learning": [],
            "topic_markdowns": {},
            "commit": None,
            "synced_at": None,
        }
    )
    last_good_commit: str | None = None
    last_checked_at: str | None = None
    last_error: SyncErrorInfo | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # Serializes every git operation on repo_dir (sync, dashboard writes, discovery).
    # Re-entrant because the write path calls sync_once while holding it.
    _repo_lock: threading.RLock = field(default_factory=threading.RLock)

    @property
    def repo_dir(self) -> Path:
        return self.state_dir / "repo"

    @property
    def snapshot_path(self) -> Path:
        return self.state_dir / "snapshot.json"

    @property
    def work_root(self) -> Path:
        return self.state_dir / "work"

    def ensure_repo_cloned(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not (self.repo_dir / ".git").exists():
            clone_repo(self.remote_url, self.repo_dir, branch=self.branch, env=self.env)

    def load_snapshot_from_disk(self) -> bool:
        """Restore snapshot from snapshot.json on startup if present (§10.1 step 6)."""
        if not self.snapshot_path.exists():
            return False
        try:
            data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "projects" in data and "learning" in data:
                with self._lock:
                    self.snapshot = data
                    self.last_good_commit = data.get("commit")
                return True
        except Exception:
            pass
        return False

    def sync_once(self, force: bool = False) -> bool:
        """Fetch origin/<branch>, validate in a staging worktree, and atomically swap snapshot (§10.1).

        Never raises: any failure is recorded in last_error and the previous snapshot is kept.
        """
        with self._repo_lock:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            try:
                return self._sync_once_locked(force, now_iso)
            except Exception as exc:
                with self._lock:
                    self.last_checked_at = now_iso
                    self.last_error = SyncErrorInfo(
                        commit="unknown",
                        file="sync",
                        message=f"{type(exc).__name__}: {exc}",
                    )
                return False

    def _sync_once_locked(self, force: bool, now_iso: str) -> bool:
        try:
            self.ensure_repo_cloned()
            fetch_origin(self.repo_dir, branch=self.branch, env=self.env)
            remote_ref = f"origin/{self.branch}"
            fetched_commit = get_head_commit(self.repo_dir, remote_ref, env=self.env)
        except GitError as exc:
            with self._lock:
                self.last_checked_at = now_iso
                self.last_error = SyncErrorInfo(
                    commit="unknown",
                    file="git",
                    message=str(exc),
                )
            return False

        if not fetched_commit:
            with self._lock:
                self.last_checked_at = now_iso
            return False

        # Step 2: If origin/<branch> equals the last loaded commit and no prior error on it, stop
        with self._lock:
            if not force and fetched_commit == self.last_good_commit and self.last_error is None:
                self.last_checked_at = now_iso
                return True

        # Step 3: Check out fetched commit into a staging worktree and run full validation
        staging_wt = self.work_root / f"stage-{secrets.token_hex(4)}"
        create_worktree(self.repo_dir, staging_wt, ref=fetched_commit, env=self.env)
        try:
            vres = validate_data_repo(staging_wt)
            if not vres.ok:
                first_err = vres.errors[0]
                err_file = "repository"
                if ":" in first_err:
                    err_file = first_err.split(":", 1)[0].strip()
                with self._lock:
                    self.last_checked_at = now_iso
                    self.last_error = SyncErrorInfo(
                        commit=fetched_commit,
                        file=err_file,
                        message="; ".join(vres.errors),
                    )
                return False

            # Step 4: Build in-memory snapshot
            projects_list = _read_all_projects(staging_wt)
            learning_list, topic_mds = _read_all_learning(staging_wt)

            new_snapshot = {
                "projects": projects_list,
                "learning": learning_list,
                "topic_markdowns": topic_mds,
                "updated_at": _last_changed(staging_wt, self.env),
                "commit": fetched_commit,
                "synced_at": now_iso,
            }

            # Write snapshot.json atomically (temp file + rename)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp_snap = self.snapshot_path.with_suffix(".json.tmp")
            tmp_snap.write_text(
                json.dumps(new_snapshot, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tmp_snap.replace(self.snapshot_path)

            with self._lock:
                self.snapshot = new_snapshot
                self.last_good_commit = fetched_commit
                self.last_checked_at = now_iso
                self.last_error = None
            return True
        finally:
            remove_worktree(self.repo_dir, staging_wt, env=self.env)

    def get_status_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "last_good_commit": self.last_good_commit,
                "last_checked_at": self.last_checked_at,
                "last_error": self.last_error.to_dict() if self.last_error else None,
            }

    def get_snapshot_dict(self) -> dict[str, Any]:
        with self._lock:
            return self.snapshot


def _last_changed(repo_root: Path, env: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """When each project and learning path last changed, from git history (ISO 8601, newest wins).

    The YAML `updated` field is only a date, so this gives the UI a precise "recently changed" order.
    """
    out: dict[str, dict[str, str]] = {"projects": {}, "learning": {}}
    proc = run_git(
        ["log", "--format=%x00%cI", "--name-only", "HEAD"], cwd=repo_root, env=env, check=False
    )
    if proc.returncode != 0:
        return out
    when = ""
    for line in proc.stdout.splitlines():
        if line.startswith("\x00"):
            when = line[1:]
            continue
        parts = line.strip().split("/")
        if len(parts) >= 2 and parts[0] in ("projects", "learning") and when:
            # git log lists newest commits first, so the first time seen is the latest change
            out[parts[0]].setdefault(parts[1], when)
    return out


def _read_all_projects(repo_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    p_root = repo_root / "projects"
    if not p_root.exists():
        return out
    for pdir in sorted(p_root.iterdir()):
        pfile = pdir / "project.yaml"
        if pdir.is_dir() and pfile.exists():
            plain = to_plain_python(load_yaml_roundtrip(pfile))
            if isinstance(plain, dict):
                out.append(plain)
    out.sort(key=lambda x: str(x.get("name", "")).lower())
    return out


def _read_all_learning(
    repo_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    out: list[dict[str, Any]] = []
    topic_mds: dict[str, str] = {}
    l_root = repo_root / "learning"
    if not l_root.exists():
        return out, topic_mds
    for ldir in sorted(l_root.iterdir()):
        lfile = ldir / "path.yaml"
        if ldir.is_dir() and lfile.exists():
            plain = to_plain_python(load_yaml_roundtrip(lfile))
            if isinstance(plain, dict):
                path_id = str(plain.get("id", ldir.name))
                out.append(plain)
                t_dir = ldir / "topics"
                if t_dir.exists():
                    for md_file in sorted(t_dir.glob("*.md")):
                        key = f"{path_id}/{md_file.stem}"
                        topic_mds[key] = md_file.read_text(encoding="utf-8")
    out.sort(key=lambda x: str(x.get("name", "")).lower())
    return out, topic_mds
