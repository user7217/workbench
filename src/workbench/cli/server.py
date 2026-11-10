"""The `serve` command: the dashboard server without Docker."""

from __future__ import annotations

import os
import signal
import threading
import time

import typer

from workbench.cli._common import _fail
from workbench.config import get_workbench_dir, load_local_config

app = typer.Typer()

LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


@app.command("serve")
def cmd_serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Address to listen on (a Tailscale IP to reach it from your other devices)",
    ),
    port: int = typer.Option(8420, "--port", help="Port to listen on"),
    exit_with_parent: bool = typer.Option(
        False, "--exit-with-parent", hidden=True, help="Stop when the starting process exits"
    ),
) -> None:
    """Run the dashboard server (API and web UI) without Docker.

    Settings come from the same WORKBENCH_* environment variables as the Docker image. The data
    repo defaults to the one in ~/.workbench/config.toml, and the server keeps its own clone in
    ~/.workbench/server (apart from the CLI's).
    """
    cfg = load_local_config()
    if not os.environ.get("WORKBENCH_DATA_REMOTE"):
        if not cfg.data_repo_url:
            _fail("no data repo: set WORKBENCH_DATA_REMOTE or run `workbench setup` first.")
        os.environ["WORKBENCH_DATA_REMOTE"] = cfg.data_repo_url
        os.environ.setdefault("WORKBENCH_BRANCH", cfg.branch)
    os.environ.setdefault("WORKBENCH_STATE_DIR", str(get_workbench_dir() / "server"))
    # Today's Work follows config.toml (`workbench today on|off`) unless the variable is set
    os.environ.setdefault("WORKBENCH_TODAY", cfg.today)
    if host in LOOPBACK_HOSTS:
        # Only this machine can connect; also refuse requests addressed to any other name
        os.environ.setdefault("WORKBENCH_ALLOWED_HOSTS", "127.0.0.1,localhost")

    import uvicorn

    from workbench.server.app import create_app

    if exit_with_parent:
        _exit_with_parent()
    typer.echo(f"Workbench dashboard on http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


def _exit_with_parent() -> None:
    """Stop the server once the process that started it is gone.

    The desktop app runs the server as a child; if the app is force-quit or crashes it cannot stop
    the server, which would keep the port. An orphaned process gets a new parent, so watch for that.
    """
    parent = os.getppid()

    def watch() -> None:
        while os.getppid() == parent:
            time.sleep(2)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()
