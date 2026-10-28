"""Typer CLI application for Workbench (§8)."""

from __future__ import annotations

import datetime
import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from typing import Optional, get_args

import typer
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from workbench.config import (
    WorkbenchLocalConfig,
    get_workbench_dir,
    load_local_config,
    save_local_config,
)
from workbench.demo_env import init_demo_environment
from workbench.discovery.local import run_local_discovery
from workbench.gitops import (
    GitError,
    clone_repo,
    commit_all,
    create_worktree,
    fetch_origin,
    get_changed_files,
    get_head_commit,
    is_git_repo,
    list_files_at_ref,
    ls_remote,
    push_head_to_branch,
    rebase_onto,
    remove_worktree,
    run_git,
    show_file_at_ref,
)
from workbench.hooks import evaluate_hook_input, mark_repo_seen
from workbench.identity import (
    AmbiguousRepoIdentityError,
    find_project_for_repo,
    resolve_local_repo_identity,
    slugify_name,
)
from workbench.install import run_install
from workbench.learning_md import (
    export_learning_markdown,
    merge_learning_paths,
    parse_learning_markdown,
)
from workbench.models import Project, ProjectStatus
from workbench.privacy import build_free_text_diff, run_llm_privacy_reviewer
from workbench.semantic import check_semantic_diff
from workbench.validate import (
    validate_any_file,
    validate_changed_paths,
    validate_data_repo,
    validate_learning_file,
    validate_project_file,
)
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    parse_iso_date_for_yaml,
    to_plain_python,
)

app = typer.Typer(help="Workbench CLI — personal inventory of software projects and learning paths.")
learning_app = typer.Typer(help="Manage learning paths.")
demo_app = typer.Typer(help="Manage artificial demo environment.")
app.add_typer(learning_app, name="learning")
app.add_typer(demo_app, name="demo")


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
                raise typer.Exit(code=2)
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


@app.command("show")
def cmd_show(
    project_id: Optional[str] = typer.Option(None, "--id", help="Explicit project ID to show"),
) -> None:
    """In a project repo: print that project's YAML, or 'unregistered' plus instructions."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
        run_git(["reset", "--hard", f"origin/{cfg.branch}"], cwd=data_dir, check=False)
    except GitError:
        pass

    if project_id:
        pfile = data_dir / "projects" / project_id / "project.yaml"
        if pfile.exists():
            typer.echo(pfile.read_text(encoding="utf-8"), nl=False)
            return
        typer.echo(
            f"unregistered: project '{project_id}' is not registered in Workbench.\n"
            "Run 'workbench begin' inside the project repository to register and edit it."
        )
        return

    cwd = Path.cwd()
    if not is_git_repo(cwd):
        typer.echo("Error: current directory is not a git repository.", err=True)
        raise typer.Exit(code=1)

    root_commit, github_slug, repo_name = resolve_local_repo_identity(cwd)
    if not root_commit:
        typer.echo(
            f"unregistered: repository '{repo_name}' has no commits yet.\n"
            "Create an initial commit first, then run 'workbench begin'."
        )
        return

    projects = _load_all_projects(data_dir)
    try:
        matched = find_project_for_repo(projects, root_commit=root_commit, github_slug=github_slug)
    except AmbiguousRepoIdentityError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    if matched is None:
        typer.echo(
            f"unregistered: repository '{repo_name}' (root {root_commit[:12]}) is not registered.\n"
            "To register it, run `workbench begin`, edit the generated project.yaml, and run `workbench push`."
        )
        return

    pfile = data_dir / "projects" / matched.id / "project.yaml"
    typer.echo(pfile.read_text(encoding="utf-8"), nl=False)


@app.command("begin")
def cmd_begin(
    project_id: Optional[str] = typer.Option(None, "--id", help="Explicit project ID to edit"),
    learning_id: Optional[str] = typer.Option(
        None, "--learning", help="Explicit learning path ID to edit"
    ),
) -> None:
    """Fetch origin/main, create a fresh worktree under ~/.workbench/work/<id>-<rand>/, and print the file path to edit."""
    wb_dir = get_workbench_dir()
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()

    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2)

    work_root = wb_dir / "work"
    rand_suffix = secrets.token_hex(4)

    if learning_id:
        lid = slugify_name(learning_id)
        wt_dir = work_root / f"{lid}-{rand_suffix}"
        try:
            create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
        except GitError as exc:
            typer.echo(f"Error creating worktree: {exc}", err=True)
            raise typer.Exit(code=2)
        target_file = wt_dir / "learning" / lid / "path.yaml"
        if not target_file.exists():
            cm = CommentedMap()
            cm["schema_version"] = 1
            cm["id"] = lid
            cm["name"] = learning_id[:80]
            cm["description"] = ""
            cm["status"] = "active"
            cm["updated"] = parse_iso_date_for_yaml(datetime.date.today().isoformat())
            cm["references"] = []
            cm["topics"] = []
            dump_yaml_roundtrip(cm, target_file)
        typer.echo(str(target_file))
        return

    # Determine target project from --id or current git repo
    target_id: str | None = project_id
    root_commit: str | None = None
    github_slug: str | None = None
    repo_name: str = "project"

    cwd = Path.cwd()
    if is_git_repo(cwd):
        root_commit, github_slug, repo_name = resolve_local_repo_identity(cwd)

    if target_id is None:
        if not root_commit:
            typer.echo(
                "Error: current directory is not a git repo with commits (or pass --id <project-id>).",
                err=True,
            )
            raise typer.Exit(code=1)

    # Create temporary worktree to inspect origin/main projects accurately
    temp_wt = work_root / f"tmp-{rand_suffix}"
    try:
        create_worktree(data_dir, temp_wt, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2)

    projects = _load_all_projects(temp_wt)
    matched = None
    if target_id:
        for p in projects:
            if p.id == target_id:
                matched = p
                break
    elif root_commit:
        try:
            matched = find_project_for_repo(
                projects, root_commit=root_commit, github_slug=github_slug
            )
        except AmbiguousRepoIdentityError as exc:
            remove_worktree(data_dir, temp_wt)
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1)

    if matched is not None:
        final_id = matched.id
    else:
        base_slug = slugify_name(target_id or repo_name)
        existing_ids = {p.id for p in projects}
        final_id = base_slug
        idx = 2
        while final_id in existing_ids:
            suf = f"-{idx}"
            final_id = f"{base_slug[:48 - len(suf)]}{suf}"
            idx += 1

    wt_dir = work_root / f"{final_id}-{rand_suffix}"
    # Move temp_wt to final wt_dir using git worktree move
    try:
        run_git(["worktree", "move", str(temp_wt), str(wt_dir)], cwd=data_dir)
    except GitError:
        remove_worktree(data_dir, temp_wt)
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")

    target_file = wt_dir / "projects" / final_id / "project.yaml"
    if not target_file.exists():
        cm = CommentedMap()
        cm["schema_version"] = 1
        cm["id"] = final_id
        cm["name"] = (target_id or repo_name)[:80]
        cm["description"] = ""
        cm["status"] = "active"
        cm["origin"] = "agent"
        cm["updated"] = parse_iso_date_for_yaml(datetime.date.today().isoformat())
        repos_seq = CommentedSeq()
        if root_commit:
            repo_item = CommentedMap()
            if github_slug:
                repo_item["github"] = github_slug
            repo_item["root_commit"] = root_commit
            repo_item["name"] = repo_name[:80]
            repos_seq.append(repo_item)
        cm["repos"] = repos_seq
        cm["links"] = []
        cm["phases"] = []
        dump_yaml_roundtrip(cm, target_file)

    # Record baseline metadata for detecting agent edits to `updated` or `origin`
    baseline_marker = wt_dir / ".git_wb_baseline.json"
    try:
        raw_base = to_plain_python(load_yaml_roundtrip(target_file))
        import json

        baseline_marker.write_text(
            json.dumps(
                {
                    "rel_path": target_file.relative_to(wt_dir).as_posix(),
                    "updated": raw_base.get("updated"),
                    "origin": raw_base.get("origin"),
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    typer.echo(str(target_file))


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


@app.command("push")
def cmd_push(
    worktree: Path = typer.Argument(..., help="Path to worktree directory (or YAML file inside it)"),
    message: str = typer.Option(..., "-m", "--message", help="One-line commit summary"),
    allow_removals: bool = typer.Option(
        False, "--allow-removals", help="Allow removing existing IDs or >30% content deletion"
    ),
) -> None:
    """Validate, privacy-check, commit, rebase, and push changes from a worktree (§8)."""
    _push_worktree(worktree, message, allow_removals, mark_seen=True)


def _push_worktree(
    worktree: Path, message: str, allow_removals: bool, mark_seen: bool
) -> None:
    """Shared push pipeline; mark_seen records the cwd repo as seen for the Stop hook."""
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
        typer.echo("Workbench: no changes to push (worktree removed).")
        return

    # 1. Check allowed paths (§4.2, §6.1)
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

            # Check if agent edited `updated` or `origin` (§6.3)
            expected_prev_updated = (
                old_plain.get("updated")
                if old_plain is not None
                else baseline_info.get("updated")
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
                combined_free_text_diffs.append(
                    f"--- {rel_path}\n+ {new_md_text}"
                )

    for w in all_warnings:
        typer.echo(w, err=True)

    if all_errors:
        for err in all_errors:
            typer.echo(f"Validation error: {err}", err=True)
        raise typer.Exit(code=1)

    # 3. LLM privacy reviewer on free-text changes (§7.2)
    full_ft_diff = "\n".join(combined_free_text_diffs)
    if full_ft_diff.strip():
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
    try:
        commit_sha = commit_all(wt_dir, message)
    except GitError as exc:
        typer.echo(f"Git commit error: {exc}", err=True)
        raise typer.Exit(code=2)

    # 5. Fetch + rebase onto origin/main + re-validate + push (retry push race up to 3 times)
    for attempt in range(3):
        try:
            fetch_origin(data_dir, branch=cfg.branch)
        except GitError as exc:
            typer.echo(f"Git fetch error: {exc}", err=True)
            raise typer.Exit(code=2)

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
    typer.echo(f"Pushed {commit_sha[:8]} to {cfg.branch}: {message}")


@app.command("archive-empty")
def cmd_archive_empty(
    dry_run: bool = typer.Option(False, "--dry-run", help="List empty projects without archiving them"),
    keep: list[str] = typer.Option([], "--keep", help="Project ID to leave active (repeatable)"),
) -> None:
    """Archive every active discovered project with no phases and no topics, in one commit."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2)

    wt_dir = get_workbench_dir() / "work" / f"archive-empty-{secrets.token_hex(4)}"
    try:
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2)

    keep_ids = set(keep)
    to_archive: list[tuple[str, Path]] = []
    for pfile in sorted((wt_dir / "projects").glob("*/project.yaml")):
        plain = to_plain_python(load_yaml_roundtrip(pfile))
        if not isinstance(plain, dict):
            continue
        pid = str(plain.get("id", pfile.parent.name))
        # Only auto-discovered stubs: a hand-made project (e.g. `workbench new`) starts empty on purpose
        is_empty_stub = (
            plain.get("origin") == "discovered"
            and not plain.get("phases")
            and not plain.get("topics")
        )
        if is_empty_stub and plain.get("status") != "archived" and pid not in keep_ids:
            to_archive.append((pid, pfile))

    if not to_archive or dry_run:
        remove_worktree(data_dir, wt_dir)
        if not to_archive:
            typer.echo("No empty projects to archive.")
            return
        typer.echo(f"Would archive {len(to_archive)} empty project(s):")
        for pid, _ in to_archive:
            typer.echo(f"  {pid}")
        return

    typer.echo(f"Archiving {len(to_archive)} empty project(s):")
    for pid, pfile in to_archive:
        doc = load_yaml_roundtrip(pfile)
        doc["status"] = "archived"
        dump_yaml_roundtrip(doc, pfile)
        typer.echo(f"  {pid}")

    _push_worktree(
        wt_dir,
        f"Archive {len(to_archive)} empty project(s)",
        allow_removals=False,
        mark_seen=False,
    )


@app.command("link")
def cmd_link(
    project_id: str = typer.Option(..., "--id", help="Project to attach the current repo to"),
) -> None:
    """Attach the current git repo to an existing project, e.g. one spanning several repos.

    An empty discovered project that already claims this repo is removed in the same commit;
    a project with real content that claims it is left alone and the link is refused.
    """
    cwd = Path.cwd()
    root_commit = github_slug = None
    repo_name = "repo"
    if is_git_repo(cwd):
        root_commit, github_slug, repo_name = resolve_local_repo_identity(cwd)
    if not root_commit:
        typer.echo("Error: current directory is not a git repo with commits.", err=True)
        raise typer.Exit(code=1)

    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2)

    wt_dir = get_workbench_dir() / "work" / f"link-{project_id}-{secrets.token_hex(4)}"
    try:
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2)

    def abort(msg: str, code: int = 1) -> None:
        remove_worktree(data_dir, wt_dir)
        typer.echo(msg, err=True)
        raise typer.Exit(code=code)

    target_file = wt_dir / "projects" / project_id / "project.yaml"
    if not target_file.exists():
        abort(
            f"Error: project '{project_id}' does not exist. "
            f'Create it first with `workbench new "<name>"`.'
        )

    gh_lower = github_slug.lower() if github_slug else None

    def claims_this_repo(repo_entry) -> bool:
        if repo_entry.root_commit != root_commit:
            return False
        # Same root commit with a different GitHub name is a fork, which identity resolution tells apart
        return not (gh_lower and repo_entry.github and repo_entry.github.lower() != gh_lower)

    stubs_to_remove: list[str] = []
    for proj in _load_all_projects(wt_dir):
        if not any(claims_this_repo(r) for r in proj.repos):
            continue
        if proj.id == project_id:
            remove_worktree(data_dir, wt_dir)
            typer.echo(f"Repo '{repo_name}' is already linked to '{project_id}'.")
            return
        is_empty_stub = proj.origin == "discovered" and not proj.phases and not proj.topics
        if is_empty_stub and len(proj.repos) == 1:
            stubs_to_remove.append(proj.id)
        else:
            abort(
                f"Error: repo '{repo_name}' is already claimed by project '{proj.id}', which has "
                "content or other repos. Move its content into "
                f"'{project_id}' by hand, then remove '{proj.id}' with `workbench push --allow-removals`."
            )

    doc = load_yaml_roundtrip(target_file)
    repos_seq = doc.get("repos")
    if repos_seq is None:
        repos_seq = CommentedSeq()
        doc["repos"] = repos_seq
    repo_item = CommentedMap()
    if github_slug:
        repo_item["github"] = github_slug
    repo_item["root_commit"] = root_commit
    repo_item["name"] = repo_name[:80]
    repos_seq.append(repo_item)
    dump_yaml_roundtrip(doc, target_file)

    for stub_id in stubs_to_remove:
        shutil.rmtree(wt_dir / "projects" / stub_id)
        typer.echo(f"Removing empty discovered project '{stub_id}' (it claimed this repo).")

    _push_worktree(
        wt_dir,
        f"Link {repo_name} to {project_id}",
        allow_removals=bool(stubs_to_remove),
        mark_seen=False,
    )


@app.command("validate")
def cmd_validate(
    path: Optional[Path] = typer.Argument(
        None, help="File or workbench-data directory to validate"
    ),
) -> None:
    """Validate a single metadata file or the entire workbench-data repository."""
    target = path.resolve() if path else _get_data_repo_dir()
    if not target.exists():
        typer.echo(f"Error: path '{target}' does not exist.", err=True)
        raise typer.Exit(code=1)

    if target.is_dir():
        res = validate_data_repo(target)
    else:
        res = validate_any_file(target)

    for w in res.warnings:
        typer.echo(f"Warning: {w}", err=True)
    if not res.ok:
        for err in res.errors:
            typer.echo(f"Validation error: {err}", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Valid: {target}")


@app.command("new")
def cmd_new(
    name: str = typer.Argument(..., help="Display name of the project or learning path"),
    learning: bool = typer.Option(
        False, "--learning", help="Create a learning path instead of a project"
    ),
    parent: Optional[str] = typer.Option(
        None, "--parent", help="Parent project id (makes this a child project; needs --under)"
    ),
    under: Optional[str] = typer.Option(
        None, "--under", help="Phase or component id in the parent this project sits under"
    ),
    status: str = typer.Option(
        "active", "--status", help="Initial status: planned, active, paused, done, archived"
    ),
) -> None:
    """Create a project with no repo, or a learning path, by hand (§8)."""
    if (parent is None) != (under is None):
        typer.echo("Error: --parent and --under must be given together.", err=True)
        raise typer.Exit(code=1)
    if learning and parent is not None:
        typer.echo("Error: --parent/--under apply to projects, not learning paths.", err=True)
        raise typer.Exit(code=1)
    if status not in get_args(ProjectStatus):
        typer.echo(
            f"Error: invalid --status '{status}' (choose from {', '.join(get_args(ProjectStatus))}).",
            err=True,
        )
        raise typer.Exit(code=1)

    wb_dir = get_workbench_dir()
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    fetch_origin(data_dir, branch=cfg.branch)

    item_id = slugify_name(name)
    wt_dir = wb_dir / "work" / f"new-{item_id}-{secrets.token_hex(4)}"
    create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")

    try:
        if parent is not None:
            link_error = _parent_link_error(_load_valid_projects(wt_dir), item_id, parent, under)
            if link_error:
                typer.echo(f"Error: {link_error}", err=True)
                raise typer.Exit(code=1)
        today_date = parse_iso_date_for_yaml(datetime.date.today().isoformat())
        if learning:
            target = wt_dir / "learning" / item_id / "path.yaml"
            if target.exists():
                typer.echo(f"Error: learning path '{item_id}' already exists.", err=True)
                raise typer.Exit(code=1)
            cm = CommentedMap()
            cm["schema_version"] = 1
            cm["id"] = item_id
            cm["name"] = name[:80]
            cm["description"] = ""
            cm["status"] = status
            cm["updated"] = today_date
            cm["references"] = []
            cm["topics"] = []
            dump_yaml_roundtrip(cm, target)
            vres, _, _ = validate_learning_file(target, expected_dir_id=item_id)
        else:
            target = wt_dir / "projects" / item_id / "project.yaml"
            if target.exists():
                typer.echo(f"Error: project '{item_id}' already exists.", err=True)
                raise typer.Exit(code=1)
            cm = CommentedMap()
            cm["schema_version"] = 1
            cm["id"] = item_id
            cm["name"] = name[:80]
            cm["description"] = ""
            cm["status"] = status
            if parent is not None:
                cm["parent"] = parent
                cm["under"] = under
            cm["origin"] = "manual"
            cm["updated"] = today_date
            cm["repos"] = []
            cm["links"] = []
            cm["phases"] = []
            dump_yaml_roundtrip(cm, target)
            vres, _, _ = validate_project_file(target, expected_dir_id=item_id)

        if not vres.ok:
            for err in vres.errors:
                typer.echo(f"Validation error: {err}", err=True)
            raise typer.Exit(code=1)

        kind = "learning path" if learning else "project"
        sha = commit_all(wt_dir, f"Create {kind} {item_id}")
        if not push_head_to_branch(wt_dir, branch=cfg.branch):
            typer.echo("Error: failed to push new entry.", err=True)
            raise typer.Exit(code=2)
        where = f" under '{parent}/{under}'" if parent else ""
        typer.echo(f"Created {kind} '{item_id}'{where} ({sha[:8]})")
    finally:
        remove_worktree(data_dir, wt_dir)


def _load_valid_projects(repo_root: Path) -> dict[str, Project]:
    """Projects in repo_root that pass validation, keyed by id."""
    out: dict[str, Project] = {}
    projects_dir = repo_root / "projects"
    if not projects_dir.exists():
        return out
    for pdir in sorted(projects_dir.iterdir()):
        pfile = pdir / "project.yaml"
        if pdir.is_dir() and pfile.exists():
            _, proj, _ = validate_project_file(pfile, expected_dir_id=pdir.name)
            if proj is not None:
                out[proj.id] = proj
    return out


def _parent_link_error(
    projects: dict[str, Project], child_id: str, parent_id: str, under: str
) -> str | None:
    """Why child_id cannot sit under parent_id/under, or None if the link is fine."""
    if parent_id == child_id:
        return f"project '{child_id}' cannot be its own parent."
    parent = projects.get(parent_id)
    if parent is None:
        return f"parent project '{parent_id}' does not exist (or is invalid)."
    item_ids = [g.id for g in list(parent.phases) + list(parent.topics)]
    item_ids += [c.id for c in parent.all_components()]
    if under not in item_ids:
        known = ", ".join(item_ids) or "none"
        return (
            f"'{under}' is not a phase or component of project '{parent_id}' "
            f"(available: {known})."
        )
    chain = [child_id, parent_id]
    current = parent.parent
    while current is not None:
        if current == child_id:
            return f"attaching would create a parent cycle: {' -> '.join(chain + [child_id])}."
        if current in chain or current not in projects:
            break
        chain.append(current)
        current = projects[current].parent
    return None


def _open_project_worktree(prefix: str, project_id: str) -> tuple[Path, Path, Path]:
    """Fetch origin and create a fresh worktree; return (data_dir, wt_dir, project.yaml path)."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2)
    wt_dir = get_workbench_dir() / "work" / f"{prefix}-{project_id}-{secrets.token_hex(4)}"
    try:
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2)
    target = wt_dir / "projects" / project_id / "project.yaml"
    if not target.exists():
        remove_worktree(data_dir, wt_dir)
        typer.echo(f"Error: project '{project_id}' does not exist.", err=True)
        raise typer.Exit(code=1)
    return data_dir, wt_dir, target


@app.command("attach")
def cmd_attach(
    child_id: str = typer.Option(..., "--id", help="Project to place under a parent"),
    parent: str = typer.Option(..., "--parent", help="Parent project id"),
    under: str = typer.Option(..., "--under", help="Phase or component id in the parent"),
) -> None:
    """Make an existing project a child of another project, under one of its phases/components.

    A project has at most one parent; attaching an already-attached project moves it.
    """
    data_dir, wt_dir, target = _open_project_worktree("attach", child_id)
    link_error = _parent_link_error(_load_valid_projects(wt_dir), child_id, parent, under)
    if link_error:
        remove_worktree(data_dir, wt_dir)
        typer.echo(f"Error: {link_error}", err=True)
        raise typer.Exit(code=1)

    doc = load_yaml_roundtrip(target)
    if doc.get("parent") == parent and doc.get("under") == under:
        remove_worktree(data_dir, wt_dir)
        typer.echo(f"Project '{child_id}' is already under '{parent}/{under}'.")
        return
    if "parent" in doc:
        doc["parent"] = parent
        doc["under"] = under
    else:
        keys = list(doc.keys())
        pos = keys.index("status") + 1 if "status" in keys else len(keys)
        doc.insert(pos, "parent", parent)
        doc.insert(pos + 1, "under", under)
    dump_yaml_roundtrip(doc, target)
    _push_worktree(
        wt_dir, f"Attach {child_id} to {parent}/{under}", allow_removals=False, mark_seen=False
    )


@app.command("detach")
def cmd_detach(
    child_id: str = typer.Option(..., "--id", help="Child project to make top-level again"),
) -> None:
    """Remove a project's parent/under so it stands on its own again."""
    data_dir, wt_dir, target = _open_project_worktree("detach", child_id)
    doc = load_yaml_roundtrip(target)
    if "parent" not in doc and "under" not in doc:
        remove_worktree(data_dir, wt_dir)
        typer.echo(f"Project '{child_id}' has no parent; nothing to detach.")
        return
    old_parent = doc.get("parent")
    doc.pop("parent", None)
    doc.pop("under", None)
    dump_yaml_roundtrip(doc, target)
    _push_worktree(
        wt_dir, f"Detach {child_id} from {old_parent}", allow_removals=False, mark_seen=False
    )


@learning_app.command("import")
def cmd_learning_import(
    file: Path = typer.Argument(..., help="YAML or Markdown outline to import as a learning path"),
) -> None:
    """Import a learning path from YAML or a Markdown outline (# Name, ## Topic, ### Subtopic).

    Re-importing into an existing path merges: statuses only move forward and no item is removed.
    """
    if not file.exists():
        typer.echo(f"Error: file '{file}' does not exist.", err=True)
        raise typer.Exit(code=1)

    text = file.read_text(encoding="utf-8")
    if file.suffix.lower() in (".yaml", ".yml"):
        incoming = to_plain_python(load_yaml_str_roundtrip(text))
        if not isinstance(incoming, dict):
            typer.echo("Error: YAML file must contain a mapping.", err=True)
            raise typer.Exit(code=1)
        incoming["schema_version"] = 1
        incoming["id"] = str(incoming.get("id") or slugify_name(str(incoming.get("name") or file.stem)))
    else:
        incoming, warnings = parse_learning_markdown(
            text, fallback_name=file.stem.replace("-", " ").title()
        )
        for w in warnings:
            typer.echo(f"Warning: {w}", err=True)
    item_id = incoming["id"]

    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2)
    wt_dir = get_workbench_dir() / "work" / f"import-{item_id}-{secrets.token_hex(4)}"
    create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")

    target = wt_dir / "learning" / item_id / "path.yaml"
    if target.exists():
        existing = to_plain_python(load_yaml_roundtrip(target))
        merged = merge_learning_paths(existing, incoming)
        if merged == existing:
            remove_worktree(data_dir, wt_dir)
            typer.echo(f"Learning path '{item_id}' already matches; nothing to import.")
            return
        typer.echo(f"Merging into existing learning path '{item_id}'.")
    else:
        merged = incoming
        typer.echo(f"Creating learning path '{item_id}'.")

    merged["updated"] = parse_iso_date_for_yaml(datetime.date.today().isoformat())
    dump_yaml_roundtrip(merged, target)
    _push_worktree(wt_dir, f"Import learning path {item_id}", allow_removals=False, mark_seen=False)


@learning_app.command("export")
def cmd_learning_export(
    path_id: str = typer.Argument(..., help="Learning path id to print as a Markdown outline"),
) -> None:
    """Print a learning path as the Markdown outline that `learning import` reads."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
        run_git(["reset", "--hard", f"origin/{cfg.branch}"], cwd=data_dir, check=False)
    except GitError:
        pass
    pfile = data_dir / "learning" / path_id / "path.yaml"
    if not pfile.exists():
        typer.echo(f"Error: learning path '{path_id}' does not exist.", err=True)
        raise typer.Exit(code=1)
    typer.echo(export_learning_markdown(to_plain_python(load_yaml_roundtrip(pfile))), nl=False)


@app.command("discover")
def cmd_discover(
    local: bool = typer.Option(True, "--local", help="Scan configured local roots"),
) -> None:
    """Scan local folders and register unknown owned repositories (§8)."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        registered = run_local_discovery(data_repo_dir=data_dir, config=cfg)
    except Exception as exc:
        typer.echo(f"Discovery failed: {exc}", err=True)
        raise typer.Exit(code=2)

    if registered:
        typer.echo(f"Registered {len(registered)} repository(s): {', '.join(registered)}")
    else:
        typer.echo("No new repositories discovered.")


@app.command("history")
def cmd_history(
    item_id: Optional[str] = typer.Argument(None, help="Optional project or learning path ID"),
    limit: int = typer.Option(20, "-n", "--limit", help="Max commits to show"),
) -> None:
    """Show recent data-repo commits."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    fetch_origin(data_dir, branch=cfg.branch)

    args = ["log", f"origin/{cfg.branch}", f"-n{limit}", "--oneline"]
    if item_id:
        args.extend(["--", f"projects/{item_id}", f"learning/{item_id}"])
    proc = run_git(args, cwd=data_dir)
    typer.echo(proc.stdout, nl=False)


@app.command("undo")
def cmd_undo(
    commit: str = typer.Argument(..., help="Commit SHA in workbench-data to revert"),
) -> None:
    """Revert a commit in workbench-data and push to origin/main (§8)."""
    wb_dir = get_workbench_dir()
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    fetch_origin(data_dir, branch=cfg.branch)

    wt_dir = wb_dir / "work" / f"undo-{secrets.token_hex(4)}"
    create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    try:
        proc = run_git(["revert", "--no-edit", commit], cwd=wt_dir, check=False)
        if proc.returncode != 0:
            typer.echo(f"Error reverting {commit}: {proc.stderr.strip()}", err=True)
            raise typer.Exit(code=2)

        if not push_head_to_branch(wt_dir, branch=cfg.branch):
            typer.echo("Error pushing revert commit.", err=True)
            raise typer.Exit(code=2)

        new_sha = get_head_commit(wt_dir, "HEAD") or ""
        try:
            run_git(["reset", "--hard", f"origin/{cfg.branch}"], cwd=data_dir, check=False)
        except GitError:
            pass
        typer.echo(f"Reverted {commit[:8]} -> pushed {new_sha[:8]}")
    finally:
        remove_worktree(data_dir, wt_dir)


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


@app.command("install")
def cmd_install(
    home: Path = typer.Option(..., "--home", help="Target home directory for installation"),
    claude: bool = typer.Option(False, "--claude", help="Install Claude skill and Stop hook"),
    gemini: bool = typer.Option(False, "--gemini", help="Install Gemini CLI rules, command, and hook"),
    launchd: bool = typer.Option(False, "--launchd", help="Install macOS launchd local discovery job"),
) -> None:
    """Install agent skill, hooks, and launchd job into the specified home directory (§8)."""
    target_home = home.resolve()
    installed = run_install(
        home_dir=target_home,
        claude=claude,
        gemini=gemini,
        launchd=launchd,
    )
    for p in installed:
        typer.echo(f"Installed: {p}")


@app.command("setup")
def cmd_setup(
    home: Optional[Path] = typer.Option(
        None, "--home", help="Target home directory (defaults to current HOME)"
    ),
) -> None:
    """Interactive go-live wizard (§12)."""
    target_home = home.resolve() if home else Path(os.environ.get("HOME", str(Path.home()))).resolve()

    data_repo_url = typer.prompt("Workbench data repository URL").strip()
    owners_raw = typer.prompt("GitHub owner username(s) (comma-separated)").strip()
    emails_raw = typer.prompt("Git author email(s) for local discovery (comma-separated)").strip()
    roots_raw = typer.prompt(
        "Local scan roots (comma-separated)", default="~/Documents, ~/code, ~/projects"
    ).strip()

    typer.echo(f"Checking repository access for {data_repo_url}...")
    if not ls_remote(data_repo_url):
        typer.echo(
            f"Error: cannot access '{data_repo_url}' via `git ls-remote`. "
            "Verify the repository exists and your SSH key or credential has access.",
            err=True,
        )
        raise typer.Exit(code=2)

    owners = [x.strip() for x in owners_raw.split(",") if x.strip()]
    emails = [x.strip() for x in emails_raw.split(",") if x.strip()]
    roots = [x.strip() for x in roots_raw.split(",") if x.strip()]

    cfg = WorkbenchLocalConfig(
        data_repo_url=data_repo_url,
        branch="main",
        scan_roots=roots,
        github_owners=owners,
        user_emails=emails,
    )
    cfg_path = save_local_config(cfg, home_dir=target_home)
    typer.echo(f"Wrote configuration to {cfg_path}")

    data_dir = target_home / ".workbench" / "data"
    if not data_dir.exists():
        clone_repo(data_repo_url, data_dir, branch="main")
        typer.echo(f"Cloned data repository to {data_dir}")

    planned_paths = [
        target_home / ".claude" / "skills" / "workbench" / "SKILL.md",
        target_home / ".claude" / "settings.json",
        target_home / ".gemini" / "GEMINI.md",
        target_home / ".gemini" / "commands" / "workbench.toml",
        target_home / ".gemini" / "settings.json",
        target_home / "Library" / "LaunchAgents" / "com.workbench.discover.plist",
    ]
    typer.echo("\nWorkbench setup will create or merge the following files:")
    for pp in planned_paths:
        typer.echo(f"  - {pp}")

    confirm = typer.prompt("Type 'yes' to install agent integrations and launchd job").strip()
    if confirm != "yes":
        typer.echo("Skipped agent installation (you can run `workbench install` later).")
        return

    installed = run_install(home_dir=target_home, claude=True, gemini=True, launchd=True)
    for p in installed:
        typer.echo(f"Installed: {p}")
    typer.echo("Setup complete.")


@demo_app.command("init")
def cmd_demo_init(
    directory: Path = typer.Argument(..., help="Directory where artificial demo environment will be created"),
) -> None:
    """Create the artificial demo environment with bare remote, fake HOME, and fake repos (§11)."""
    info = init_demo_environment(directory)
    typer.echo(f"Initialized demo environment at {info['base_dir']}")


if __name__ == "__main__":
    app()
