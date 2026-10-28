"""Tests for FastAPI server sync, invalid push resilience, snapshot restore, non-main branch ignore, X-Workbench protection, and mark-done roundtrip (§14)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.demo_env import init_demo_environment
from workbench.gitops import (
    GitError,
    commit_all,
    create_worktree,
    init_bare_repo,
    init_repo,
    push_head_to_branch,
    remove_worktree,
    run_git,
)
from workbench.server import sync as sync_module
from workbench.server.app import create_app
from workbench.server.sync import ServerState
from workbench.validate import ValidationResult
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_roundtrip


def test_server_sync_valid_invalid_restart_non_main_and_mark_done(
    tmp_path: Path, monkeypatch: any
) -> None:
    """Verify all §14 server requirements."""
    demo_dir = tmp_path / "demo_srv"
    info = init_demo_environment(demo_dir)
    remote_bare = Path(info["remote_bare"])
    laptop_data = Path(info["laptop_data_dir"])
    laptop_home = Path(info["laptop_home"])
    monkeypatch.setenv("HOME", str(laptop_home))

    server_state_dir = tmp_path / "server_state"
    state = ServerState(
        remote_url=str(remote_bare),
        state_dir=server_state_dir,
        branch="main",
    )
    app = create_app(state=state, enable_background_loop=False)

    with TestClient(app) as client:
        # 1. Initial startup sync loaded all demo projects and learning paths
        snap_resp = client.get("/api/snapshot")
        assert snap_resp.status_code == 200
        snap = snap_resp.json()
        assert len(snap["projects"]) >= 6
        assert len(snap["learning"]) >= 3
        initial_commit = snap["commit"]
        assert initial_commit is not None

        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        assert status_resp.json()["last_good_commit"] == initial_commit
        assert status_resp.json()["last_error"] is None

        # 2. Never loads a non-main branch: create a feature branch on remote with an extra project and confirm ignored
        wt_branch = tmp_path / "wt_other_branch"
        create_worktree(laptop_data, wt_branch, ref="origin/main")
        (wt_branch / "projects" / "should-not-load").mkdir(parents=True)
        (wt_branch / "projects" / "should-not-load" / "project.yaml").write_text(
            "schema_version: 1\nid: should-not-load\nname: Non Main Branch Project\n"
            "description: ''\nstatus: active\norigin: manual\nupdated: 2026-10-20\n"
            "repos: []\nlinks: []\nphases: []\n",
            encoding="utf-8",
        )
        commit_all(wt_branch, "Commit on other branch")
        push_head_to_branch(wt_branch, branch="experimental-branch")
        remove_worktree(laptop_data, wt_branch)

        sync_resp = client.post(
            "/api/sync",
            headers={"X-Workbench": "1", "Content-Type": "application/json"},
            json={},
        )
        assert sync_resp.status_code == 200
        snap_after_branch = client.get("/api/snapshot").json()
        assert all(p["id"] != "should-not-load" for p in snap_after_branch["projects"])

        # 3. Valid push to main becomes visible within one poll
        wt_valid = tmp_path / "wt_valid_push"
        create_worktree(laptop_data, wt_valid, ref="origin/main")
        ws_file = wt_valid / "projects" / "demo-weather-station" / "project.yaml"
        doc = load_yaml_roundtrip(ws_file)
        doc["description"] = "Updated via valid push for server test."
        dump_yaml_roundtrip(doc, ws_file)
        new_valid_sha = commit_all(wt_valid, "Valid update to weather station")
        push_head_to_branch(wt_valid, branch="main")
        remove_worktree(laptop_data, wt_valid)

        state.sync_once()
        snap_valid = client.get("/api/snapshot").json()
        assert snap_valid["commit"] == new_valid_sha
        ws_proj = next(p for p in snap_valid["projects"] if p["id"] == "demo-weather-station")
        assert ws_proj["description"] == "Updated via valid push for server test."

        # 4. Invalid push to main keeps the previous snapshot and reports the error on /api/status
        wt_invalid = tmp_path / "wt_invalid_push"
        create_worktree(laptop_data, wt_invalid, ref="origin/main")
        bad_file = wt_invalid / "projects" / "demo-weather-station" / "project.yaml"
        bad_file.write_text(
            "schema_version: 1\nid: demo-weather-station\nname: Weather Station\n"
            "description: Leaked path /Users/secret/file.txt\nstatus: active\n"
            "origin: agent\nupdated: 2026-10-20\nrepos: []\nlinks: []\nphases: []\n",
            encoding="utf-8",
        )
        bad_sha = commit_all(wt_invalid, "Invalid commit with local path")
        push_head_to_branch(wt_invalid, branch="main")
        remove_worktree(laptop_data, wt_invalid)

        ok = state.sync_once()
        assert ok is False
        st_bad = client.get("/api/status").json()
        assert st_bad["last_good_commit"] == new_valid_sha
        assert st_bad["last_error"] is not None
        assert st_bad["last_error"]["commit"] == bad_sha
        assert "local_path_users" in st_bad["last_error"]["message"]

        # Snapshot still serves the previous good version!
        snap_still_good = client.get("/api/snapshot").json()
        assert snap_still_good["commit"] == new_valid_sha

        # Revert the bad commit on main so remote is clean again
        wt_fix = tmp_path / "wt_fix"
        create_worktree(laptop_data, wt_fix, ref="origin/main")
        run_git(["revert", "--no-edit", bad_sha], cwd=wt_fix)
        push_head_to_branch(wt_fix, branch="main")
        remove_worktree(laptop_data, wt_fix)
        assert state.sync_once() is True
        assert client.get("/api/status").json()["last_error"] is None

        # 5. Write without X-Workbench header is rejected
        no_header_resp = client.post(
            "/api/learning/demo-signal-processing/items/aliasing/status",
            json={"status": "done"},
        )
        assert no_header_resp.status_code == 403

        # 6. Mark-done with X-Workbench: 1 updates snapshot and round-trips to the remote bare repo
        mark_resp = client.post(
            "/api/learning/demo-signal-processing/items/aliasing/status",
            headers={"X-Workbench": "1", "Content-Type": "application/json"},
            json={"status": "done"},
        )
        assert mark_resp.status_code == 200, mark_resp.text
        updated_dsp = next(
            lp
            for lp in mark_resp.json()["snapshot"]["learning"]
            if lp["id"] == "demo-signal-processing"
        )
        aliasing_sub = updated_dsp["topics"][0]["subtopics"][0]
        assert aliasing_sub["id"] == "aliasing"
        assert aliasing_sub["status"] == "done"

        # Verify remote bare repo received the commit
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        log_out = run_git(["log", "origin/main", "-n1", "--oneline"], cwd=laptop_data).stdout
        assert "Mark aliasing done via dashboard" in log_out

    # 7. Server restart restores from snapshot.json before syncing
    restarted_state = ServerState(
        remote_url="",  # Empty remote_url so lifespan only loads snapshot.json from disk
        state_dir=server_state_dir,
        branch="main",
    )
    restarted_app = create_app(state=restarted_state, enable_background_loop=False)
    with TestClient(restarted_app) as client2:
        restored_snap = client2.get("/api/snapshot").json()
        assert len(restored_snap["projects"]) >= 6
        dsp2 = next(lp for lp in restored_snap["learning"] if lp["id"] == "demo-signal-processing")
        assert dsp2["topics"][0]["subtopics"][0]["status"] == "done"


def _make_minimal_remote(tmp_path: Path) -> Path:
    """Create a bare remote whose main branch has a single commit."""
    bare = tmp_path / "remote.git"
    init_bare_repo(bare)
    seed = tmp_path / "seed"
    init_repo(seed)
    (seed / "workbench.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    commit_all(seed, "Seed commit")
    run_git(["remote", "add", "origin", str(bare)], cwd=seed)
    assert push_head_to_branch(seed, branch="main")
    return bare


def test_sync_once_records_git_error_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A git failure while staging the fetched commit is reported on status, not raised."""
    state = ServerState(remote_url=str(_make_minimal_remote(tmp_path)), state_dir=tmp_path / "state")

    def failing_create_worktree(*args: object, **kwargs: object) -> None:
        raise GitError("simulated worktree failure")

    monkeypatch.setattr(sync_module, "create_worktree", failing_create_worktree)

    assert state.sync_once(force=True) is False
    status = state.get_status_dict()
    assert status["last_error"] is not None
    assert "simulated worktree failure" in status["last_error"]["message"]
    assert status["last_checked_at"] is not None


def test_poll_loop_survives_sync_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception from one poll iteration must not stop later polls."""
    state = ServerState(remote_url="unused", state_dir=tmp_path / "state")
    calls: list[int] = []

    def flaky_sync_once(force: bool = False) -> bool:
        calls.append(1)
        if len(calls) == 2:  # first poll after the startup sync
            raise GitError("simulated fetch failure")
        return True

    monkeypatch.setattr(state, "sync_once", flaky_sync_once)
    app = create_app(state=state, poll_seconds=0.05)

    with TestClient(app):
        deadline = time.monotonic() + 3.0
        while len(calls) < 4 and time.monotonic() < deadline:
            time.sleep(0.02)

    assert len(calls) >= 4, f"poll loop stopped after {len(calls)} sync calls"


def test_concurrent_syncs_are_serialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two simultaneous sync_once calls must not run git operations on the same clone at once."""
    state = ServerState(remote_url=str(_make_minimal_remote(tmp_path)), state_dir=tmp_path / "state")
    state.ensure_repo_cloned()

    active = 0
    max_active = 0
    counter_lock = threading.Lock()

    def slow_validate(repo_root: Path) -> ValidationResult:
        nonlocal active, max_active
        with counter_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.3)
        with counter_lock:
            active -= 1
        return ValidationResult()

    monkeypatch.setattr(sync_module, "validate_data_repo", slow_validate)

    results: list[object] = []

    def run_sync() -> None:
        try:
            results.append(state.sync_once(force=True))
        except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
            results.append(exc)

    threads = [threading.Thread(target=run_sync) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results == [True, True]
    assert max_active == 1


WRITE_HEADERS = {"X-Workbench": "1", "Content-Type": "application/json"}

NESTED_PROJECT_YAML = """schema_version: 1
id: demo-nested-rover
name: Nested Rover
description: Rover with nested components for dashboard write tests.
status: active
origin: manual
updated: 2026-10-01
repos: []
links: []
phases:
  - id: build
    name: Build
    status: not_started
    components:
      - id: navigation
        name: Navigation
        status: in_progress
        components:
          - id: ekf
            name: EKF
            status: in_progress
            components:
              - id: ekf-local
                name: Local EKF
                status: not_started
"""


def _find_component_in(nodes: list[dict], comp_id: str) -> dict | None:
    for node in nodes:
        if node.get("id") == comp_id:
            return node
        found = _find_component_in(node.get("components", []) or [], comp_id)
        if found is not None:
            return found
    return None


def _remote_head(laptop_data: Path) -> tuple[str, str]:
    """Return (subject of origin/main HEAD, sha) after fetching from the bare remote."""
    run_git(["fetch", "origin", "main"], cwd=laptop_data)
    subject = run_git(["log", "origin/main", "-n1", "--format=%s"], cwd=laptop_data).stdout.strip()
    sha = run_git(["rev-parse", "origin/main"], cwd=laptop_data).stdout.strip()
    return subject, sha


def _remote_yaml(laptop_data: Path, rel_path: str) -> dict:
    from workbench.yamlio import load_yaml_str_roundtrip, to_plain_python

    text = run_git(["show", f"origin/main:{rel_path}"], cwd=laptop_data).stdout
    return to_plain_python(load_yaml_str_roundtrip(text))


@pytest.fixture
def write_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Demo environment plus a project with nested components pushed to the remote."""
    info = init_demo_environment(tmp_path / "demo_writes")
    laptop_data = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(Path(info["laptop_home"])))

    wt = tmp_path / "wt_seed_nested"
    create_worktree(laptop_data, wt, ref="origin/main")
    proj_dir = wt / "projects" / "demo-nested-rover"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.yaml").write_text(NESTED_PROJECT_YAML, encoding="utf-8")
    commit_all(wt, "Add nested rover project")
    assert push_head_to_branch(wt, branch="main")
    remove_worktree(laptop_data, wt)

    state = ServerState(
        remote_url=str(Path(info["remote_bare"])),
        state_dir=tmp_path / "server_state_writes",
        branch="main",
    )
    app = create_app(state=state, enable_background_loop=False)
    with TestClient(app) as client:
        assert client.get("/api/status").json()["last_error"] is None
        yield client, laptop_data


def test_project_and_learning_status_writes_round_trip(write_env) -> None:
    """Nested component, phase, project, and learning path status edits reach the remote."""
    import datetime

    client, laptop_data = write_env
    today = datetime.date.today().isoformat()
    rover_path = "projects/demo-nested-rover/project.yaml"

    # Nested component (depth 3) status change
    resp = client.post(
        "/api/projects/demo-nested-rover/items/ekf-local/status",
        headers=WRITE_HEADERS,
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"]["last_error"] is None
    rover = next(p for p in body["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert _find_component_in(rover["phases"][0]["components"], "ekf-local")["status"] == "done"
    assert rover["updated"] == today
    subject, sha = _remote_head(laptop_data)
    assert subject == "Mark ekf-local done via dashboard"
    assert body["snapshot"]["commit"] == sha
    remote_rover = _remote_yaml(laptop_data, rover_path)
    assert _find_component_in(remote_rover["phases"][0]["components"], "ekf-local")["status"] == "done"
    # Sibling statuses untouched
    assert _find_component_in(remote_rover["phases"][0]["components"], "ekf")["status"] == "in_progress"

    # Phase status change
    resp = client.post(
        "/api/projects/demo-nested-rover/items/build/status",
        headers=WRITE_HEADERS,
        json={"status": "in_progress"},
    )
    assert resp.status_code == 200, resp.text
    rover = next(p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert rover["phases"][0]["status"] == "in_progress"
    assert _remote_head(laptop_data)[0] == "Mark build in_progress via dashboard"

    # Archive, then unarchive the project
    resp = client.post(
        "/api/projects/demo-nested-rover/status", headers=WRITE_HEADERS, json={"status": "archived"}
    )
    assert resp.status_code == 200, resp.text
    rover = next(p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert rover["status"] == "archived"
    assert _remote_head(laptop_data)[0] == "Set project demo-nested-rover to archived via dashboard"
    assert _remote_yaml(laptop_data, rover_path)["status"] == "archived"

    resp = client.post(
        "/api/projects/demo-nested-rover/status", headers=WRITE_HEADERS, json={"status": "active"}
    )
    assert resp.status_code == 200, resp.text
    rover = next(p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert rover["status"] == "active"
    assert _remote_head(laptop_data)[0] == "Set project demo-nested-rover to active via dashboard"
    # Earlier edits survived the later ones
    remote_rover = _remote_yaml(laptop_data, rover_path)
    assert remote_rover["phases"][0]["status"] == "in_progress"
    assert _find_component_in(remote_rover["phases"][0]["components"], "ekf-local")["status"] == "done"

    # Learning path top-level status
    resp = client.post(
        "/api/learning/demo-signal-processing/status", headers=WRITE_HEADERS, json={"status": "paused"}
    )
    assert resp.status_code == 200, resp.text
    dsp = next(lp for lp in resp.json()["snapshot"]["learning"] if lp["id"] == "demo-signal-processing")
    assert dsp["status"] == "paused"
    assert dsp["updated"] == today
    assert _remote_head(laptop_data)[0] == "Set learning path demo-signal-processing to paused via dashboard"

    # Existing learning item endpoint still works on the shared pipeline
    resp = client.post(
        "/api/learning/demo-signal-processing/items/aliasing/status",
        headers=WRITE_HEADERS,
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    assert _remote_head(laptop_data)[0] == "Mark aliasing done via dashboard"

    # Re-sending the same status is a no-op: 200, and no new commit on the remote
    _, head_before_noop = _remote_head(laptop_data)
    resp = client.post(
        "/api/learning/demo-signal-processing/items/aliasing/status",
        headers=WRITE_HEADERS,
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    assert _remote_head(laptop_data)[1] == head_before_noop


def test_status_write_rejections(write_env) -> None:
    """Header, body, status, id, and not-found errors never reach the remote."""
    client, laptop_data = write_env
    _, head_before = _remote_head(laptop_data)

    write_urls = [
        "/api/projects/demo-nested-rover/status",
        "/api/projects/demo-nested-rover/items/ekf-local/status",
        "/api/learning/demo-signal-processing/status",
        "/api/learning/demo-signal-processing/items/aliasing/status",
    ]
    for url in write_urls:
        # 403 without X-Workbench
        assert client.post(url, json={"status": "done"}).status_code == 403, url
        # 400 without a status field
        assert client.post(url, headers=WRITE_HEADERS, json={}).status_code == 400, url
        # 400 for a status outside the allowed set
        resp = client.post(url, headers=WRITE_HEADERS, json={"status": "finished"})
        assert resp.status_code == 400, url
        assert "Invalid status" in resp.json()["detail"]

    # Status sets differ per level: projects take ProjectStatus, items take PhaseStatus
    assert client.post(
        "/api/projects/demo-nested-rover/status", headers=WRITE_HEADERS, json={"status": "parked"}
    ).status_code == 400
    assert client.post(
        "/api/projects/demo-nested-rover/items/ekf-local/status",
        headers=WRITE_HEADERS,
        json={"status": "archived"},
    ).status_code == 400
    assert client.post(
        "/api/learning/demo-signal-processing/items/aliasing/status",
        headers=WRITE_HEADERS,
        json={"status": "parked"},
    ).status_code == 400

    # 400 for ids that are not slugs
    for url in [
        "/api/projects/Bad_ID/status",
        "/api/projects/demo-nested-rover/items/EKF..local/status",
        "/api/learning/bad--id/status",
        "/api/learning/demo-signal-processing/items/-x/status",
    ]:
        resp = client.post(url, headers=WRITE_HEADERS, json={"status": "done"})
        assert resp.status_code == 400, url
        assert "Invalid" in resp.json()["detail"]

    # 404 for unknown projects, paths, and items
    for url in [
        "/api/projects/no-such-project/status",
        "/api/projects/no-such-project/items/ekf-local/status",
        "/api/projects/demo-nested-rover/items/no-such-item/status",
        "/api/learning/no-such-path/status",
        "/api/learning/demo-signal-processing/items/no-such-item/status",
    ]:
        status = "active" if url.endswith("-project/status") or url.endswith("-path/status") else "done"
        resp = client.post(url, headers=WRITE_HEADERS, json={"status": status})
        assert resp.status_code == 404, f"{url}: {resp.text}"

    # Nothing was pushed by any rejected request
    assert _remote_head(laptop_data)[1] == head_before
