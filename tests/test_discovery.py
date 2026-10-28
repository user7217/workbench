"""Tests for local filesystem discovery and GitHub REST discovery (§14)."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from workbench.config import load_local_config
from workbench.demo_env import init_demo_environment
from workbench.discovery.github import run_github_discovery
from workbench.discovery.local import run_local_discovery
from workbench.gitops import init_repo, run_git


def test_local_discovery_owned_vs_third_party_idempotent_and_empty_repo(
    tmp_path: Path, monkeypatch: any
) -> None:
    """Local scan registers owned repos, skips third-party clone, ignores empty repos, and is idempotent (§14)."""
    demo_dir = tmp_path / "demo_disc"
    info = init_demo_environment(demo_dir)
    laptop_home = Path(info["laptop_home"])
    data_dir = Path(info["laptop_data_dir"])
    code_dir = Path(info["code_dir"])
    monkeypatch.setenv("HOME", str(laptop_home))

    # Add an empty git repo with 0 commits inside code_dir
    empty_repo = code_dir / "empty-no-commits"
    init_repo(empty_repo, branch="main")
    run_git(
        ["remote", "add", "origin", "https://github.com/demouser/empty-no-commits.git"],
        cwd=empty_repo,
    )

    cfg = load_local_config(home_dir=laptop_home)

    # First run registers `demo-habit-tracker`, skips `third-party-lib` and `empty-no-commits`
    registered1 = run_local_discovery(data_repo_dir=data_dir, config=cfg, home_dir=laptop_home)
    assert registered1 == ["demo-habit-tracker"]

    # Second run is idempotent (adds nothing)
    registered2 = run_local_discovery(data_repo_dir=data_dir, config=cfg, home_dir=laptop_home)
    assert registered2 == []

    # Verify third-party-lib and empty-no-commits were never registered
    run_git(["fetch", "origin", "main"], cwd=data_dir)
    run_git(["reset", "--hard", "origin/main"], cwd=data_dir)
    assert (data_dir / "projects" / "demo-habit-tracker" / "project.yaml").exists()
    assert not (data_dir / "projects" / "third-party-lib").exists()
    assert not (data_dir / "projects" / "empty-no-commits").exists()


@respx.mock
def test_github_discovery_skips_forks_archived_paginates_and_handles_rate_limits(
    tmp_path: Path, monkeypatch: any
) -> None:
    """GitHub fake skips forks & archived repos, follows pagination and rel=last root commit, sanitizes private descriptions, and respects rate limits (§10.3, §14)."""
    demo_dir = tmp_path / "demo_gh"
    info = init_demo_environment(demo_dir)
    laptop_home = Path(info["laptop_home"])
    data_dir = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(laptop_home))

    api_base = "https://api.github.com"

    # Page 1 has a fork, an archived repo, and one valid repo; Link header points to page 2
    respx.get(f"{api_base}/users/demouser/repos?per_page=100").mock(
        return_value=httpx.Response(
            200,
            headers={"Link": f'<{api_base}/users/demouser/repos?per_page=100&page=2>; rel="next"'},
            json=[
                {
                    "name": "forked-repo",
                    "full_name": "demouser/forked-repo",
                    "fork": True,
                    "archived": False,
                    "description": "Should be skipped",
                },
                {
                    "name": "archived-repo",
                    "full_name": "demouser/archived-repo",
                    "fork": False,
                    "archived": True,
                    "description": "Should be skipped",
                },
                {
                    "name": "demo-cloud-backup",
                    "full_name": "demouser/demo-cloud-backup",
                    "fork": False,
                    "archived": False,
                    "description": "Encrypted backup tool.",
                },
            ],
        )
    )

    # Page 2 has a repo whose description contains a local path (/Users/...) which must be blanked
    respx.get(f"{api_base}/users/demouser/repos?per_page=100&page=2").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "demo-secret-desc",
                    "full_name": "demouser/demo-secret-desc",
                    "fork": False,
                    "archived": False,
                    "description": "Syncs /Users/alice/private notes to server",
                }
            ],
        )
    )

    # Commits pagination for demo-cloud-backup: page 1 points to rel="last" page 5
    respx.get(f"{api_base}/repos/demouser/demo-cloud-backup/commits?per_page=1").mock(
        return_value=httpx.Response(
            200,
            headers={
                "Link": f'<{api_base}/repos/demouser/demo-cloud-backup/commits?per_page=1&page=5>; rel="last"'
            },
            json=[{"sha": "9999999999999999999999999999999999999999"}],
        )
    )
    respx.get(f"{api_base}/repos/demouser/demo-cloud-backup/commits?per_page=1&page=5").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "1111111111111111111111111111111111111111"}],
        )
    )

    # Commits for demo-secret-desc: single commit
    respx.get(f"{api_base}/repos/demouser/demo-secret-desc/commits?per_page=1").mock(
        return_value=httpx.Response(
            200,
            json=[{"sha": "2222222222222222222222222222222222222222"}],
        )
    )

    res = run_github_discovery(
        data_repo_dir=data_dir,
        github_owners=["demouser"],
        token="fake-read-token",
        api_base=api_base,
    )
    assert not res.rate_limited
    assert set(res.registered_ids) == {"demo-cloud-backup", "demo-secret-desc"}

    # Verify description privacy sanitization and root commit
    run_git(["fetch", "origin", "main"], cwd=data_dir)
    run_git(["reset", "--hard", "origin/main"], cwd=data_dir)
    cb_yaml = (data_dir / "projects" / "demo-cloud-backup" / "project.yaml").read_text(encoding="utf-8")
    assert "1111111111111111111111111111111111111111" in cb_yaml
    assert "Encrypted backup tool." in cb_yaml

    sd_yaml = (data_dir / "projects" / "demo-secret-desc" / "project.yaml").read_text(encoding="utf-8")
    assert "/Users/alice" not in sd_yaml
    assert "description: ''" in sd_yaml or 'description: ""' in sd_yaml

    # Now test rate limit handling (429 with Retry-After)
    respx.get(f"{api_base}/users/rateowner/repos?per_page=100").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "120", "X-RateLimit-Remaining": "0"},
            json={"message": "API rate limit exceeded"},
        )
    )
    rl_res = run_github_discovery(
        data_repo_dir=data_dir,
        github_owners=["rateowner"],
        token="fake-read-token",
        api_base=api_base,
    )
    assert rl_res.rate_limited is True
    assert rl_res.retry_after == 120
