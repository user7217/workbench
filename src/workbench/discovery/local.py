"""Local filesystem repository discovery (§8)."""

from __future__ import annotations

import os
from pathlib import Path

from workbench.config import WorkbenchLocalConfig
from workbench.discovery.register import DiscoveredRepoCandidate, register_discovered_repos
from workbench.gitops import get_commit_emails
from workbench.identity import resolve_local_repo_identity

SKIP_DIRS = {"node_modules", ".venv", "vendor", "__pycache__", "dist", "build"}


def expand_scan_root(root_str: str, home_dir: Path | None = None, env: dict[str, str] | None = None) -> Path:
    """Expand ~ in scan root using explicit home_dir or HOME from env."""
    if root_str.startswith("~/") or root_str == "~":
        base_home = home_dir
        if base_home is None:
            lookup_env = os.environ if env is None else env
            if "HOME" in lookup_env:
                base_home = Path(lookup_env["HOME"])
            else:
                base_home = Path.home()
        rel = root_str[2:] if root_str.startswith("~/") else ""
        return (base_home / rel).resolve()
    return Path(root_str).resolve()


def find_git_repos_under(root: Path, max_depth: int = 4) -> list[Path]:
    """Walk root up to max_depth and return paths to git repositories."""
    if not root.exists() or not root.is_dir():
        return []

    found: list[Path] = []

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        git_marker = current / ".git"
        if git_marker.exists():
            found.append(current)
            return
        if depth == max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            if entry.name.startswith(".") or entry.name in SKIP_DIRS:
                continue
            _walk(entry, depth + 1)

    _walk(root, 0)
    return found


def is_repo_owned_by_user(
    repo_dir: Path,
    github_slug: str | None,
    github_owners: list[str],
    user_emails: list[str],
    env: dict[str, str] | None = None,
) -> bool:
    """Return True if remote owner is in github_owners OR at least one commit was authored by user_emails (§8)."""
    owners_lower = {o.lower() for o in github_owners if o}
    emails_lower = {e.lower() for e in user_emails if e}

    if github_slug and "/" in github_slug:
        owner = github_slug.split("/", 1)[0].lower()
        if owner in owners_lower:
            return True

    if emails_lower:
        repo_emails = get_commit_emails(repo_dir, env=env)
        if repo_emails & emails_lower:
            return True

    return False


def scan_local_candidates(
    config: WorkbenchLocalConfig,
    home_dir: Path | None = None,
    env: dict[str, str] | None = None,
    max_depth: int = 4,
) -> list[DiscoveredRepoCandidate]:
    """Scan configured local roots and return owned repo candidates with at least 1 commit."""
    candidates: list[DiscoveredRepoCandidate] = []
    seen_roots: set[str] = set()

    for root_str in config.scan_roots:
        root_path = expand_scan_root(root_str, home_dir=home_dir, env=env)
        for repo_dir in find_git_repos_under(root_path, max_depth=max_depth):
            root_commit, github_slug, repo_name = resolve_local_repo_identity(repo_dir, env=env)
            if not root_commit:
                # Ignore repos with no commits until their first commit (§5.4, §14)
                continue
            if root_commit in seen_roots:
                continue
            if not is_repo_owned_by_user(
                repo_dir=repo_dir,
                github_slug=github_slug,
                github_owners=config.github_owners,
                user_emails=config.user_emails,
                env=env,
            ):
                continue
            seen_roots.add(root_commit)
            candidates.append(
                DiscoveredRepoCandidate(
                    name=repo_name,
                    root_commit=root_commit,
                    github=github_slug,
                    description="",
                )
            )
    return candidates


def run_local_discovery(
    data_repo_dir: Path,
    config: WorkbenchLocalConfig,
    home_dir: Path | None = None,
    work_root: Path | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Scan local folders and register any newly discovered owned repositories."""
    candidates = scan_local_candidates(config, home_dir=home_dir, env=env)
    return register_discovered_repos(
        data_repo_dir=data_repo_dir,
        candidates=candidates,
        branch=config.branch,
        work_root=work_root,
        env=env,
        commit_source="local discovery",
    )
