"""Serve the agent API on a Unix socket, as a second uvicorn server inside the dashboard process.

Enabled by WORKBENCH_AGENT_SOCKET=<path>; unset means no socket and no change to the server.
The dashboard's lifespan starts it after the first sync and stops it on shutdown, so it shares
the dashboard's ServerState (one snapshot, one repo lock serializing every write).

Who can connect is decided by the socket file alone: it is mode 0600, owned by
WORKBENCH_AGENT_SOCKET_UID / WORKBENCH_AGENT_SOCKET_GID when set. The claude.ai connector runs
in a rootless container whose root user is an unprivileged host user (uid 1001, `mcpsvc`), so
the socket is chowned to that host uid. The dashboard container runs as root under rootful
Docker, which is what allows the chown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

logger = logging.getLogger(__name__)

SOCKET_MODE = 0o600
# Seconds an in-flight agent request may take to finish once the dashboard shuts down
_GRACEFUL_SHUTDOWN_SECONDS = 10
_STARTUP_POLL_SECONDS = 0.01


@dataclass(frozen=True)
class AgentSocketSettings:
    path: Path
    uid: int | None = None
    gid: int | None = None


def _int_env(environ: Mapping[str, str], name: str) -> int | None:
    raw = environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from None


def agent_socket_settings_from_env(
    environ: Mapping[str, str] = os.environ,
) -> AgentSocketSettings | None:
    """Settings from WORKBENCH_AGENT_SOCKET[_UID|_GID]; None when the socket is not configured."""
    raw_path = environ.get("WORKBENCH_AGENT_SOCKET", "").strip()
    if not raw_path:
        return None
    return AgentSocketSettings(
        path=Path(raw_path),
        uid=_int_env(environ, "WORKBENCH_AGENT_SOCKET_UID"),
        gid=_int_env(environ, "WORKBENCH_AGENT_SOCKET_GID"),
    )


def _remove_stale_socket(path: Path) -> None:
    """Remove a socket left by an earlier run; refuse to delete anything that is not a socket."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(mode):
        raise RuntimeError(f"Agent socket path {path} exists and is not a socket")
    path.unlink()


def bind_agent_socket(settings: AgentSocketSettings) -> socket.socket:
    """Bind (not yet listening) a Unix socket at settings.path, mode 0600, chowned as configured."""
    path = settings.path
    path.parent.mkdir(parents=True, exist_ok=True)
    _remove_stale_socket(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
        # Restricted before listen(): until then every connect is refused, so there is no
        # moment when the socket accepts connections from other users.
        os.chmod(path, SOCKET_MODE)
        if settings.uid is not None or settings.gid is not None:
            os.chown(
                path,
                settings.uid if settings.uid is not None else -1,
                settings.gid if settings.gid is not None else -1,
            )
    except BaseException:
        sock.close()
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        raise
    return sock


def _log_unexpected_exit(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Agent socket server stopped", exc_info=task.exception())


class _AgentUvicornServer(uvicorn.Server):
    """A uvicorn server that leaves signal handling to the dashboard's own server.

    The dashboard's uvicorn server owns SIGINT/SIGTERM; its lifespan shutdown stops this one.
    """

    def capture_signals(self) -> contextlib.AbstractContextManager[None]:  # type: ignore[override]
        return contextlib.nullcontext()


class AgentSocketServer:
    """Runs `app` on the configured Unix socket in the current event loop."""

    def __init__(self, app: FastAPI, settings: AgentSocketSettings) -> None:
        self.app = app
        self.settings = settings
        self._server: _AgentUvicornServer | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Bind the socket and wait until the server accepts connections; raises if it cannot."""
        sock = bind_agent_socket(self.settings)
        config = uvicorn.Config(
            self.app,
            lifespan="off",
            # Keep the logging the dashboard's own server set up
            log_config=None,
            timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS,
        )
        self._server = _AgentUvicornServer(config)
        self._task = asyncio.create_task(
            self._server.serve(sockets=[sock]), name="workbench-agent-socket"
        )
        try:
            while not self._server.started:
                if self._task.done():
                    self._task.result()
                    raise RuntimeError("Agent socket server exited during startup")
                await asyncio.sleep(_STARTUP_POLL_SECONDS)
        except BaseException:
            if not self._task.done():
                self._task.cancel()
            sock.close()
            self._unlink_socket()
            raise
        self._task.add_done_callback(_log_unexpected_exit)
        logger.info(
            "Agent API listening on unix socket %s (mode %o, uid %s, gid %s)",
            self.settings.path,
            SOCKET_MODE,
            self.settings.uid if self.settings.uid is not None else "unchanged",
            self.settings.gid if self.settings.gid is not None else "unchanged",
        )

    async def stop(self) -> None:
        """Stop serving and remove the socket file."""
        try:
            if self._server is not None:
                self._server.should_exit = True
            if self._task is not None:
                # A failure was already logged by _log_unexpected_exit
                with contextlib.suppress(Exception):
                    await self._task
        finally:
            self._unlink_socket()

    def _unlink_socket(self) -> None:
        path = self.settings.path
        with contextlib.suppress(FileNotFoundError):
            if stat.S_ISSOCK(path.lstat().st_mode):
                path.unlink()
