"""Data-repo history commands: history, undo."""

from __future__ import annotations

import secrets

import typer

from workbench import commitmsg
from workbench.cli._common import _get_data_repo_dir
from workbench.config import get_workbench_dir, load_local_config
from workbench.gitops import (
    GitError,
    create_worktree,
    fetch_origin,
    get_head_commit,
    push_head_to_branch,
    remove_worktree,
    run_git,
)

app = typer.Typer()


@app.command("history")
def cmd_history(
    item_id: str | None = typer.Argument(None, help="Optional project or learning path ID"),
    limit: int = typer.Option(20, "-n", "--limit", help="Max commits to show"),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help=f"Only these commit kinds, comma-separated ({', '.join(commitmsg.KINDS)})",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Only commits scoped to this project (or learning/<id>), including its items",
    ),
) -> None:
    """Show recent data-repo commits, optionally filtered by kind and scope."""
    kinds: set[str] = set()
    if kind:
        kinds = {k.strip() for k in kind.split(",") if k.strip()}
        unknown = sorted(kinds - set(commitmsg.KINDS))
        if unknown:
            typer.echo(
                f"Error: unknown kind(s) {', '.join(unknown)} (choose from {', '.join(commitmsg.KINDS)}).",
                err=True,
            )
            raise typer.Exit(code=1)

    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    fetch_origin(data_dir, branch=cfg.branch)

    paths = ["--", f"projects/{item_id}", f"learning/{item_id}"] if item_id else []
    if not kinds and not project:
        args = ["log", f"origin/{cfg.branch}", f"-n{limit}", "--oneline", *paths]
        typer.echo(run_git(args, cwd=data_dir).stdout, nl=False)
        return

    # Filter on the parsed `<kind>(<scope>): ...` subject; legacy messages never match a filter.
    proc = run_git(["log", f"origin/{cfg.branch}", "--format=%h %s", *paths], cwd=data_dir)
    shown = 0
    for line in proc.stdout.splitlines():
        if shown >= limit:
            break
        _, _, subject = line.partition(" ")
        parsed = commitmsg.parse_message(subject)
        if parsed is None:
            continue
        if kinds and parsed.kind not in kinds:
            continue
        if project and not commitmsg.scope_matches(parsed.scope, project):
            continue
        typer.echo(line)
        shown += 1


@app.command("undo")
def cmd_undo(
    commit: str = typer.Argument(..., help="Commit SHA in workbench-data to revert"),
) -> None:
    """Revert a commit in workbench-data and push to origin/main."""
    wb_dir = get_workbench_dir()
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    fetch_origin(data_dir, branch=cfg.branch)

    wt_dir = wb_dir / "work" / f"undo-{secrets.token_hex(4)}"
    create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    try:
        target = run_git(["log", "-1", "--format=%H%n%s", commit], cwd=wt_dir, check=False)
        if target.returncode != 0:
            typer.echo(f"Error reverting {commit}: unknown commit.", err=True)
            raise typer.Exit(code=2)
        full_sha, _, subject = target.stdout.strip().partition("\n")
        proc = run_git(["revert", "--no-commit", full_sha], cwd=wt_dir, check=False)
        if proc.returncode != 0:
            typer.echo(f"Error reverting {commit}: {proc.stderr.strip()}", err=True)
            raise typer.Exit(code=2)
        parsed = commitmsg.parse_message(subject)
        message = commitmsg.build_message(
            "undo", f"revert {full_sha[:8]} ({subject})", parsed.scope if parsed else None
        )
        proc = run_git(["commit", "-m", message], cwd=wt_dir, check=False)
        if proc.returncode != 0:
            typer.echo(
                f"Error reverting {commit}: {proc.stdout.strip() or proc.stderr.strip()}", err=True
            )
            raise typer.Exit(code=2)

        if not push_head_to_branch(wt_dir, branch=cfg.branch):
            typer.echo("Error pushing revert commit.", err=True)
            raise typer.Exit(code=2)

        new_sha = get_head_commit(wt_dir, "HEAD") or ""
        try:
            run_git(["reset", "--hard", f"origin/{cfg.branch}"], cwd=data_dir, check=False)
        except GitError:
            pass
        typer.echo(f"Reverted {full_sha[:8]} -> pushed {new_sha[:8]}")
    finally:
        remove_worktree(data_dir, wt_dir)
