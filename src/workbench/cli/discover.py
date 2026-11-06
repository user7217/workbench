"""The `discover` command: register owned repositories found in local folders."""

from __future__ import annotations

import typer

from workbench.cli._common import _get_data_repo_dir
from workbench.config import load_local_config
from workbench.discovery.local import run_local_discovery

app = typer.Typer()


@app.command("discover")
def cmd_discover(
    local: bool = typer.Option(True, "--local", help="Scan configured local roots"),
) -> None:
    """Scan local folders and register unknown owned repositories."""
    data_dir = _get_data_repo_dir()
    cfg = load_local_config()
    try:
        registered = run_local_discovery(data_repo_dir=data_dir, config=cfg)
    except Exception as exc:
        typer.echo(f"Discovery failed: {exc}", err=True)
        raise typer.Exit(code=2) from None

    if registered:
        typer.echo(f"Registered {len(registered)} repository(s): {', '.join(registered)}")
    else:
        typer.echo("No new repositories discovered.")
