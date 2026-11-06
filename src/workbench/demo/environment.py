"""Build the artificial demo environment from the data files under data/.

data/repos.yaml lists the fake code repos, data/seed/ mirrors the seeded workbench-data repo,
and data/github_fixtures.yaml feeds the fake GitHub server. Seed YAML names commits of the
fake repos with placeholders, filled in once the repos exist:

- {{root:<repo>}}: the repo's full root commit SHA
- {{first:<repo>}} / {{last:<repo>}}: its first / last commit, shortened to 12 characters
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from workbench.config import DEFAULT_REVIEWER_COMMAND, WorkbenchLocalConfig, save_local_config
from workbench.gitops import (
    clone_repo,
    commit_all,
    get_root_commit,
    init_bare_repo,
    init_repo,
    make_isolated_git_env,
    push_head_to_branch,
    run_git,
)
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    to_plain_python,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
SEED_DIR = DATA_DIR / "seed"

_COMMIT_PLACEHOLDER = re.compile(r"\{\{(root|first|last):([a-z0-9-]+)\}\}")

# (root_commit, all_commit_shas) of each fake code repo, keyed by its directory name
RepoCommits = dict[str, tuple[str, list[str]]]


def _load_data_yaml(name: str) -> Any:
    return to_plain_python(load_yaml_roundtrip(DATA_DIR / name))


def _create_project_git_repo(
    repo_dir: Path,
    github_slug: str,
    files: dict[str, str],
    extra_commits: list[tuple[str, dict[str, str]]] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Create a fake git repo with real commits and return (root_commit, all_commit_shas)."""
    init_repo(repo_dir, branch="main", env=env)
    run_git(
        ["remote", "add", "origin", f"https://github.com/{github_slug}.git"],
        cwd=repo_dir,
        env=env,
    )
    for rel, content in files.items():
        fpath = repo_dir / rel
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    first_sha = commit_all(repo_dir, f"Initial commit for {github_slug}", env=env)
    shas = [first_sha]

    for msg, changes in extra_commits or []:
        for rel, content in changes.items():
            fpath = repo_dir / rel
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        sha = commit_all(repo_dir, msg, env=env)
        shas.append(sha)

    root_sha = get_root_commit(repo_dir, env=env) or first_sha
    return root_sha, shas


def _create_code_repos(code_dir: Path, env: dict[str, str]) -> RepoCommits:
    """Create every repo in data/repos.yaml under code_dir, in file order."""
    commits: RepoCommits = {}
    for name, spec in _load_data_yaml("repos.yaml").items():
        repo_env = env
        author = spec.get("author")
        if author:
            # e.g. a third-party clone, which discovery must not register
            repo_env = dict(env)
            repo_env["GIT_AUTHOR_NAME"] = author["name"]
            repo_env["GIT_AUTHOR_EMAIL"] = author["email"]
            repo_env["GIT_COMMITTER_NAME"] = author["name"]
            repo_env["GIT_COMMITTER_EMAIL"] = author["email"]
        extra = [(c["message"], c["files"]) for c in spec.get("commits", [])]
        commits[name] = _create_project_git_repo(
            code_dir / name, spec["github"], spec["files"], extra, env=repo_env
        )
    return commits


def _fill_commit_placeholders(text: str, commits: RepoCommits) -> str:
    def commit_for(match: re.Match[str]) -> str:
        kind, repo = match.groups()
        root, shas = commits[repo]
        if kind == "root":
            return root
        return (shas[0] if kind == "first" else shas[-1])[:12]

    return _COMMIT_PLACEHOLDER.sub(commit_for, text)


def _write_seed_files(seed_dir: Path, commits: RepoCommits) -> None:
    """Copy data/seed into seed_dir; project.yaml/path.yaml are filled in and round-tripped."""
    for src in sorted(SEED_DIR.rglob("*")):
        if not src.is_file() or src.suffix not in (".yaml", ".md"):
            continue
        dest = seed_dir / src.relative_to(SEED_DIR)
        text = src.read_text(encoding="utf-8")
        if src.name in ("project.yaml", "path.yaml"):
            data = load_yaml_str_roundtrip(_fill_commit_placeholders(text, commits))
            dump_yaml_roundtrip(data, dest)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")


def _github_fixtures(commits: RepoCommits) -> dict[str, Any]:
    """data/github_fixtures.yaml with each root_commit_of replaced by that repo's root_commit."""
    fixtures = _load_data_yaml("github_fixtures.yaml")
    repos = []
    for entry in fixtures["repos"]:
        repo = {k: v for k, v in entry.items() if k != "root_commit_of"}
        repo["root_commit"] = commits[entry["root_commit_of"]][0]
        repos.append(repo)
    fixtures["repos"] = repos
    return fixtures


def init_demo_environment(base_dir: Path) -> dict[str, Any]:
    """Build the complete artificial Workbench environment under base_dir."""
    base_dir = base_dir.resolve()
    remote_bare = base_dir / "remote" / "workbench-data.git"
    laptop_home = base_dir / "laptop"
    code_dir = base_dir / "code"

    env = make_isolated_git_env(laptop_home)
    env["GIT_AUTHOR_NAME"] = "Workbench Demo"
    env["GIT_AUTHOR_EMAIL"] = "demo@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Workbench Demo"
    env["GIT_COMMITTER_EMAIL"] = "demo@example.invalid"

    # 1. Initialize bare workbench-data.git
    init_bare_repo(remote_bare, branch="main", env=env)

    # 2. Create fake project repos in <dir>/code/
    commits = _create_code_repos(code_dir, env)

    # 3. Seed workbench-data via a staging repo, then push to bare remote
    seed_dir = base_dir / "_seed_work"
    init_repo(seed_dir, branch="main", env=env)
    run_git(["remote", "add", "origin", str(remote_bare)], cwd=seed_dir, env=env)
    _write_seed_files(seed_dir, commits)
    commit_all(seed_dir, "Seed initial demo Workbench metadata", env=env)
    push_head_to_branch(seed_dir, branch="main", env=env)

    # 4. Clone workbench-data into <laptop>/.workbench/data and write config.toml
    laptop_wb_dir = laptop_home / ".workbench"
    laptop_data_dir = laptop_wb_dir / "data"
    clone_repo(str(remote_bare), laptop_data_dir, branch="main", env=env)

    # Create fixture data for fake GitHub server
    fixtures_path = base_dir / "github_fixtures.json"
    fixtures_path.write_text(
        json.dumps(_github_fixtures(commits), indent=2) + "\n", encoding="utf-8"
    )

    cfg = WorkbenchLocalConfig(
        data_repo_url=str(remote_bare),
        branch="main",
        scan_roots=[str(code_dir)],
        github_owners=["demouser"],
        user_emails=["demo@example.invalid"],
        privacy_review="off",
        reviewer_command=DEFAULT_REVIEWER_COMMAND,
    )
    save_local_config(cfg, home_dir=laptop_home, env=env)

    # Clean up temporary seed working directory
    import shutil

    shutil.rmtree(seed_dir, ignore_errors=True)

    return {
        "base_dir": str(base_dir),
        "remote_bare": str(remote_bare),
        "laptop_home": str(laptop_home),
        "laptop_data_dir": str(laptop_data_dir),
        "code_dir": str(code_dir),
        "github_fixtures": str(fixtures_path),
    }
