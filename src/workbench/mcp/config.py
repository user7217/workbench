"""Configuration of the MCP server, read from the environment.

Every auth setting is required: with any of them missing or malformed, `load_config` raises
`ConfigError` naming each problem and the server does not start. There is no way to run it
without the GitHub login.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MCP_PATH = "/mcp"
# Where claude.ai (web, desktop, mobile) sends the browser back after sign-in
DEFAULT_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
MIN_SECRET_KEY_LENGTH = 32

ENV_PUBLIC_URL = "WORKBENCH_MCP_PUBLIC_URL"
ENV_CLIENT_ID = "WORKBENCH_MCP_GITHUB_CLIENT_ID"
ENV_CLIENT_SECRET = "WORKBENCH_MCP_GITHUB_CLIENT_SECRET"
ENV_ALLOWED_ID = "WORKBENCH_MCP_ALLOWED_GITHUB_ID"
ENV_SECRET_KEY = "WORKBENCH_MCP_SECRET_KEY"
ENV_STATE_DIR = "WORKBENCH_MCP_STATE_DIR"
ENV_REDIRECT_URIS = "WORKBENCH_MCP_ALLOWED_REDIRECT_URIS"
ENV_HOST = "WORKBENCH_MCP_HOST"
ENV_PORT = "WORKBENCH_MCP_PORT"
ENV_AGENT_SOCKET = "WORKBENCH_AGENT_SOCKET"


class ConfigError(ValueError):
    """The environment does not configure the server completely; the message lists every gap."""


@dataclass(frozen=True)
class McpConfig:
    public_url: str  # scheme and host, no trailing slash; the connector URL is this plus MCP_PATH
    github_client_id: str
    github_client_secret: str
    allowed_github_id: int
    secret_key: str  # signs the issued tokens and encrypts the stored OAuth state
    state_dir: Path
    agent_socket: Path
    allowed_redirect_uris: tuple[str, ...] = DEFAULT_REDIRECT_URIS
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def mcp_url(self) -> str:
        return self.public_url + MCP_PATH


def _check_public_url(raw: str, problems: list[str]) -> str:
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        problems.append(f"{ENV_PUBLIC_URL} must be an https:// URL (http only for localhost)")
    elif not parts.hostname or parts.path or parts.query or parts.fragment:
        problems.append(
            f"{ENV_PUBLIC_URL} must be just the scheme and host, like https://mcp.example.com"
        )
    return url


def load_config(env: Mapping[str, str] | None = None) -> McpConfig:
    """Read the configuration from `env` (default: os.environ); raise ConfigError if incomplete."""
    env = os.environ if env is None else env
    problems: list[str] = []

    def required(name: str) -> str:
        value = env.get(name, "").strip()
        if not value:
            problems.append(f"{name} is not set")
        return value

    public_url = required(ENV_PUBLIC_URL)
    if public_url:
        public_url = _check_public_url(public_url, problems)
    client_id = required(ENV_CLIENT_ID)
    client_secret = required(ENV_CLIENT_SECRET)

    allowed_raw = required(ENV_ALLOWED_ID)
    allowed_id = 0
    if allowed_raw:
        if allowed_raw.isdigit() and int(allowed_raw) > 0:
            allowed_id = int(allowed_raw)
        else:
            problems.append(
                f"{ENV_ALLOWED_ID} must be the numeric GitHub user id (the `id` field of "
                "https://api.github.com/users/<login>), not the login"
            )

    secret_key = required(ENV_SECRET_KEY)
    if secret_key and len(secret_key) < MIN_SECRET_KEY_LENGTH:
        problems.append(
            f"{ENV_SECRET_KEY} must be at least {MIN_SECRET_KEY_LENGTH} characters "
            '(python3 -c "import secrets; print(secrets.token_urlsafe(48))")'
        )

    redirect_raw = env.get(ENV_REDIRECT_URIS, "").strip()
    redirect_uris = DEFAULT_REDIRECT_URIS
    if redirect_raw:
        redirect_uris = tuple(u.strip() for u in redirect_raw.split(",") if u.strip())

    port_raw = env.get(ENV_PORT, "").strip() or "8000"
    port = 8000
    if port_raw.isdigit() and 0 < int(port_raw) < 65536:
        port = int(port_raw)
    else:
        problems.append(f"{ENV_PORT} must be a port number")

    if problems:
        raise ConfigError(
            "The Workbench MCP server is not configured, so it will not start:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\nSee docs/web-connector.md."
        )
    return McpConfig(
        public_url=public_url,
        github_client_id=client_id,
        github_client_secret=client_secret,
        allowed_github_id=allowed_id,
        secret_key=secret_key,
        state_dir=Path(env.get(ENV_STATE_DIR, "").strip() or "/state"),
        agent_socket=Path(env.get(ENV_AGENT_SOCKET, "").strip() or "/agent/agent.sock"),
        allowed_redirect_uris=redirect_uris,
        host=env.get(ENV_HOST, "").strip() or "0.0.0.0",
        port=port,
    )
