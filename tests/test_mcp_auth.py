"""The connector's configuration and GitHub login: fail closed, one allowed account, 401s.

The real GitHub and claude.ai sign-in cannot run here; GitHub's API is a mock transport.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import fastmcp
import httpx2
import pytest
from mcp.server.auth.provider import TokenError
from starlette.testclient import TestClient

from workbench.mcp import server as mcp_server
from workbench.mcp.auth import build_auth
from workbench.mcp.config import ConfigError, McpConfig, load_config

ALLOWED_ID = 4242
SECRET = "s" * 40
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def _env(state_dir: Path, **overrides: str) -> dict[str, str]:
    env = {
        "WORKBENCH_MCP_PUBLIC_URL": "https://mcp.example.com",
        "WORKBENCH_MCP_GITHUB_CLIENT_ID": "Ov23liTest",
        "WORKBENCH_MCP_GITHUB_CLIENT_SECRET": "test-client-secret",
        "WORKBENCH_MCP_ALLOWED_GITHUB_ID": str(ALLOWED_ID),
        "WORKBENCH_MCP_SECRET_KEY": SECRET,
        "WORKBENCH_MCP_STATE_DIR": str(state_dir),
        "WORKBENCH_AGENT_SOCKET": str(state_dir / "agent.sock"),
    }
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> McpConfig:
    # build_auth points FastMCP's home at the state dir; put it back after the test
    monkeypatch.setattr(fastmcp.settings, "home", fastmcp.settings.home)
    return load_config(_env(tmp_path / "state"))


def _github(user_id: int, login: str = "someone") -> httpx2.AsyncClient:
    """A client whose GitHub API says every token belongs to `user_id`."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/user":
            return httpx2.Response(200, json={"id": user_id, "login": login})
        if request.url.path == "/user/repos":
            return httpx2.Response(200, json=[], headers={"x-oauth-scopes": "read:user"})
        return httpx2.Response(404)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


# --- configuration: fail closed ---


def test_complete_config(tmp_path: Path) -> None:
    cfg = load_config(_env(tmp_path, WORKBENCH_MCP_PUBLIC_URL="https://mcp.example.com/"))
    assert cfg.public_url == "https://mcp.example.com"
    assert cfg.mcp_url == "https://mcp.example.com/mcp"
    assert cfg.allowed_github_id == ALLOWED_ID
    assert cfg.host == "0.0.0.0" and cfg.port == 8000
    assert CLAUDE_CALLBACK in cfg.allowed_redirect_uris


def test_defaults_for_container_paths() -> None:
    env = _env(Path("/x"))
    del env["WORKBENCH_MCP_STATE_DIR"], env["WORKBENCH_AGENT_SOCKET"]
    cfg = load_config(env)
    assert cfg.state_dir == Path("/state")
    assert cfg.agent_socket == Path("/agent/agent.sock")


def test_missing_auth_settings_are_all_named() -> None:
    with pytest.raises(ConfigError) as err:
        load_config({})
    message = str(err.value)
    for name in (
        "WORKBENCH_MCP_PUBLIC_URL",
        "WORKBENCH_MCP_GITHUB_CLIENT_ID",
        "WORKBENCH_MCP_GITHUB_CLIENT_SECRET",
        "WORKBENCH_MCP_ALLOWED_GITHUB_ID",
        "WORKBENCH_MCP_SECRET_KEY",
    ):
        assert name in message


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"WORKBENCH_MCP_ALLOWED_GITHUB_ID": "octocat"}, "numeric GitHub user id"),
        ({"WORKBENCH_MCP_ALLOWED_GITHUB_ID": "0"}, "numeric GitHub user id"),
        ({"WORKBENCH_MCP_SECRET_KEY": "short"}, "at least 32 characters"),
        ({"WORKBENCH_MCP_PUBLIC_URL": "http://mcp.example.com"}, "https://"),
        ({"WORKBENCH_MCP_PUBLIC_URL": "https://mcp.example.com/mcp"}, "just the scheme"),
        ({"WORKBENCH_MCP_GITHUB_CLIENT_SECRET": "  "}, "CLIENT_SECRET is not set"),
        ({"WORKBENCH_MCP_PORT": "eighty"}, "port number"),
    ],
)
def test_bad_settings_are_refused(tmp_path: Path, override: dict[str, str], expected: str) -> None:
    with pytest.raises(ConfigError, match=expected):
        load_config(_env(tmp_path, **override))


def test_http_is_allowed_only_for_localhost(tmp_path: Path) -> None:
    cfg = load_config(_env(tmp_path, WORKBENCH_MCP_PUBLIC_URL="http://localhost:8000"))
    assert cfg.mcp_url == "http://localhost:8000/mcp"


def test_main_refuses_to_start_without_auth(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in list(_env(Path("/x"))):
        monkeypatch.delenv(name, raising=False)
    started: list[Any] = []
    monkeypatch.setattr(mcp_server, "create_app", lambda cfg: started.append(cfg))
    with pytest.raises(SystemExit) as exit_info:
        mcp_server.main()
    assert exit_info.value.code == 2
    assert started == []
    assert "will not start" in capsys.readouterr().err


# --- the one allowed GitHub account ---


async def test_verifier_accepts_only_the_allowed_id(config: McpConfig) -> None:
    allowed = build_auth(config, http_client=_github(ALLOWED_ID, "owner"))
    token = await allowed._owner_verifier.verify_token("gho_token")
    assert token is not None and token.subject == str(ALLOWED_ID)

    other = build_auth(config, http_client=_github(ALLOWED_ID + 1, "owner-lookalike"))
    assert await other._owner_verifier.verify_token("gho_token") is None


async def test_no_tokens_are_issued_to_another_account(config: McpConfig) -> None:
    other = build_auth(config, http_client=_github(ALLOWED_ID + 1))
    with pytest.raises(TokenError) as err:
        await other._extract_upstream_claims({"access_token": "gho_token"})
    assert err.value.error == "invalid_grant"

    allowed = build_auth(config, http_client=_github(ALLOWED_ID, "owner"))
    claims = await allowed._extract_upstream_claims({"access_token": "gho_token"})
    assert claims == {"github_id": str(ALLOWED_ID), "github_login": "owner"}


async def test_an_unreachable_github_refuses_the_login(config: McpConfig) -> None:
    def down(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    auth = build_auth(config, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(down)))
    with pytest.raises(TokenError):
        await auth._extract_upstream_claims({"access_token": "gho_token"})


# --- the HTTP app ---


@pytest.fixture
def http(config: McpConfig):
    with TestClient(mcp_server.create_app(config), base_url="https://mcp.example.com") as c:
        yield c


def test_mcp_endpoint_requires_a_token(http: TestClient) -> None:
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    headers = {"accept": "application/json, text/event-stream"}
    response = http.post("/mcp", json=initialize, headers=headers)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        'resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )

    forged = http.post(
        "/mcp", json=initialize, headers={**headers, "authorization": "Bearer not-a-token"}
    )
    assert forged.status_code == 401


def test_oauth_discovery_metadata(http: TestClient) -> None:
    resource = http.get("/.well-known/oauth-protected-resource/mcp").json()
    assert resource["resource"] == "https://mcp.example.com/mcp"
    assert resource["authorization_servers"][0].rstrip("/") == "https://mcp.example.com"

    server = http.get("/.well-known/oauth-authorization-server").json()
    assert server["authorization_endpoint"] == "https://mcp.example.com/authorize"
    assert server["token_endpoint"] == "https://mcp.example.com/token"
    assert server["registration_endpoint"] == "https://mcp.example.com/register"
    assert server["code_challenge_methods_supported"] == ["S256"]
    assert server["scopes_supported"] == ["read:user"]


def _register(http: TestClient, redirect_uri: str):
    return http.post(
        "/register",
        json={
            "client_name": "test",
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )


def test_only_claude_callbacks_can_register(http: TestClient) -> None:
    assert _register(http, "https://evil.example.com/callback").status_code == 400
    assert _register(http, CLAUDE_CALLBACK).status_code == 201


async def test_client_registrations_survive_a_restart(http: TestClient, config: McpConfig) -> None:
    client_id = _register(http, CLAUDE_CALLBACK).json()["client_id"]
    assert any(config.state_dir.rglob("*")), "nothing was written to the state volume"

    restarted = build_auth(config)  # a new process with the same state volume and key
    assert await restarted.get_client(client_id) is not None

    rotated = build_auth(
        load_config(_env(config.state_dir, WORKBENCH_MCP_SECRET_KEY="r" * 40))
    )  # a new key starts afresh
    assert await rotated.get_client(client_id) is None
