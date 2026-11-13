"""Global pytest fixtures enforcing strict git and HOME isolation (§0.2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from workbench.gitops import make_isolated_git_env

# Capture Playwright browser cache location before HOME is overridden in each test
_REAL_HOME = os.environ.get("HOME", str(Path.home()))
_PW_CACHE = os.environ.get(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path(_REAL_HOME) / "Library" / "Caches" / "ms-playwright"),
)


_SERVER_ENV_VARS = (
    "WORKBENCH_ALLOWED_HOSTS",
    "WORKBENCH_DATA_REMOTE",
    "WORKBENCH_BRANCH",
    "WORKBENCH_STATE_DIR",
)


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Automatically isolate HOME and git config in every test (§0.2)."""
    fake_home = tmp_path / "isolated_home"
    iso_env = make_isolated_git_env(fake_home)
    for k, v in iso_env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", _PW_CACHE)
    # `workbench serve` writes its settings into os.environ; a test that runs it must not
    # leave them behind (an allowed-hosts list makes every later TestClient request a 400)
    for var in _SERVER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return fake_home
