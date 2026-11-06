"""Commands that create entries and change the project tree: new, attach, detach."""

from __future__ import annotations

import datetime
import secrets
from pathlib import Path
from typing import get_args

import typer
from ruamel.yaml.comments import CommentedMap

from workbench import commitmsg
from workbench.cli._common import _get_data_repo_dir, _open_project_worktree, _push_worktree
from workbench.config import get_workbench_dir, load_local_config
from workbench.gitops import (
    GitError,
    create_worktree,
    fetch_origin,
    get_head_commit,
    remove_worktree,
)
from workbench.identity import slugify_name
from workbench.models import Project, ProjectStatus
from workbench.validate import validate_learning_file, validate_project_file
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    parse_iso_date_for_yaml,
    to_plain_python,
)

app = typer.Typer()


@app.command("new")
def cmd_new(
    name: str = typer.Argument(..., help="Display name of the project or learning path"),
    learning: bool = typer.Option(
        False, "--learning", help="Create a learning path instead of a project"
    ),
    parent: str | None = typer.Option(
        None, "--parent", help="Parent project id (makes this a child project; needs --under)"
    ),
    under: str | None = typer.Option(
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
    try:
        fetch_origin(data_dir, branch=cfg.branch)
    except GitError as exc:
        typer.echo(f"Error fetching origin/{cfg.branch}: {exc}", err=True)
        raise typer.Exit(code=2) from None

    item_id = slugify_name(name)
    kind = "learning path" if learning else "project"
    wt_dir = wb_dir / "work" / f"new-{item_id}-{secrets.token_hex(4)}"
    create_worktree(data_dir, wt_dir, ref=f"origin/{cfg.branch}")

    # Pre-push checks: on failure nothing was committed, so drop the worktree.
    try:
        target = _write_new_entry(wt_dir, item_id, name, learning, status, parent, under)
    except typer.Exit:
        remove_worktree(data_dir, wt_dir)
        raise
    _write_new_entry_baseline(wt_dir, target)

    # Same pipeline as push/attach/link: privacy review, rebase, repo-wide validation, retries.
    scope = f"learning/{item_id}" if learning else item_id
    summary = f"new {kind} {name[:80]}" + (f" under {parent}/{under}" if parent else "")
    _push_worktree(
        wt_dir,
        commitmsg.build_message("create", summary, scope),
        allow_removals=False,
        mark_seen=False,
    )
    where = f" under '{parent}/{under}'" if parent else ""
    sha = get_head_commit(data_dir, f"origin/{cfg.branch}") or ""
    typer.echo(f"Created {kind} '{item_id}'{where} ({sha[:8]})")


def _write_new_entry(
    wt_dir: Path,
    item_id: str,
    name: str,
    learning: bool,
    status: str,
    parent: str | None,
    under: str | None,
) -> Path:
    """Write a fresh project/learning YAML into wt_dir and validate it; Exit(1) on any problem."""
    if parent is not None:
        link_error = _parent_link_error(_load_valid_projects(wt_dir), item_id, parent, under)
        if link_error:
            typer.echo(f"Error: {link_error}", err=True)
            raise typer.Exit(code=1)
    today_date = parse_iso_date_for_yaml(datetime.date.today().isoformat())
    cm = CommentedMap()
    cm["schema_version"] = 1
    cm["id"] = item_id
    cm["name"] = name[:80]
    cm["description"] = ""
    cm["status"] = status
    if learning:
        target = wt_dir / "learning" / item_id / "path.yaml"
        if target.exists():
            typer.echo(f"Error: learning path '{item_id}' already exists.", err=True)
            raise typer.Exit(code=1)
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
    return target


def _write_new_entry_baseline(wt_dir: Path, target: Path) -> None:
    """Record the CLI-set updated/origin so _push_worktree keeps origin 'manual' without warnings."""
    import json

    raw = to_plain_python(load_yaml_roundtrip(target))
    (wt_dir / ".git_wb_baseline.json").write_text(
        json.dumps(
            {
                "rel_path": target.relative_to(wt_dir).as_posix(),
                "updated": raw.get("updated"),
                "origin": raw.get("origin"),
            }
        ),
        encoding="utf-8",
    )


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
            f"'{under}' is not a phase or component of project '{parent_id}' (available: {known})."
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
        wt_dir,
        commitmsg.build_message("attach", f"under {parent}/{under}", child_id),
        allow_removals=False,
        mark_seen=False,
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
        wt_dir,
        commitmsg.build_message("detach", f"from {old_parent}", child_id),
        allow_removals=False,
        mark_seen=False,
    )
