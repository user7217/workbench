"""Repository identity resolution via root commit SHA (§5.4)."""

from __future__ import annotations

import re
from pathlib import Path

from workbench.gitops import get_remote_url, get_root_commit
from workbench.models import Project


class AmbiguousRepoIdentityError(RuntimeError):
    """Raised when multiple projects claim the same root commit and cannot be disambiguated."""


def parse_github_slug(remote_url: str | None) -> str | None:
    """Extract 'owner/repo' slug from a GitHub or git remote URL."""
    if not remote_url:
        return None
    url = remote_url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    # Match git@github.com:owner/repo or https://github.com/owner/repo
    m = re.search(r"github\.com[:/]([^/]+/[^/]+)$", url)
    if m:
        return m.group(1)
    # Match file/path style .../owner/repo
    parts = [p for p in Path(url).parts if p not in ("/", "\\")]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None


def slugify_name(name: str) -> str:
    """Convert an arbitrary project or repo name into a valid Workbench slug ID (<= 48 chars)."""
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    s = re.sub(r"-+", "-", s)
    if not s:
        s = "project"
    s = s[:48].rstrip("-")
    return s or "project"


def resolve_local_repo_identity(
    repo_dir: Path, env: dict[str, str] | None = None
) -> tuple[str | None, str | None, str]:
    """Return (root_commit, github_slug, repo_name) for a local git repository."""
    root_commit = get_root_commit(repo_dir, env=env)
    remote_url = get_remote_url(repo_dir, env=env)
    github_slug = parse_github_slug(remote_url)
    repo_name = repo_dir.resolve().name
    if github_slug and "/" in github_slug:
        repo_name = github_slug.split("/", 1)[1]
    return root_commit, github_slug, repo_name


def find_project_for_repo(
    projects: list[Project],
    root_commit: str,
    github_slug: str | None = None,
) -> Project | None:
    """Find the project matching root_commit, disambiguating forks by github_slug (§5.4)."""
    matches: list[Project] = []
    for proj in projects:
        for repo_entry in proj.repos:
            if repo_entry.root_commit == root_commit:
                matches.append(proj)
                break

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    # Multiple projects claim the same root commit (forks): disambiguate by github name
    if github_slug:
        gh_lower = github_slug.lower()
        narrowed: list[Project] = []
        for proj in matches:
            for repo_entry in proj.repos:
                if repo_entry.root_commit == root_commit and repo_entry.github and repo_entry.github.lower() == gh_lower:
                    narrowed.append(proj)
                    break
        if len(narrowed) == 1:
            return narrowed[0]

    ids = ", ".join(p.id for p in matches)
    raise AmbiguousRepoIdentityError(
        f"Ambiguous repo identity for root_commit '{root_commit}': claimed by multiple projects ({ids}) "
        f"and could not disambiguate by github slug ({github_slug!r})."
    )
