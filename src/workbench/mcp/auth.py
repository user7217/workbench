"""GitHub login for the MCP server, open to one GitHub account only.

FastMCP's OAuth proxy is the authorization server claude.ai talks to: it serves the discovery
metadata, registers claude.ai as a client (CIMD or dynamic registration), shows a consent page,
sends the browser to GitHub, and issues its own signed tokens that point at the stored GitHub
token. On top of that this module checks the GitHub account's numeric id twice:

- when the code is exchanged for tokens: any other account gets no token at all;
- on every MCP request, through the token verifier: a token whose GitHub account is not the
  allowed one (or whose GitHub grant was revoked) is refused with 401.

The ids are compared, not logins, because a GitHub login can be renamed and then claimed by
someone else.
"""

from __future__ import annotations

import logging
from typing import Any

import fastmcp
import httpx2
from fastmcp.server.auth import TokenVerificationError
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.github import GitHubTokenVerifier
from mcp.server.auth.provider import TokenError

from workbench.mcp.config import McpConfig

logger = logging.getLogger(__name__)

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
# Enough to read the account's id from /user; no access to repositories or anything else
GITHUB_SCOPES = ["read:user"]
# Each MCP request re-checks the GitHub token; caching the answer this long saves GitHub calls,
# and is also how long a revoked GitHub grant can keep working
VERIFY_CACHE_SECONDS = 300


class AllowlistedGitHubTokenVerifier(GitHubTokenVerifier):
    """A GitHub token verifier that accepts only the one allowed GitHub user id."""

    def __init__(self, *, allowed_user_id: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.allowed_user_id = str(allowed_user_id)

    def is_allowed(self, result: AccessToken | None) -> bool:
        if result is None:
            return False
        if result.subject == self.allowed_user_id:
            return True
        login = (result.claims or {}).get("login")
        logger.warning(
            "Refused GitHub account %s (id %s): not the allowed account", login, result.subject
        )
        return False

    async def verify_token(self, token: str) -> AccessToken | None:
        result = await super().verify_token(token)
        return result if self.is_allowed(result) else None


class OwnerOnlyGitHubAuth(OAuthProxy):
    """FastMCP's OAuth proxy in front of GitHub, issuing tokens only to the allowed account."""

    def __init__(self, *, verifier: AllowlistedGitHubTokenVerifier, **kwargs: Any) -> None:
        super().__init__(token_verifier=verifier, **kwargs)
        self._owner_verifier = verifier

    async def _extract_upstream_claims(self, idp_tokens: dict[str, Any]) -> dict[str, Any] | None:
        # Called while the authorization code is exchanged, before any token is issued
        try:
            result = await self._owner_verifier.verify_token(idp_tokens.get("access_token", ""))
        except TokenVerificationError as exc:
            raise TokenError(
                "invalid_grant", "GitHub could not be reached to check the account; try again"
            ) from exc
        if result is None:
            raise TokenError("invalid_grant", "This GitHub account may not use this server")
        return {"github_id": result.subject, "github_login": (result.claims or {}).get("login")}


def build_auth(
    config: McpConfig, http_client: httpx2.AsyncClient | None = None
) -> OwnerOnlyGitHubAuth:
    """The auth provider for the server, storing its state under `config.state_dir`.

    `http_client` (tests) replaces the client the GitHub account checks are made with.
    """
    config.state_dir.mkdir(parents=True, exist_ok=True)
    # FastMCP keeps client registrations and the (encrypted) GitHub tokens in a file store under
    # its home; pointing that at the state volume is what lets logins survive a restart. The
    # store's encryption key is derived from the signing key, so a new key starts afresh.
    fastmcp.settings.home = config.state_dir
    verifier = AllowlistedGitHubTokenVerifier(
        allowed_user_id=config.allowed_github_id,
        required_scopes=GITHUB_SCOPES,
        cache_ttl_seconds=VERIFY_CACHE_SECONDS,
        http_client=http_client,
    )
    return OwnerOnlyGitHubAuth(
        verifier=verifier,
        upstream_authorization_endpoint=GITHUB_AUTHORIZE_URL,
        upstream_token_endpoint=GITHUB_TOKEN_URL,
        upstream_client_id=config.github_client_id,
        upstream_client_secret=config.github_client_secret,
        base_url=config.public_url,
        issuer_url=config.public_url,
        allowed_client_redirect_uris=list(config.allowed_redirect_uris),
        jwt_signing_key=config.secret_key,
        require_authorization_consent=True,
    )
