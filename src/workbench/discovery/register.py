"""Register newly discovered repositories into workbench-data."""

from __future__ import annotations

import datetime
import secrets
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from workbench.commitmsg import build_message
from workbench.gitops import (
    commit_all,
    create_worktree,
    fetch_origin,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
)
from workbench.identity import slugify_name
from workbench.models import Project
from workbench.privacy import scan_text_value
from workbench.validate import validate_project_file
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_roundtrip, parse_iso_date_for_yaml, to_plain_python


@dataclass
class DiscoveredRepoCandidate:
    name: str
    root_commit: str
    github: str | None = None
    description: str = ""


def load_existing_projects(repo_root: Path) -> dict[str, Project]:
    """Load all valid Project models from repo_root/projects."""
    projects: dict[str, Project] = {}
    projects_dir = repo_root / "projects"
    if not projects_dir.exists():
        return projects
    for pdir in sorted(projects_dir.iterdir()):
        pfile = pdir / "project.yaml"
        if pdir.is_dir() and pfile.exists():
            res, proj, _ = validate_project_file(pfile, expected_dir_id=pdir.name)
            if proj is not None:
                projects[proj.id] = proj
            else:
                # Even if validation had minor issues, inspect raw dict for root_commit
                try:
                    raw = to_plain_python(load_yaml_roundtrip(pfile))
                    if isinstance(raw, dict) and "id" in raw:
                        projects[str(raw["id"])] = Project.model_construct(**raw)
                except Exception:
                    pass
    return projects


def sanitize_discovered_description(desc: str | None) -> str:
    """Clean and privacy-scan a repo description; return blank if it fails privacy scan."""
    if not desc:
        return ""
    cleaned = desc.strip()[:500]
    if scan_text_value(cleaned, "description"):
        return ""
    return cleaned


def build_minimal_discovered_project_map(
    proj_id: str,
    candidate: DiscoveredRepoCandidate,
    today: datetime.date | None = None,
) -> CommentedMap:
    """Create a round-trip CommentedMap for a newly discovered project (§5.1)."""
    d = today or datetime.date.today()
    cm = CommentedMap()
    cm["schema_version"] = 1
    cm["id"] = proj_id
    cm["name"] = candidate.name[:80]
    cm["description"] = sanitize_discovered_description(candidate.description)
    cm["status"] = "active"
    cm["origin"] = "discovered"
    cm["updated"] = parse_iso_date_for_yaml(d.isoformat())
    cm["uses_phases"] = False
    repo_entry = CommentedMap()
    if candidate.github:
        repo_entry["github"] = candidate.github
    repo_entry["root_commit"] = candidate.root_commit
    repo_entry["name"] = candidate.name[:80]
    cm["repos"] = [repo_entry]
    cm["links"] = []
    cm["phases"] = []
    cm["topics"] = []
    return cm


def register_discovered_repos(
    data_repo_dir: Path,
    candidates: list[DiscoveredRepoCandidate],
    branch: str = "main",
    work_root: Path | None = None,
    env: dict[str, str] | None = None,
    commit_source: str = "discovery",
) -> list[str]:
    """Register unknown candidate repos and push all new entries in a single commit.

    Returns the list of newly registered project IDs.
    """
    if not candidates:
        return []

    fetch_origin(data_repo_dir, branch=branch, env=env)
    wt_parent = work_root or (data_repo_dir.parent / "work")
    wt_dir = wt_parent / f"discover-{secrets.token_hex(4)}"
    create_worktree(data_repo_dir, wt_dir, ref=f"origin/{branch}", env=env)

    try:
        existing_projects = load_existing_projects(wt_dir)
        known_root_commits: set[str] = set()
        known_githubs: set[str] = set()
        used_ids: set[str] = set(existing_projects.keys())

        for proj in existing_projects.values():
            for r in proj.repos:
                if isinstance(r, dict):
                    rc = r.get("root_commit")
                    gh = r.get("github")
                else:
                    rc = r.root_commit
                    gh = r.github
                if rc:
                    known_root_commits.add(rc)
                if gh:
                    known_githubs.add(gh.lower())

        registered_ids: list[str] = []
        for cand in candidates:
            if not cand.root_commit:
                continue
            if cand.root_commit in known_root_commits:
                continue
            if cand.github and cand.github.lower() in known_githubs:
                continue

            base_id = slugify_name(cand.name)
            proj_id = base_id
            suffix = 2
            while proj_id in used_ids:
                s_str = f"-{suffix}"
                proj_id = f"{base_id[:48 - len(s_str)]}{s_str}"
                suffix += 1

            used_ids.add(proj_id)
            known_root_commits.add(cand.root_commit)
            if cand.github:
                known_githubs.add(cand.github.lower())

            cm = build_minimal_discovered_project_map(proj_id, cand)
            target_file = wt_dir / "projects" / proj_id / "project.yaml"
            dump_yaml_roundtrip(cm, target_file)

            # Validate before committing
            vres, _, _ = validate_project_file(target_file, expected_dir_id=proj_id)
            if not vres.ok:
                target_file.unlink(missing_ok=True)
                continue

            registered_ids.append(proj_id)

        if not registered_ids:
            return []

        msg = build_message(
            "discover",
            f"register {len(registered_ids)} repo(s) via {commit_source}: {', '.join(registered_ids)}",
        )
        commit_all(wt_dir, msg, env=env)

        for _attempt in range(3):
            fetch_origin(data_repo_dir, branch=branch, env=env)
            if not rebase_onto(wt_dir, target_ref=f"origin/{branch}", env=env):
                raise RuntimeError("Rebase conflict during discovery push")
            if push_head_to_branch(wt_dir, branch=branch, env=env):
                break
        else:
            raise RuntimeError("Failed to push discovered repos after 3 attempts")

        return registered_ids
    finally:
        remove_worktree(data_repo_dir, wt_dir, env=env)
