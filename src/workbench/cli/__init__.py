"""Typer CLI application for Workbench.

Each module holds the commands of one area on its own Typer app; `app` merges them in the
order below, which is also the order `workbench --help` lists them in.
"""

from __future__ import annotations

import typer

from workbench import __version__
from workbench.cli import (
    agents,
    demo,
    discover,
    hierarchy,
    history,
    items,
    learning,
    projects,
    server,
    setup,
)

app = typer.Typer(
    help="Workbench CLI — personal inventory of software projects and learning paths."
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"workbench {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Show the version and exit"
    ),
) -> None:
    """Workbench CLI — personal inventory of software projects and learning paths."""


for _commands in (projects, hierarchy, items, discover, history, agents, server, setup):
    app.add_typer(_commands.app)
app.add_typer(learning.learning_app, name="learning")
app.add_typer(demo.demo_app, name="demo")
