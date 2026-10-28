"""All Git subprocess operations with isolated environment support."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when a git command fails."""


def make_isolated_git_env(
    home_dir: Path, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Create a completely isolated git environment pointing HOME and gitconfig to home_dir."""
    env = dict(os.environ if base_env is None else base_env)
    home_dir.mkdir(parents=True, exist_ok=True)
    gitconfig = home_dir / ".gitconfig"
    if not gitconfig.exists():
        gitconfig.write_text(
            "[user]\n\tname = Workbench Test\n\temail = test@example.invalid\n"
            "[init]\n\tdefaultBranch = main\n",
            encoding="utf-8",
        )
    env["HOME"] = str(home_dir)
    env["GIT_CONFIG_GLOBAL"] = str(gitconfig)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_AUTHOR_NAME", "Workbench Test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "Workbench Test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.invalid")
    return env


def _prepare_env(env: dict[str, str] | None = None) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    merged.setdefault("GIT_AUTHOR_NAME", "Workbench")
    merged.setdefault("GIT_AUTHOR_EMAIL", "workbench@example.invalid")
    merged.setdefault("GIT_COMMITTER_NAME", "Workbench")
    merged.setdefault("GIT_COMMITTER_EMAIL", "workbench@example.invalid")
    return merged


def run_git(
    args: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with the given arguments and environment."""
    run_env = _prepare_env(env)
    cmd = ["git"] + args
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=run_env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (code {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def git_output_bytes(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> bytes | None:
    """Raw stdout of a git command (no text decoding), or None if it failed."""
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), env=_prepare_env(env), capture_output=True, check=False
    )
    return proc.stdout if proc.returncode == 0 else None


def is_git_repo(path: Path, env: dict[str, str] | None = None) -> bool:
    """Return True if path is inside a git work tree."""
    if not path.exists():
        return False
    proc = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path, env=env, check=False)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def get_repo_toplevel(path: Path, env: dict[str, str] | None = None) -> Path | None:
    """Return top-level directory of git repository containing path."""
    proc = run_git(["rev-parse", "--show-toplevel"], cwd=path, env=env, check=False)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def init_bare_repo(path: Path, branch: str = "main", env: dict[str, str] | None = None) -> None:
    """Initialize a bare git repository with the specified default branch."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(["init", "--bare", f"--initial-branch={branch}"], cwd=path, env=env)


def init_repo(path: Path, branch: str = "main", env: dict[str, str] | None = None) -> None:
    """Initialize a standard git repository."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(["init", f"--initial-branch={branch}"], cwd=path, env=env)


def clone_repo(
    remote_url: str,
    dest: Path,
    branch: str = "main",
    env: dict[str, str] | None = None,
) -> None:
    """Clone remote repository into dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "--branch", branch, remote_url, str(dest)], env=env)


def ls_remote(remote_url: str, env: dict[str, str] | None = None) -> bool:
    """Verify remote repository access with git ls-remote."""
    proc = run_git(["ls-remote", remote_url], env=env, check=False)
    return proc.returncode == 0


def fetch_origin(
    repo_dir: Path, branch: str = "main", env: dict[str, str] | None = None
) -> None:
    """Fetch origin/<branch> in repo_dir."""
    run_git(["fetch", "origin", branch], cwd=repo_dir, env=env)


def get_head_commit(
    repo_dir: Path, ref: str = "HEAD", env: dict[str, str] | None = None
) -> str | None:
    """Get full commit SHA for ref, or None if ref does not exist."""
    proc = run_git(["rev-parse", "--verify", ref], cwd=repo_dir, env=env, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def get_root_commit(repo_dir: Path, env: dict[str, str] | None = None) -> str | None:
    """Get canonical root commit SHA (lexicographically smallest if multiple roots, §5.4).

    Returns None if the repository has no commits yet.
    """
    proc = run_git(
        ["rev-list", "--max-parents=0", "HEAD"], cwd=repo_dir, env=env, check=False
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    roots = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if not roots:
        return None
    return sorted(roots)[0]


def get_remote_url(
    repo_dir: Path, remote: str = "origin", env: dict[str, str] | None = None
) -> str | None:
    """Get URL of the specified remote, or None if not configured."""
    proc = run_git(["remote", "get-url", remote], cwd=repo_dir, env=env, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def get_commit_emails(repo_dir: Path, env: dict[str, str] | None = None) -> set[str]:
    """Return set of lowercased author emails across commits in repo_dir."""
    proc = run_git(["log", "--format=%ae"], cwd=repo_dir, env=env, check=False)
    if proc.returncode != 0:
        return set()
    return {line.strip().lower() for line in proc.stdout.splitlines() if line.strip()}


def create_worktree(
    repo_dir: Path,
    worktree_path: Path,
    ref: str = "origin/main",
    env: dict[str, str] | None = None,
) -> None:
    """Create a detached git worktree at worktree_path from ref."""
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    run_git(["worktree", "prune"], cwd=repo_dir, env=env, check=False)
    run_git(
        ["worktree", "add", "--detach", str(worktree_path), ref],
        cwd=repo_dir,
        env=env,
    )


def remove_worktree(
    repo_dir: Path, worktree_path: Path, env: dict[str, str] | None = None
) -> None:
    """Remove a git worktree and prune metadata."""
    run_git(
        ["worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_dir,
        env=env,
        check=False,
    )
    run_git(["worktree", "prune"], cwd=repo_dir, env=env, check=False)


def get_changed_files(
    worktree_path: Path, base_ref: str = "origin/main", env: dict[str, str] | None = None
) -> list[str]:
    """Return list of repo-relative paths modified, added, or deleted relative to base_ref."""
    changed: set[str] = set()
    # Committed or staged/unstaged vs base_ref
    p1 = run_git(["diff", "--name-only", base_ref], cwd=worktree_path, env=env, check=False)
    if p1.returncode == 0:
        for line in p1.stdout.splitlines():
            if line.strip():
                changed.add(line.strip())
    # Untracked files
    p2 = run_git(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=worktree_path,
        env=env,
        check=False,
    )
    if p2.returncode == 0:
        for line in p2.stdout.splitlines():
            if line.strip():
                changed.add(line.strip())
    return sorted(changed)


def show_file_at_ref(
    repo_dir: Path, ref: str, rel_path: str, env: dict[str, str] | None = None
) -> str | None:
    """Return content of rel_path at git ref, or None if it did not exist."""
    proc = run_git(["show", f"{ref}:{rel_path}"], cwd=repo_dir, env=env, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def list_files_at_ref(
    repo_dir: Path, ref: str, rel_dir: str, env: dict[str, str] | None = None
) -> list[str]:
    """List repo-relative file paths under rel_dir at ref."""
    proc = run_git(["ls-tree", "-r", "--name-only", ref, rel_dir], cwd=repo_dir, env=env, check=False)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def commit_all(
    repo_dir: Path, message: str, env: dict[str, str] | None = None
) -> str:
    """Stage all changes and create a commit, returning the new commit SHA."""
    run_git(["add", "-A"], cwd=repo_dir, env=env)
    status = run_git(["status", "--porcelain"], cwd=repo_dir, env=env)
    if not status.stdout.strip():
        head = get_head_commit(repo_dir, "HEAD", env=env)
        return head or ""
    run_git(["commit", "-m", message], cwd=repo_dir, env=env)
    return get_head_commit(repo_dir, "HEAD", env=env) or ""


def rebase_onto(
    worktree_path: Path, target_ref: str = "origin/main", env: dict[str, str] | None = None
) -> bool:
    """Rebase current HEAD onto target_ref. If conflict occurs, abort rebase and return False."""
    proc = run_git(["rebase", target_ref], cwd=worktree_path, env=env, check=False)
    if proc.returncode != 0:
        run_git(["rebase", "--abort"], cwd=worktree_path, env=env, check=False)
        return False
    return True


def push_head_to_branch(
    worktree_path: Path, branch: str = "main", env: dict[str, str] | None = None
) -> bool:
    """Push HEAD to origin refs/heads/<branch>. Returns True on success, False on race/rejection."""
    proc = run_git(
        ["push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=worktree_path,
        env=env,
        check=False,
    )
    return proc.returncode == 0
