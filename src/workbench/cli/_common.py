"""Helpers shared by the CLI commands: the data repo clone, worktrees and the push pipeline."""

from __future__ import annotations

import datetime
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer

from workbench import edits
from workbench.agents.hooks import mark_repo_seen
from workbench.config import get_workbench_dir, load_local_config
from workbench.gitops import (
    GitError,
    clone_repo,
    commit_all,
    create_worktree,
    fetch_origin,
    get_changed_files,
    get_head_commit,
    list_files_at_ref,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
    run_git,
    show_file_at_ref,
)
from workbench.privacy import build_free_text_diff, run_llm_privacy_reviewer
from workbench.semantic import check_semantic_diff
from workbench.validate import (
    TODAY_FILE,
    validate_any_file,
    validate_changed_paths,
    validate_data_repo,
    validate_learning_file,
    validate_project_file,
    validate_today_file,
)
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    parse_iso_date_for_yaml,
    to_plain_python,
)


def _get_data_repo_dir(home_dir: Path | None = None) -> Path:
    wb_dir = get_workbench_dir(home_dir=home_dir)
    data_dir = wb_dir / "data"
    if not data_dir.exists():
        cfg = load_local_config(home_dir=home_dir)
        if cfg.data_repo_url:
            try:
                clone_repo(cfg.data_repo_url, data_dir, branch=cfg.branch)
            except GitError as exc:
                typer.echo(f"Error cloning data repo: {exc}", err=True)
                raise typer.Exit(code=2) from None
        else:
            typer.echo(
                "Error: Workbench data repository is not initialized (~/.workbench/data missing).",
                err=True,
            )
            raise typer.Exit(code=2)
    return data_dir


def _load_all_projects(data_dir: Path) -> list:
    from workbench.discovery.register import load_existing_projects

    return list(load_existing_projects(data_dir).values())


def _resolve_worktree_root(path_arg: Path) -> Path:
    """Accept either the worktree directory or the project.yaml/path.yaml file inside it."""
    p = path_arg.resolve()
    if p.is_file():
        # e.g. <wt>/projects/<id>/project.yaml -> walk up to worktree root containing workbench.yaml or .git
        curr = p.parent
        while curr != curr.parent:
            if (curr / ".git").exists() or (curr / "workbench.yaml").exists():
                return curr
            curr = curr.parent
        return p.parent
    return p


PushMessage = str | Callable[[list[str]], str]


def _push_worktree(
    worktree: Path,
    message: PushMessage,
    allow_removals: bool,
    mark_seen: bool,
    *,
    llm_review: bool = True,
    announce: bool = True,
    fetched: bool = False,
) -> str | None:
    """Shared push pipeline; returns the pushed commit sha, or None when nothing changed.

    `message` may be a callable that gets the changed paths. mark_seen records the cwd repo
    as seen for the Stop hook. llm_review=False skips only the LLM privacy reviewer (the
    deterministic privacy scan always runs). announce=False suppresses the result lines so
    the caller prints its own. fetched=True skips the first fetch before rebasing when the
    caller just fetched (a push race still re-fetches on retry).
    """
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    wt_dir = _resolve_worktree_root(worktree)

    if not wt_dir.exists():
        typer.echo(f"Error: worktree '{wt_dir}' does not exist.", err=True)
        raise typer.Exit(code=1)

    # Remove internal baseline marker before checking git status
    baseline_info: dict = {}
    baseline_marker = wt_dir / ".git_wb_baseline.json"
    if baseline_marker.exists():
        try:
            import json

            baseline_info = json.loads(baseline_marker.read_text(encoding="utf-8"))
        except Exception:
            pass
        baseline_marker.unlink(missing_ok=True)

    base_ref = f"origin/{cfg.branch}"
    changed_files = get_changed_files(wt_dir, base_ref=base_ref)
    if not changed_files:
        remove_worktree(data_dir, wt_dir)
        if mark_seen:
            mark_repo_seen(Path.cwd())
        if announce:
            typer.echo("Workbench: no changes to push (worktree removed).")
        return None

    # 1. Check allowed paths
    path_res = validate_changed_paths(changed_files)
    if not path_res.ok:
        for err in path_res.errors:
            typer.echo(f"Validation error: {err}", err=True)
        raise typer.Exit(code=1)

    today_str = datetime.date.today().isoformat()
    today_date = parse_iso_date_for_yaml(today_str)

    all_warnings: list[str] = []
    all_errors: list[str] = []
    combined_free_text_diffs: list[str] = []

    # 2. Process each changed YAML file: overwrite updated/origin, structural & semantic & privacy checks
    for rel_path in changed_files:
        abs_path = wt_dir / rel_path
        if rel_path == TODAY_FILE:
            # The Today's Work list is not project content: entries (or the whole file) going
            # away is normal, so the removal and semantic guards below do not apply to it.
            if abs_path.exists():
                all_errors.extend(validate_today_file(abs_path)[0].errors)
            continue
        old_yaml_str = show_file_at_ref(wt_dir, base_ref, rel_path)
        old_plain: dict | None = None
        if old_yaml_str is not None:
            try:
                old_plain = to_plain_python(load_yaml_str_roundtrip(old_yaml_str))
            except Exception:
                old_plain = None

        if not abs_path.exists():
            # File was deleted!
            if not allow_removals:
                all_errors.append(
                    f"Removed file '{rel_path}' is not allowed without --allow-removals"
                )
            continue

        if rel_path.endswith(".yaml") and rel_path != "workbench.yaml":
            try:
                rt_doc = load_yaml_roundtrip(abs_path)
                plain_before = to_plain_python(rt_doc)
            except Exception as exc:
                all_errors.append(f"{rel_path}: YAML syntax error: {exc}")
                continue

            if not isinstance(plain_before, dict):
                all_errors.append(f"{rel_path}: YAML root must be a mapping")
                continue

            # Check if agent edited `updated` or `origin`
            expected_prev_updated = (
                old_plain.get("updated") if old_plain is not None else baseline_info.get("updated")
            )
            if (
                expected_prev_updated is not None
                and plain_before.get("updated") != expected_prev_updated
            ):
                all_warnings.append(
                    f"Warning ({rel_path}): 'updated' was edited by agent; overwritten by CLI."
                )

            rt_doc["updated"] = today_date

            if rel_path.startswith("projects/"):
                expected_prev_origin = (
                    old_plain.get("origin")
                    if old_plain is not None
                    else baseline_info.get("origin", "agent")
                )
                if (
                    expected_prev_origin is not None
                    and plain_before.get("origin") != expected_prev_origin
                ):
                    all_warnings.append(
                        f"Warning ({rel_path}): 'origin' was edited by agent; overwritten by CLI."
                    )
                # Set CLI-managed origin: if previously discovered and now has phases, upgrade to agent
                cli_origin = expected_prev_origin or "agent"
                if cli_origin == "discovered" and plain_before.get("phases"):
                    cli_origin = "agent"
                rt_doc["origin"] = cli_origin

            dump_yaml_roundtrip(rt_doc, abs_path)

            # Structural + deterministic privacy validation
            if rel_path.startswith("projects/"):
                vres, _, new_plain = validate_project_file(abs_path)
                old_mds: dict[str, str] = {}
                new_mds: dict[str, str] = {}
            else:
                vres, _, new_plain = validate_learning_file(abs_path, check_topics_dir=True)
                # Load old and new topic markdowns for semantic & LLM review
                lp_dir_rel = Path(rel_path).parent.as_posix()
                old_mds = {}
                for old_md_rel in list_files_at_ref(wt_dir, base_ref, f"{lp_dir_rel}/topics"):
                    content = show_file_at_ref(wt_dir, base_ref, old_md_rel)
                    if content is not None:
                        old_mds[Path(old_md_rel).name] = content
                new_mds = {}
                topics_dir = abs_path.parent / "topics"
                if topics_dir.exists():
                    for md_p in sorted(topics_dir.glob("*.md")):
                        new_mds[md_p.name] = md_p.read_text(encoding="utf-8")

            all_errors.extend(vres.errors)
            all_warnings.extend(vres.warnings)

            if new_plain is not None:
                sem_res = check_semantic_diff(
                    old_data=old_plain,
                    new_data=new_plain,
                    allow_removals=allow_removals,
                    old_markdowns=old_mds,
                    new_markdowns=new_mds,
                )
                all_errors.extend(sem_res.errors)
                all_warnings.extend(sem_res.warnings)

                ft_diff = build_free_text_diff(
                    old_data=old_plain,
                    new_data=new_plain,
                    old_markdowns=old_mds,
                    new_markdowns=new_mds,
                )
                if ft_diff:
                    combined_free_text_diffs.append(ft_diff)

        elif rel_path.endswith(".md"):
            vres = validate_any_file(abs_path)
            all_errors.extend(vres.errors)
            all_warnings.extend(vres.warnings)
            new_md_text = abs_path.read_text(encoding="utf-8")
            if old_yaml_str != new_md_text:
                combined_free_text_diffs.append(f"--- {rel_path}\n+ {new_md_text}")

    for w in all_warnings:
        typer.echo(w, err=True)

    if all_errors:
        for err in all_errors:
            typer.echo(f"Validation error: {err}", err=True)
        raise typer.Exit(code=1)

    # 3. LLM privacy reviewer on free-text changes
    full_ft_diff = "\n".join(combined_free_text_diffs)
    if llm_review and full_ft_diff.strip():
        rev_res = run_llm_privacy_reviewer(
            diff_text=full_ft_diff,
            mode=cfg.privacy_review,
            reviewer_command=cfg.reviewer_command,
        )
        for w in rev_res.warnings:
            typer.echo(f"Warning: {w}", err=True)
        if rev_res.errors:
            for err in rev_res.errors:
                typer.echo(f"Privacy review failed: {err}", err=True)
            raise typer.Exit(code=1)

    # 4. Commit in worktree
    if callable(message):
        message = message(changed_files)
    try:
        commit_sha = commit_all(wt_dir, message)
    except GitError as exc:
        typer.echo(f"Git commit error: {exc}", err=True)
        raise typer.Exit(code=2) from None

    # 5. Fetch + rebase onto origin/main + re-validate + push (retry push race up to 3 times)
    for attempt in range(3):
        try:
            if attempt > 0 or not fetched:
                fetch_origin(data_dir, branch=cfg.branch)
        except GitError as exc:
            typer.echo(f"Git fetch error: {exc}", err=True)
            raise typer.Exit(code=2) from None

        if not rebase_onto(wt_dir, target_ref=base_ref):
            typer.echo(
                f"Rebase conflict onto {base_ref}: rebase aborted and worktree kept at {wt_dir}. "
                "Please run `workbench begin` again and reapply your changes.",
                err=True,
            )
            raise typer.Exit(code=1)

        # Re-validate full repo after rebase
        repo_val = validate_data_repo(wt_dir)
        if not repo_val.ok:
            for err in repo_val.errors:
                typer.echo(f"Post-rebase validation error: {err}", err=True)
            raise typer.Exit(code=1)

        if push_head_to_branch(wt_dir, branch=cfg.branch):
            commit_sha = get_head_commit(wt_dir, "HEAD") or commit_sha
            break
    else:
        typer.echo(
            f"Error: failed to push to origin/{cfg.branch} after 3 attempts due to concurrent push race.",
            err=True,
        )
        raise typer.Exit(code=2)

    # 6. Remove worktree on success and mark local repo seen
    remove_worktree(data_dir, wt_dir)
    try:
        run_git(["reset", "--hard", f"origin/{cfg.branch}"], cwd=data_dir, check=False)
    except GitError:
        pass
    if mark_seen:
        mark_repo_seen(Path.cwd())
    if announce:
        typer.echo(f"Pushed {commit_sha[:8]} to {cfg.branch}: {message}")
    return commit_sha


def _open_project_worktree(prefix: str, project_id: str) -> tuple[Path, Path, Path]:
    """Fetch origin and create a fresh worktree; return (data_dir, wt_dir, project.yaml path)."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2) from None
    wt_dir = get_workbench_dir() / "work" / f"{prefix}-{project_id}-{secrets.token_hex(4)}"
    try:
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2) from None
    target = wt_dir / "projects" / project_id / "project.yaml"
    if not target.exists():
        remove_worktree(data_dir, wt_dir)
        typer.echo(f"Error: project '{project_id}' does not exist.", err=True)
        raise typer.Exit(code=1)
    return data_dir, wt_dir, target


def _fail(message: str, code: int = 1) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=code)


def _check_input(check: Callable[[], Any]) -> Any:
    """Run an edits.require_* check before touching git; an EditError becomes Exit(1)."""
    try:
        return check()
    except edits.EditError as exc:
        _fail(str(exc))


def _quick_edit(
    prefix: str, project_id: str, mutate: Callable[[Any], Any], message: Callable[[Any], str]
) -> tuple[Any, str | None]:
    """Edit one project.yaml in a fresh worktree and push it on the quick path.

    Quick path = the full push pipeline (validation, deterministic privacy scan, semantic
    check without removals, rebase/retry) minus the LLM privacy reviewer, reusing the fetch
    made when the worktree was opened. Returns (mutate's result, pushed sha or None when
    nothing changed). The worktree is always removed; the cwd repo is marked seen.
    """
    data_dir, wt_dir, target = _open_project_worktree(prefix, project_id)
    try:
        doc = load_yaml_roundtrip(target)
        try:
            result = mutate(doc)
        except edits.EditError as exc:
            hint = (
                f" (list ids with `workbench show --id {project_id} --brief`)"
                if exc.status_code == 404
                else ""
            )
            _fail(f"{exc}{hint}")
        dump_yaml_roundtrip(doc, target)
        sha = _push_worktree(
            wt_dir,
            message(result),
            allow_removals=False,
            mark_seen=True,
            llm_review=False,
            announce=False,
            fetched=True,
        )
    finally:
        if wt_dir.exists():
            remove_worktree(data_dir, wt_dir)
    return result, sha
