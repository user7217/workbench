"""Quick edits of one project item: set, add, focus."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from workbench import commitmsg, edits
from workbench.agents.hooks import get_repo_focus, set_repo_focus
from workbench.cli._common import _check_input, _fail, _quick_edit

app = typer.Typer()


def _split_ids(value: str | None) -> list[str] | None:
    """Parse a comma-separated id list option; None when the option was not given."""
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


@app.command("set")
def cmd_set(
    project_id: str = typer.Argument(..., help="Project id"),
    item_id: str = typer.Argument(..., help="Phase/topic/component id (any depth)"),
    status: str | None = typer.Option(
        None, "--status", help="not_started, in_progress, done or parked"
    ),
    name: str | None = typer.Option(None, "--name", help="New display name"),
    summary: str | None = typer.Option(None, "--summary", help="Summary (empty clears it)"),
    notes: str | None = typer.Option(
        None, "--notes", help="Notes, replaced whole (empty clears them)"
    ),
    depends_on: str | None = typer.Option(
        None,
        "--depends-on",
        help="Comma-separated component ids this needs first, replaced whole (empty clears)",
    ),
) -> None:
    """Quick update of one phase/component: status and/or text fields, committed and pushed."""
    fields = {
        k: v for k, v in (("name", name), ("summary", summary), ("notes", notes)) if v is not None
    }
    deps = _split_ids(depends_on)
    if status is None and not fields and deps is None:
        _fail("give at least one of --status, --name, --summary, --notes, --depends-on.")
    _check_input(lambda: edits.require_slug(project_id, "project id"))
    _check_input(lambda: edits.require_slug(item_id, "item id"))
    if status is not None:
        _check_input(lambda: edits.require_status(status, edits.PHASE_STATUSES))
    if name is not None and not name.strip():
        _fail("--name cannot be empty.")
    described = dict(fields)
    if deps is not None:
        described["depends_on"] = deps
    what = commitmsg.describe_item_change(status=status, fields=described)
    # Parents that followed their parts (edits.roll_up_project), named in the commit and the output
    rolled: edits.RollUp = []

    def mutate(doc: Any) -> None:
        if status is not None:
            rolled[:] = edits.set_project_item_status(doc, project_id, item_id, status)
        if fields:
            edits.set_project_item_fields(doc, project_id, item_id, fields)
        if deps is not None:
            edits.set_project_item_dependencies(doc, project_id, item_id, deps)

    _, sha = _quick_edit(
        "set",
        project_id,
        mutate,
        lambda _r: commitmsg.build_message(
            "update", what + edits.describe_roll_up(rolled), f"{project_id}/{item_id}"
        ),
    )
    set_repo_focus(Path.cwd(), f"{project_id}/{item_id}")
    if sha is None:
        typer.echo(f"Unchanged {project_id}/{item_id}: {what} (already recorded)")
    else:
        typer.echo(
            f"Updated {project_id}/{item_id}: {what}{edits.describe_roll_up(rolled)} ({sha[:8]})"
        )


@app.command("add")
def cmd_add(
    project_id: str = typer.Argument(..., help="Project id"),
    under: str | None = typer.Option(
        None, "--under", help="Phase/topic/component to add under (omit for a new phase/topic)"
    ),
    name: str = typer.Option(..., "--name", help="Display name of the new item"),
    new_id: str | None = typer.Option(None, "--id", help="Explicit id (default: slug of name)"),
    status: str = typer.Option(
        "not_started", "--status", help="not_started, in_progress, done or parked"
    ),
    summary: str = typer.Option("", "--summary", help="What it is and why it is needed"),
    notes: str = typer.Option("", "--notes", help="Current state / next step"),
    depends_on: str | None = typer.Option(
        None, "--depends-on", help="Comma-separated component ids this needs first"
    ),
) -> None:
    """Quick add of a component under an item, or of a new phase/topic; prints the new id."""
    _check_input(lambda: edits.require_slug(project_id, "project id"))
    if under is not None:
        _check_input(lambda: edits.require_slug(under, "--under id"))
    if new_id is not None:
        _check_input(lambda: edits.require_slug(new_id, "--id"))
    _check_input(lambda: edits.require_status(status, edits.PHASE_STATUSES))
    if not name.strip():
        _fail("--name cannot be empty.")

    def mutate(doc: Any) -> tuple[str, str]:
        return edits.add_project_item(
            doc,
            project_id,
            parent_id=under,
            name=name,
            item_id=new_id,
            status=status,
            summary=summary,
            notes=notes,
            depends_on=_split_ids(depends_on),
        )

    def message(result: tuple[str, str]) -> str:
        added, label = result
        if under is None:
            return commitmsg.build_message("update", f"add {label} {added}", project_id)
        return commitmsg.build_message("update", f"add {added}", f"{project_id}/{under}")

    (added, label), sha = _quick_edit("add", project_id, mutate, message)
    # The focus stays on the item being worked on: the parent, or the new phase/topic itself.
    set_repo_focus(Path.cwd(), f"{project_id}/{under or added}")
    where = f"under {under}" if under else f"as a new {label}"
    typer.echo(f"Added {project_id}/{added} {where} ({(sha or '')[:8]})")


@app.command("focus")
def cmd_focus(
    set_to: str | None = typer.Option(
        None, "--set", help="<project> or <project>/<item> to focus on in this repo"
    ),
    clear: bool = typer.Option(False, "--clear", help="Clear this repo's focus"),
) -> None:
    """Show, set or clear the item this git repo is currently working on (used in hook reminders)."""
    if set_to is not None and clear:
        _fail("use either --set or --clear, not both.")
    cwd = Path.cwd()
    if set_to is not None:
        parts = set_to.strip("/").split("/")
        if len(parts) > 2:
            _fail("--set takes <project> or <project>/<item>.")
        _check_input(lambda: edits.require_slug(parts[0], "project id"))
        if len(parts) == 2:
            _check_input(lambda: edits.require_slug(parts[1], "item id"))
        focus = "/".join(parts)
        if not set_repo_focus(cwd, focus):
            typer.echo("Not in a git repository with commits; focus is per repo, nothing set.")
            return
        typer.echo(f"Focus set to {focus}")
        return
    if clear:
        if not set_repo_focus(cwd, None):
            typer.echo("Not in a git repository with commits; focus is per repo, nothing to clear.")
            return
        typer.echo("Focus cleared.")
        return
    in_repo, focus = get_repo_focus(cwd)
    if not in_repo:
        typer.echo("Not in a git repository with commits; focus is per repo.")
    elif focus:
        typer.echo(f"Focus: {focus}")
    else:
        typer.echo("No focus set for this repo.")
