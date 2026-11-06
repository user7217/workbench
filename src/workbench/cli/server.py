"""The `serve` command: the dashboard server without Docker."""

from __future__ import annotations

import os

import typer

from workbench.cli._common import _fail
from workbench.config import get_workbench_dir, load_local_config

app = typer.Typer()


@app.command("serve")
def cmd_serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Address to listen on (a Tailscale IP to reach it from your other devices)",
    ),
    port: int = typer.Option(8420, "--port", help="Port to listen on"),
) -> None:
    """Run the dashboard server (API and web UI) without Docker.

    Settings come from the same WORKBENCH_* environment variables as the Docker image. The data
    repo defaults to the one in ~/.workbench/config.toml, and the server keeps its own clone in
    ~/.workbench/server (apart from the CLI's).
    """
    if not os.environ.get("WORKBENCH_DATA_REMOTE"):
        cfg = load_local_config()
        if not cfg.data_repo_url:
            _fail("no data repo: set WORKBENCH_DATA_REMOTE or run `workbench setup` first.")
        os.environ["WORKBENCH_DATA_REMOTE"] = cfg.data_repo_url
        os.environ.setdefault("WORKBENCH_BRANCH", cfg.branch)
    os.environ.setdefault("WORKBENCH_STATE_DIR", str(get_workbench_dir() / "server"))

    import uvicorn

    from workbench.server.app import create_app

    typer.echo(f"Workbench dashboard on http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
