"""Global pytest fixtures enforcing strict git and HOME isolation (§0.2)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from workbench.gitops import make_isolated_git_env

# Capture Playwright browser cache location before HOME is overridden in each test: where
# `playwright install` put the browsers, unless PLAYWRIGHT_BROWSERS_PATH says otherwise (as in CI)
_REAL_HOME = Path(os.environ.get("HOME", str(Path.home())))
if sys.platform == "darwin":
    _PW_DEFAULT = _REAL_HOME / "Library" / "Caches" / "ms-playwright"
elif sys.platform == "win32":
    _PW_DEFAULT = Path(os.environ.get("LOCALAPPDATA", str(_REAL_HOME))) / "ms-playwright"
else:
    _PW_DEFAULT = _REAL_HOME / ".cache" / "ms-playwright"
_PW_CACHE = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(_PW_DEFAULT))


_SERVER_ENV_VARS = (
    "WORKBENCH_ALLOWED_HOSTS",
    "WORKBENCH_DATA_REMOTE",
    "WORKBENCH_BRANCH",
    "WORKBENCH_STATE_DIR",
    "WORKBENCH_AGENT_SOCKET",
    "WORKBENCH_AGENT_SOCKET_UID",
    "WORKBENCH_AGENT_SOCKET_GID",
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
