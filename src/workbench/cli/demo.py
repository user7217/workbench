"""The `demo` sub-app: the artificial demo environment."""

from __future__ import annotations

from pathlib import Path

import typer

from workbench.demo import init_demo_environment

demo_app = typer.Typer(help="Manage artificial demo environment.")


@demo_app.command("init")
def cmd_demo_init(
    directory: Path = typer.Argument(
        ..., help="Directory where artificial demo environment will be created"
    ),
) -> None:
    """Create the artificial demo environment with bare remote, fake HOME, and fake repos."""
    info = init_demo_environment(directory)
    typer.echo(f"Initialized demo environment at {info['base_dir']}")
