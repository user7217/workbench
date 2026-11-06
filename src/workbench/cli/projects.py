"""Project entry commands: show, begin, push, archive-empty, link, validate."""

from __future__ import annotations

import datetime
import secrets
import shutil
from pathlib import Path
from typing import Any

import typer
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from workbench import commitmsg, edits
from workbench.cli._common import _get_data_repo_dir, _load_all_projects, _push_worktree
from workbench.config import get_workbench_dir, load_local_config
from workbench.gitops import (
    GitError,
    create_worktree,
    fetch_origin,
    is_git_repo,
    remove_worktree,
    run_git,
)
from workbench.identity import (
    AmbiguousRepoIdentityError,
    find_project_for_repo,
    resolve_local_repo_identity,
    slugify_name,
)
from workbench.validate import validate_any_file, validate_data_repo
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    parse_iso_date_for_yaml,
    to_plain_python,
)

app = typer.Typer()


@app.command("show")
def cmd_show(
    project_id: str | None = typer.Option(None, "--id", help="Explicit project ID to show"),
    item_id: str | None = typer.Option(
        None, "--item", help="Show only this phase/topic/component (any depth) and its parts"
    ),
    brief: bool = typer.Option(
        False, "--brief", help="Compact outline: one line per item (id, status, name)"
    ),
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
        if not pfile.exists():
            typer.echo(
                f"unregistered: project '{project_id}' is not registered in Workbench.\n"
                "Run 'workbench begin' inside the project repository to register and edit it."
            )
            return
        _print_project(data_dir, pfile, item_id, brief)
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
        raise typer.Exit(code=1) from None

    if matched is None:
        typer.echo(
            f"unregistered: repository '{repo_name}' (root {root_commit[:12]}) is not registered.\n"
            "To register it, run `workbench begin`, edit the generated project.yaml, and run `workbench push`."
        )
        return

    pfile = data_dir / "projects" / matched.id / "project.yaml"
    _print_project(data_dir, pfile, item_id, brief)


def _print_project(data_dir: Path, pfile: Path, item_id: str | None, brief: bool) -> None:
    """Print the whole file, one item's subtree (--item), or an outline (--brief)."""
    if item_id is None and not brief:
        typer.echo(pfile.read_text(encoding="utf-8"), nl=False)
        return
    doc = load_yaml_roundtrip(pfile)
    project_id = str(doc.get("id") or pfile.parent.name)
    node = doc
    if item_id is not None:
        loc = edits.locate_project_item(doc, item_id)
        if loc is None:
            typer.echo(f"Error: item '{item_id}' not found in project '{project_id}'.", err=True)
            raise typer.Exit(code=1)
        node = loc.node
    if not brief:
        typer.echo(dump_yaml_roundtrip(node), nl=False)
        return
    child_projects = _child_projects_by_under(data_dir, project_id)
    lines: list[str] = []
    if node is doc:
        lines.append(_outline_line(doc, 0, prefix="[project] "))
        for group in edits.children(doc, edits.GROUP_KEYS):
            _outline_item(group, 1, child_projects, lines)
    else:
        _outline_item(node, 0, child_projects, lines)
    typer.echo("\n".join(lines))


def _outline_line(node: Any, depth: int, prefix: str = "") -> str:
    status = node.get("status") or "not_started"
    return f"{'  ' * depth}{prefix}{node.get('id')}  {status}  {node.get('name') or ''}"


def _outline_item(
    node: Any, depth: int, child_projects: dict[str, list[dict]], lines: list[str]
) -> None:
    """Append node and everything nested under it (child projects last) to lines."""
    lines.append(_outline_line(node, depth))
    for child in edits.children(node, edits.GROUP_CHILD_KEYS):
        _outline_item(child, depth + 1, child_projects, lines)
    for proj in child_projects.get(str(node.get("id")), []):
        lines.append(_outline_line(proj, depth + 1, prefix="[project] "))


def _child_projects_by_under(data_dir: Path, parent_id: str) -> dict[str, list[dict]]:
    """Projects whose parent is parent_id, grouped by the item they sit under (sorted by id)."""
    out: dict[str, list[dict]] = {}
    for pfile in sorted((data_dir / "projects").glob("*/project.yaml")):
        try:
            plain = to_plain_python(load_yaml_roundtrip(pfile))
        except Exception:
            continue
        if isinstance(plain, dict) and plain.get("parent") == parent_id and plain.get("under"):
            out.setdefault(str(plain["under"]), []).append(plain)
    return out


@app.command("begin")
def cmd_begin(
    project_id: str | None = typer.Option(None, "--id", help="Explicit project ID to edit"),
    learning_id: str | None = typer.Option(
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
        raise typer.Exit(code=2) from None

    work_root = wb_dir / "work"
    rand_suffix = secrets.token_hex(4)

    if learning_id:
        lid = slugify_name(learning_id)
        wt_dir = work_root / f"{lid}-{rand_suffix}"
        try:
            create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
        except GitError as exc:
            typer.echo(f"Error creating worktree: {exc}", err=True)
            raise typer.Exit(code=2) from None
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
        raise typer.Exit(code=2) from None

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
            raise typer.Exit(code=1) from None

    if matched is not None:
        final_id = matched.id
    else:
        base_slug = slugify_name(target_id or repo_name)
        existing_ids = {p.id for p in projects}
        final_id = base_slug
        idx = 2
        while final_id in existing_ids:
            suf = f"-{idx}"
            final_id = f"{base_slug[: 48 - len(suf)]}{suf}"
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


@app.command("push")
def cmd_push(
    worktree: Path = typer.Argument(
        ..., help="Path to worktree directory (or YAML file inside it)"
    ),
    message: str = typer.Option(..., "-m", "--message", help="One-line commit summary"),
    allow_removals: bool = typer.Option(
        False, "--allow-removals", help="Allow removing existing IDs or >30% content deletion"
    ),
) -> None:
    """Validate, privacy-check, commit, rebase, and push changes from a worktree.

    The commit is filed as `review(<project>): <message>` (scope omitted when several entries
    changed); a message that already uses a standard kind, e.g. `update(x/y): ...`, is kept.
    """
    _push_worktree(
        worktree,
        lambda files: commitmsg.review_message(message, commitmsg.scope_for_paths(files)),
        allow_removals,
        mark_seen=True,
    )


@app.command("archive-empty")
def cmd_archive_empty(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List empty projects without archiving them"
    ),
    keep: list[str] = typer.Option([], "--keep", help="Project ID to leave active (repeatable)"),
) -> None:
    """Archive every active discovered project with no phases and no topics, in one commit."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2) from None

    wt_dir = get_workbench_dir() / "work" / f"archive-empty-{secrets.token_hex(4)}"
    try:
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2) from None

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
        commitmsg.build_message("archive", f"{len(to_archive)} empty discovered project(s)"),
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
        raise typer.Exit(code=2) from None

    wt_dir = get_workbench_dir() / "work" / f"link-{project_id}-{secrets.token_hex(4)}"
    try:
        create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")
    except GitError as exc:
        typer.echo(f"Error creating worktree: {exc}", err=True)
        raise typer.Exit(code=2) from None

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
        commitmsg.build_message(
            "link",
            f"add repo {repo_name[:80]}"
            + (f", remove empty {', '.join(stubs_to_remove)}" if stubs_to_remove else ""),
            project_id,
        ),
        allow_removals=bool(stubs_to_remove),
        mark_seen=False,
    )


@app.command("validate")
def cmd_validate(
    path: Path | None = typer.Argument(None, help="File or workbench-data directory to validate"),
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
