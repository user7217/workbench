"""Agent hook entry points: hook-check and the headless auto-update job."""

from __future__ import annotations

import datetime
import json
import sys
import time
from pathlib import Path

import typer

from workbench import commitmsg
from workbench.agents import updater
from workbench.agents.hooks import (
    evaluate_hook_input,
    get_repo_focus,
    mark_repo_seen,
    set_repo_focus,
)
from workbench.cli._common import (
    _fail,
    _get_data_repo_dir,
    _load_all_projects,
    _open_project_worktree,
    _push_worktree,
)
from workbench.config import get_workbench_dir, load_local_config
from workbench.gitops import get_repo_toplevel, remove_worktree, run_git
from workbench.identity import (
    AmbiguousRepoIdentityError,
    find_project_for_repo,
    resolve_local_repo_identity,
)
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_roundtrip

app = typer.Typer()


@app.command("hook-check")
def cmd_hook_check(
    mark_seen: bool = typer.Option(
        False, "--mark-seen", help="Record current repo HEAD as seen without prompting"
    ),
) -> None:
    """Agent Stop hook entry point (§9.3)."""
    if mark_seen:
        mark_repo_seen(Path.cwd())
        return

    stdin_text = ""
    if not sys.stdin.isatty():
        try:
            stdin_text = sys.stdin.read()
        except Exception:
            stdin_text = ""

    out = evaluate_hook_input(stdin_text)
    if out:
        typer.echo(out)


# Give the agent time to finish writing its transcript after the Stop hook fired
JOB_START_DELAY_SECONDS = 2.0


@app.command("auto-update")
def cmd_auto_update(
    job: Path | None = typer.Option(None, "--job", help="Job file written by the Stop hook"),
    transcript: Path | None = typer.Option(
        None,
        "--transcript",
        help="Claude transcript (.jsonl) to read the last message from, to try it by hand",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the prompt and the agent's plan; change nothing"
    ),
) -> None:
    """Headless per-message update: a background agent records the last message (update_mode = "headless").

    The Stop hook runs this with --job. To try it by hand, run it inside a project repo with
    --transcript and --dry-run to see what the agent would record.
    """
    if (job is None) == (transcript is None):
        _fail("give either --job or --transcript.")
    if job is not None:
        try:
            job_data = json.loads(job.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _fail(f"cannot read job file: {exc}")
        time.sleep(JOB_START_DELAY_SECONDS)
    else:
        cwd = Path.cwd()
        top = get_repo_toplevel(cwd) or cwd
        job_data = {
            "payload": {"transcript_path": str(transcript.resolve())},
            "repo_top": str(top),
            "focus": get_repo_focus(cwd)[1],
            "previous_commit": None,
        }

    project_id = "-"
    try:
        with _updater_lock():
            project_id, result = _run_auto_update(job_data, dry_run)
    except typer.Exit:
        result = "failed (see the error above)"
    except updater.UpdaterError as exc:
        result = f"failed: {exc}"
    except Exception as exc:  # a background job must log, never crash silently
        result = f"failed: {type(exc).__name__}: {exc}"
    finally:
        if job is not None:
            job.unlink(missing_ok=True)
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')} {project_id} {result}"
    if job is not None:
        log = updater.log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    else:
        typer.echo(f"Workbench: {result}")


class _updater_lock:
    """One headless update at a time, so back-to-back messages never race on the data clone."""

    def __enter__(self) -> _updater_lock:
        path = get_workbench_dir() / "updater.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w")
        try:
            import fcntl

            fcntl.flock(self._fh, fcntl.LOCK_EX)
        except ImportError:  # Windows outside WSL: no flock, run unlocked
            pass
        return self

    def __exit__(self, *exc: object) -> None:
        self._fh.close()


def _job_project(repo_top: Path, focus: str | None) -> str | None:
    """The focus's project if it exists, else the project this repo is registered under."""
    data_dir = _get_data_repo_dir()
    focus_project = (focus or "").split("/")[0]
    if focus_project and (data_dir / "projects" / focus_project / "project.yaml").exists():
        return focus_project
    root_commit, github_slug, _ = resolve_local_repo_identity(repo_top)
    if not root_commit:
        return None
    try:
        matched = find_project_for_repo(
            _load_all_projects(data_dir), root_commit=root_commit, github_slug=github_slug
        )
    except AmbiguousRepoIdentityError:
        return None
    return matched.id if matched else None


def _repo_changes(repo_top: Path, previous_commit: str | None) -> str:
    """git diff --stat against HEAD, untracked files, and commits made since the last message."""
    parts: list[str] = []
    stat = run_git(["diff", "--stat", "HEAD"], cwd=repo_top, check=False).stdout.strip()
    if stat:
        parts.append("Uncommitted changes:\n" + "\n".join(stat.splitlines()[-40:]))
    untracked = run_git(
        ["ls-files", "--others", "--exclude-standard"], cwd=repo_top, check=False
    ).stdout.split()
    if untracked:
        parts.append("New files:\n" + "\n".join(untracked[:20]))
    if previous_commit:
        log = run_git(
            ["log", "--oneline", "-n", "10", f"{previous_commit}..HEAD"], cwd=repo_top, check=False
        ).stdout.strip()
        if log:
            parts.append("New commits:\n" + log)
    return "\n\n".join(parts)


def _run_auto_update(job_data: dict, dry_run: bool) -> tuple[str, str]:
    """(project id, one-line result) for one job; raises UpdaterError / typer.Exit on failure."""
    cfg = load_local_config()
    payload = job_data.get("payload") or {}
    repo_top = Path(job_data.get("repo_top") or Path.cwd())
    focus = job_data.get("focus")
    exchange = updater.exchange_from_hook(payload, job_data.get("transcript_size"))
    if exchange is None:
        return "-", "skipped: could not read the last message"
    project_id = _job_project(repo_top, focus)
    if project_id is None:
        return "-", "skipped: this repo is not on Workbench"

    data_dir, wt_dir, target = _open_project_worktree("auto", project_id)
    try:
        prompt = updater.build_prompt(
            project_id,
            target.read_text(encoding="utf-8"),
            exchange,
            _repo_changes(repo_top, job_data.get("previous_commit")),
            focus,
        )
        if dry_run:
            typer.echo(f"--- prompt ({len(prompt)} chars) ---\n{prompt}")
        plan = updater.parse_reply(updater.run_agent(cfg.updater_command, prompt))
        doc = load_yaml_roundtrip(target)
        applied = updater.apply_plan(doc, project_id, plan, datetime.date.today().isoformat())
        skipped = f" (skipped: {'; '.join(applied.skipped)})" if applied.skipped else ""
        if dry_run:
            typer.echo(f"--- plan ---\n{json.dumps(plan.__dict__, indent=2)}")
            return (
                project_id,
                f"dry run, would record: {'; '.join(applied.changes) or 'nothing'}{skipped}",
            )
        if not applied.changes:
            return project_id, f"no update needed{skipped}"
        dump_yaml_roundtrip(doc, target)
        scope, summary = updater.commit_scope_and_summary(project_id, plan, applied)
        message = commitmsg.build_message("update", summary, scope)
        sha = _push_worktree(
            wt_dir,
            message,
            allow_removals=False,
            mark_seen=False,  # the hook already recorded this message's state as seen
            llm_review=False,
            announce=False,
            fetched=True,
        )
    finally:
        if wt_dir.exists():
            remove_worktree(data_dir, wt_dir)
    touched = list(dict.fromkeys(applied.touched))
    if len(touched) == 1:
        set_repo_focus(repo_top, f"{project_id}/{touched[0]}")
    return project_id, f"{message} ({(sha or '')[:8]}){skipped}"
