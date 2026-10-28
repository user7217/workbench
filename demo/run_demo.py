"""Start a local Workbench server backed by isolated artificial demo data (§11, §15)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import uvicorn

from workbench.demo_env import FakeGitHubServer, init_demo_environment
from workbench.gitops import make_isolated_git_env
from workbench.server.app import create_app
from workbench.server.sync import ServerState


def main() -> None:
    demo_root = Path(
        os.environ.get("WORKBENCH_DEMO_DIR", str(Path(tempfile.gettempdir()) / "workbench-demo-live"))
    )
    if demo_root.exists():
        import shutil

        shutil.rmtree(demo_root, ignore_errors=True)

    info = init_demo_environment(demo_root)
    laptop_home = Path(info["laptop_home"])
    iso_env = make_isolated_git_env(laptop_home)

    fake_gh = FakeGitHubServer(Path(info["github_fixtures"]))
    gh_url = fake_gh.start()

    state = ServerState(
        remote_url=info["remote_bare"],
        state_dir=demo_root / "server-state",
        branch="main",
        env=iso_env,
        github_owners=["demouser"],
        github_read_token="demo-fake-token",
        github_api_base=gh_url,
    )

    host = os.environ.get("WORKBENCH_BIND", "127.0.0.1")
    port = int(os.environ.get("WORKBENCH_PORT", "8420"))

    app = create_app(state=state, poll_seconds=60.0)
    print(f"Workbench demo environment initialized at {demo_root}")
    print(f"Fake GitHub API running at {gh_url}")
    print(f"Opening Workbench dashboard at http://{host}:{port}")
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    finally:
        fake_gh.stop()


if __name__ == "__main__":
    main()
