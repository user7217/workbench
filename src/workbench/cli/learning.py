"""The `learning` sub-app: import and export learning paths."""

from __future__ import annotations

import datetime
import secrets
from pathlib import Path

import typer

from workbench import commitmsg
from workbench.cli._common import _get_data_repo_dir, _push_worktree
from workbench.config import get_workbench_dir, load_local_config
from workbench.gitops import GitError, create_worktree, fetch_origin, remove_worktree, run_git
from workbench.identity import slugify_name
from workbench.learning_md import (
    export_learning_markdown,
    merge_learning_paths,
    parse_learning_markdown,
)
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    parse_iso_date_for_yaml,
    to_plain_python,
)

learning_app = typer.Typer(help="Manage learning paths.")


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
        incoming["id"] = str(
            incoming.get("id") or slugify_name(str(incoming.get("name") or file.stem))
        )
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
        raise typer.Exit(code=2) from None
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
        import_summary = "merge into existing learning path"
    else:
        merged = incoming
        typer.echo(f"Creating learning path '{item_id}'.")
        import_summary = "new learning path"

    merged["updated"] = parse_iso_date_for_yaml(datetime.date.today().isoformat())
    dump_yaml_roundtrip(merged, target)
    _push_worktree(
        wt_dir,
        commitmsg.build_message("import", import_summary, f"learning/{item_id}"),
        allow_removals=False,
        mark_seen=False,
    )


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
