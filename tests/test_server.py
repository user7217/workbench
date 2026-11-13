"""Tests for FastAPI server sync, invalid push resilience, snapshot restore, non-main branch ignore, X-Workbench protection, and mark-done roundtrip (§14)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from workbench.demo import init_demo_environment
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
        assert "edit(learning/demo-signal-processing/aliasing): status done" in log_out

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
    state = ServerState(
        remote_url=str(_make_minimal_remote(tmp_path)), state_dir=tmp_path / "state"
    )

    def failing_create_worktree(*args: object, **kwargs: object) -> None:
        raise GitError("simulated worktree failure")

    monkeypatch.setattr(sync_module, "create_worktree", failing_create_worktree)

    assert state.sync_once(force=True) is False
    status = state.get_status_dict()
    assert status["last_error"] is not None
    assert "simulated worktree failure" in status["last_error"]["message"]
    assert status["last_checked_at"] is not None


def test_poll_loop_survives_sync_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_concurrent_syncs_are_serialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two simultaneous sync_once calls must not run git operations on the same clone at once."""
    state = ServerState(
        remote_url=str(_make_minimal_remote(tmp_path)), state_dir=tmp_path / "state"
    )
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
  - id: testing
    name: Testing
    status: not_started
    components:
      - id: field-trials
        name: Field Trials
        status: not_started
        depends_on: [ekf-local]
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

    run_git(["fetch", "origin", "main"], cwd=laptop_data)
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
    # ekf-local is the only part of ekf, ekf of navigation, navigation of build: all follow it
    assert subject == (
        "edit(demo-nested-rover/ekf-local): status done; ekf done, navigation done, build done"
    )
    assert body["snapshot"]["commit"] == sha
    remote_rover = _remote_yaml(laptop_data, rover_path)
    remote_build = remote_rover["phases"][0]
    for comp_id in ("ekf-local", "ekf", "navigation"):
        assert _find_component_in(remote_build["components"], comp_id)["status"] == "done"
    assert remote_build["status"] == "done"
    # The other phase is not an ancestor and is left alone
    assert remote_rover["phases"][1]["status"] == "not_started"

    # Phase status change
    resp = client.post(
        "/api/projects/demo-nested-rover/items/build/status",
        headers=WRITE_HEADERS,
        json={"status": "in_progress"},
    )
    assert resp.status_code == 200, resp.text
    rover = next(p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert rover["phases"][0]["status"] == "in_progress"
    assert _remote_head(laptop_data)[0] == "edit(demo-nested-rover/build): status in_progress"

    # Archive, then unarchive the project
    resp = client.post(
        "/api/projects/demo-nested-rover/status", headers=WRITE_HEADERS, json={"status": "archived"}
    )
    assert resp.status_code == 200, resp.text
    rover = next(p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert rover["status"] == "archived"
    assert _remote_head(laptop_data)[0] == "edit(demo-nested-rover): status archived"
    assert _remote_yaml(laptop_data, rover_path)["status"] == "archived"

    resp = client.post(
        "/api/projects/demo-nested-rover/status", headers=WRITE_HEADERS, json={"status": "active"}
    )
    assert resp.status_code == 200, resp.text
    rover = next(p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-nested-rover")
    assert rover["status"] == "active"
    assert _remote_head(laptop_data)[0] == "edit(demo-nested-rover): status active"
    # Earlier edits survived the later ones
    remote_rover = _remote_yaml(laptop_data, rover_path)
    assert remote_rover["phases"][0]["status"] == "in_progress"
    assert (
        _find_component_in(remote_rover["phases"][0]["components"], "ekf-local")["status"] == "done"
    )

    # Learning path top-level status
    resp = client.post(
        "/api/learning/demo-signal-processing/status",
        headers=WRITE_HEADERS,
        json={"status": "paused"},
    )
    assert resp.status_code == 200, resp.text
    dsp = next(
        lp for lp in resp.json()["snapshot"]["learning"] if lp["id"] == "demo-signal-processing"
    )
    assert dsp["status"] == "paused"
    assert dsp["updated"] == today
    assert _remote_head(laptop_data)[0] == "edit(learning/demo-signal-processing): status paused"

    # Existing learning item endpoint still works on the shared pipeline
    resp = client.post(
        "/api/learning/demo-signal-processing/items/aliasing/status",
        headers=WRITE_HEADERS,
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    assert (
        _remote_head(laptop_data)[0]
        == "edit(learning/demo-signal-processing/aliasing): status done"
    )

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
    assert (
        client.post(
            "/api/projects/demo-nested-rover/status",
            headers=WRITE_HEADERS,
            json={"status": "parked"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/projects/demo-nested-rover/items/ekf-local/status",
            headers=WRITE_HEADERS,
            json={"status": "archived"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/learning/demo-signal-processing/items/aliasing/status",
            headers=WRITE_HEADERS,
            json={"status": "parked"},
        ).status_code
        == 400
    )

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
        status = (
            "active" if url.endswith("-project/status") or url.endswith("-path/status") else "done"
        )
        resp = client.post(url, headers=WRITE_HEADERS, json={"status": status})
        assert resp.status_code == 404, f"{url}: {resp.text}"

    # Nothing was pushed by any rejected request
    assert _remote_head(laptop_data)[1] == head_before


ROVER = "projects/demo-nested-rover/project.yaml"


def _rover(body: dict) -> dict:
    return next(p for p in body["snapshot"]["projects"] if p["id"] == "demo-nested-rover")


def _post(client, url: str, body: dict, headers: dict | None = WRITE_HEADERS):
    return client.post(url, headers=headers, json=body) if headers else client.post(url, json=body)


def test_text_field_edits_round_trip(write_env) -> None:
    """Project, item (any depth), learning path and learning item text edits reach the remote."""
    client, laptop_data = write_env

    # Project name/description
    resp = _post(
        client,
        "/api/projects/demo-nested-rover/fields",
        {"name": "Rover Mk2", "description": "Second rover build."},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["status"]["last_error"] is None
    assert (_rover(body)["name"], _rover(body)["description"]) == (
        "Rover Mk2",
        "Second rover build.",
    )
    subject, sha = _remote_head(laptop_data)
    assert subject == "edit(demo-nested-rover): name, description"
    assert body["snapshot"]["commit"] == sha
    assert _remote_yaml(laptop_data, ROVER)["name"] == "Rover Mk2"

    # Nested component: summary, multi-line notes, checklist items
    resp = _post(
        client,
        "/api/projects/demo-nested-rover/items/ekf-local/fields",
        {
            "summary": "Wheel odometry + IMU",
            "notes": "line one\nline two",
            "items": ["tune Q", "tune R"],
        },
    )
    assert resp.status_code == 200, resp.text
    comp = _find_component_in(_rover(resp.json())["phases"][0]["components"], "ekf-local")
    assert (comp["summary"], comp["notes"], comp["items"]) == (
        "Wheel odometry + IMU",
        "line one\nline two",
        ["tune Q", "tune R"],
    )
    assert (
        _remote_head(laptop_data)[0] == "edit(demo-nested-rover/ekf-local): summary, notes, items"
    )
    remote = _remote_yaml(laptop_data, ROVER)
    assert (
        _find_component_in(remote["phases"][0]["components"], "ekf-local")["notes"]
        == "line one\nline two"
    )

    # A new key lands in model order (before nested components), and groups take text fields
    resp = _post(
        client, "/api/projects/demo-nested-rover/items/navigation/fields", {"summary": "Nav stack"}
    )
    assert resp.status_code == 200, resp.text
    nav = _find_component_in(
        _remote_yaml(laptop_data, ROVER)["phases"][0]["components"], "navigation"
    )
    assert list(nav).index("summary") < list(nav).index("components")
    resp = _post(
        client, "/api/projects/demo-nested-rover/items/build/fields", {"description": "Build phase"}
    )
    assert resp.status_code == 200, resp.text
    assert _remote_yaml(laptop_data, ROVER)["phases"][0]["description"] == "Build phase"

    # Empty string clears: the key is removed, even when that is most of the file's text
    resp = _post(client, "/api/projects/demo-nested-rover/fields", {"description": ""})
    assert resp.status_code == 200, resp.text
    assert _rover(resp.json()).get("description", "") == ""  # snapshot omits defaults
    assert "description" not in _remote_yaml(laptop_data, ROVER)
    resp = _post(
        client, "/api/projects/demo-nested-rover/items/ekf-local/fields", {"items": [], "notes": ""}
    )
    assert resp.status_code == 200, resp.text
    comp = _find_component_in(
        _remote_yaml(laptop_data, ROVER)["phases"][0]["components"], "ekf-local"
    )
    assert "items" not in comp and "notes" not in comp

    # Learning path and learning items (topic and subtopic)
    resp = _post(
        client, "/api/learning/demo-signal-processing/fields", {"description": "DSP refresher"}
    )
    assert resp.status_code == 200, resp.text
    dsp = next(
        lp for lp in resp.json()["snapshot"]["learning"] if lp["id"] == "demo-signal-processing"
    )
    assert dsp["description"] == "DSP refresher"
    assert _remote_head(laptop_data)[0] == "edit(learning/demo-signal-processing): description"
    resp = _post(
        client,
        "/api/learning/demo-signal-processing/items/aliasing/fields",
        {"name": "Aliasing", "notes": "See Lyons ch. 2"},
    )
    assert resp.status_code == 200, resp.text
    assert (
        _remote_head(laptop_data)[0]
        == "edit(learning/demo-signal-processing/aliasing): name, notes"
    )
    resp = _post(
        client, "/api/learning/demo-signal-processing/items/sampling/fields", {"summary": ""}
    )
    assert resp.status_code == 200, resp.text
    lp = _remote_yaml(laptop_data, "learning/demo-signal-processing/path.yaml")
    sampling = lp["topics"][0]
    assert "summary" not in sampling
    assert sampling["subtopics"][0]["name"] == "Aliasing"
    assert sampling["subtopics"][0]["notes"] == "See Lyons ch. 2"


def test_text_field_rejections(write_env) -> None:
    """Disallowed keys, bad types, too-long text, unknown targets and missing headers push nothing."""
    client, laptop_data = write_env
    _, head = _remote_head(laptop_data)
    urls = {
        "/api/projects/demo-nested-rover/fields": "description",
        "/api/projects/demo-nested-rover/items/ekf-local/fields": "summary",
        "/api/learning/demo-signal-processing/fields": "description",
        "/api/learning/demo-signal-processing/items/aliasing/fields": "summary",
    }
    for url, key in urls.items():
        assert _post(client, url, {key: "x"}, headers=None).status_code == 403, url
        for bad in ({"status": "done"}, {"id": "new-id"}, {key: "x", "updated": "2020-01-01"}):
            resp = _post(client, url, bad)
            assert resp.status_code == 400, f"{url} {bad}"
            assert "Unsupported field" in resp.json()["detail"]
        assert _post(client, url, {}).status_code == 400, url
        assert _post(client, url, {key: 5}).status_code == 400, url
        assert client.post(url, headers=WRITE_HEADERS, content="[1]").status_code == 400, url
    # items only on components, and must be a list of strings
    assert (
        _post(
            client, "/api/learning/demo-signal-processing/items/aliasing/fields", {"items": ["a"]}
        ).status_code
        == 400
    )
    assert (
        _post(
            client, "/api/projects/demo-nested-rover/items/build/fields", {"items": ["a"]}
        ).status_code
        == 400
    )
    assert (
        _post(
            client, "/api/projects/demo-nested-rover/items/ekf/fields", {"items": "a"}
        ).status_code
        == 400
    )
    # Length limits come from model validation -> 422; an empty name is invalid too
    resp = _post(client, "/api/projects/demo-nested-rover/items/ekf/fields", {"summary": "s" * 301})
    assert resp.status_code == 422, resp.text
    assert _post(client, "/api/projects/demo-nested-rover/fields", {"name": ""}).status_code == 422
    # 404s and bad ids
    for url in [
        "/api/projects/no-such/fields",
        "/api/projects/demo-nested-rover/items/nope/fields",
        "/api/learning/no-such/fields",
        "/api/learning/demo-signal-processing/items/nope/fields",
    ]:
        assert _post(client, url, {"name": "X"}).status_code == 404, url
    assert _post(client, "/api/projects/Bad_Id/fields", {"name": "X"}).status_code == 400
    assert _remote_head(laptop_data)[1] == head


def test_structure_add_move_delete(write_env) -> None:
    """Add groups/components (nested, id suffixes), reorder/move, and delete with reference checks."""
    client, laptop_data = write_env
    base = "/api/projects/demo-nested-rover"

    # New phase at a position
    resp = _post(client, f"{base}/items", {"parent_id": None, "name": "Integration", "position": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_id"] == "integration"
    assert [p["id"] for p in _rover(resp.json())["phases"]] == ["build", "integration", "testing"]
    assert _remote_head(laptop_data)[0] == "edit(demo-nested-rover): add phase integration"

    # Component in a group; the slug collides with an existing id -> -2, then -3 when nested deeper
    resp = _post(
        client,
        f"{base}/items",
        {
            "parent_id": "build",
            "name": "Navigation",
            "status": "in_progress",
            "summary": "Second nav",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_id"] == "navigation-2"
    assert (
        _remote_head(laptop_data)[0] == "edit(demo-nested-rover/build): add component navigation-2"
    )
    build = _remote_yaml(laptop_data, ROVER)["phases"][0]
    assert build["components"][-1] == {
        "id": "navigation-2",
        "name": "Navigation",
        "status": "in_progress",
        "summary": "Second nav",
    }
    resp = _post(
        client, f"{base}/items", {"parent_id": "ekf-local", "name": "Navigation", "id": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_id"] == "navigation-3"
    ekf_local = _find_component_in(
        _remote_yaml(laptop_data, ROVER)["phases"][0]["components"], "ekf-local"
    )
    assert [c["id"] for c in ekf_local["components"]] == ["navigation-3"]

    # Topic group on a project that does not use phases
    resp = _post(
        client, "/api/projects/demo-fpga-logic-analyzer/items", {"parent_id": None, "name": "Docs!"}
    )
    assert resp.status_code == 200, resp.text
    fpga = _remote_yaml(laptop_data, "projects/demo-fpga-logic-analyzer/project.yaml")
    assert fpga["topics"][-1]["id"] == "docs"
    assert _remote_head(laptop_data)[0] == "edit(demo-fpga-logic-analyzer): add topic docs"

    # Add rejections push nothing
    _, head = _remote_head(laptop_data)
    assert (
        _post(client, f"{base}/items", {"parent_id": "build", "name": "X", "id": "ekf"}).status_code
        == 422
    )
    assert _post(client, f"{base}/items", {"parent_id": "nope", "name": "X"}).status_code == 404
    assert _post(client, f"{base}/items", {"parent_id": None, "name": "  "}).status_code == 400
    assert (
        _post(
            client, f"{base}/items", {"parent_id": None, "name": "X", "status": "archived"}
        ).status_code
        == 400
    )
    assert (
        _post(client, f"{base}/items", {"parent_id": None, "name": "X", "position": 9}).status_code
        == 400
    )
    assert (
        _post(
            client, f"{base}/items", {"parent_id": None, "name": "X", "colour": "red"}
        ).status_code
        == 400
    )
    assert (
        _post(client, f"{base}/items", {"parent_id": None, "name": "X"}, headers=None).status_code
        == 403
    )
    assert (
        _post(client, "/api/projects/no-such/items", {"parent_id": None, "name": "X"}).status_code
        == 404
    )
    assert _remote_head(laptop_data)[1] == head

    # Reorder groups
    resp = _post(client, f"{base}/items/testing/move", {"position": 0})
    assert resp.status_code == 200, resp.text
    assert [p["id"] for p in _rover(resp.json())["phases"]] == ["testing", "build", "integration"]
    assert _remote_head(laptop_data)[0] == "edit(demo-nested-rover/testing): move to position 0"

    # Move a nested component (with its own child) to the front of another group; ids preserved
    resp = _post(
        client, f"{base}/items/ekf-local/move", {"parent_id": "integration", "position": 0}
    )
    assert resp.status_code == 200, resp.text
    assert (
        _remote_head(laptop_data)[0] == "edit(demo-nested-rover/ekf-local): move under integration"
    )
    remote = _remote_yaml(laptop_data, ROVER)
    integration = next(p for p in remote["phases"] if p["id"] == "integration")
    assert [c["id"] for c in integration["components"]] == ["ekf-local"]
    assert integration["components"][0]["components"][0]["id"] == "navigation-3"
    ekf = _find_component_in(
        next(p for p in remote["phases"] if p["id"] == "build")["components"], "ekf"
    )
    assert "components" not in ekf  # emptied list is dropped

    # Reorder within the same list (parent_id omitted)
    resp = _post(client, f"{base}/items/navigation-2/move", {"position": 0})
    assert resp.status_code == 200, resp.text
    build = next(p for p in _remote_yaml(laptop_data, ROVER)["phases"] if p["id"] == "build")
    assert [c["id"] for c in build["components"]] == ["navigation-2", "navigation"]

    # Move rejections
    _, head = _remote_head(laptop_data)
    assert (
        _post(
            client, f"{base}/items/navigation/move", {"parent_id": "ekf", "position": 0}
        ).status_code
        == 400
    )
    assert (
        _post(
            client, f"{base}/items/navigation/move", {"parent_id": "navigation", "position": 0}
        ).status_code
        == 400
    )
    assert (
        _post(
            client, f"{base}/items/build/move", {"parent_id": "testing", "position": 0}
        ).status_code
        == 400
    )
    assert (
        _post(
            client, f"{base}/items/navigation/move", {"parent_id": None, "position": 0}
        ).status_code
        == 400
    )
    assert (
        _post(
            client, f"{base}/items/navigation/move", {"parent_id": "nope", "position": 0}
        ).status_code
        == 404
    )
    assert _post(client, f"{base}/items/nope/move", {"position": 0}).status_code == 404
    assert _post(client, f"{base}/items/navigation/move", {"position": 2}).status_code == 400
    assert _post(client, f"{base}/items/navigation/move", {"position": -1}).status_code == 400
    assert _post(client, f"{base}/items/navigation/move", {}).status_code == 400
    assert (
        _post(client, f"{base}/items/navigation/move", {"position": 0}, headers=None).status_code
        == 403
    )
    assert _remote_head(laptop_data)[1] == head

    # Delete needs confirm: true; a depends_on reference blocks it and is named
    assert _post(client, f"{base}/items/ekf-local/delete", {}).status_code == 400
    assert _post(client, f"{base}/items/ekf-local/delete", {"confirm": "yes"}).status_code == 400
    resp = _post(client, f"{base}/items/ekf-local/delete", {"confirm": True})
    assert resp.status_code == 422, resp.text
    assert "field-trials" in resp.json()["detail"]
    resp = _post(client, f"{base}/items/integration/delete", {"confirm": True})
    assert resp.status_code == 422, resp.text  # ekf-local sits inside integration now
    assert _post(client, f"{base}/items/nope/delete", {"confirm": True}).status_code == 404
    assert (
        _post(
            client, f"{base}/items/navigation/delete", {"confirm": True}, headers=None
        ).status_code
        == 403
    )
    assert _remote_head(laptop_data)[1] == head

    # Deleting a subtree removes nested ids; deleting a group works too
    resp = _post(client, f"{base}/items/navigation/delete", {"confirm": True})
    assert resp.status_code == 200, resp.text
    assert _remote_head(laptop_data)[0].startswith("edit(demo-nested-rover/navigation): delete")
    rover = _rover(resp.json())
    build = next(p for p in rover["phases"] if p["id"] == "build")
    assert [c["id"] for c in build["components"]] == ["navigation-2"]
    resp = _post(client, f"{base}/items/testing/delete", {"confirm": True})
    assert resp.status_code == 200, resp.text
    resp = _post(client, f"{base}/items/integration/delete", {"confirm": True})
    assert resp.status_code == 200, resp.text
    remote = _remote_yaml(laptop_data, ROVER)
    assert [p["id"] for p in remote["phases"]] == ["build"]

    run_git(["reset", "--hard", "origin/main"], cwd=laptop_data)
    from workbench.validate import validate_data_repo

    assert validate_data_repo(laptop_data).ok


def test_parent_link_attach_clear_and_delete_guard(write_env) -> None:
    """Attach/clear child links; cycles and bad targets are refused; deleting a child's anchor is blocked."""
    client, laptop_data = write_env
    url = "/api/projects/demo-weather-station/parent"

    resp = _post(client, url, {"parent": "demo-nested-rover", "under": "testing"})
    assert resp.status_code == 200, resp.text
    assert _remote_head(laptop_data)[0] == (
        "edit(demo-weather-station): attach under demo-nested-rover/testing"
    )
    ws_snap = next(
        p for p in resp.json()["snapshot"]["projects"] if p["id"] == "demo-weather-station"
    )
    assert (ws_snap["parent"], ws_snap["under"]) == ("demo-nested-rover", "testing")
    ws = _remote_yaml(laptop_data, "projects/demo-weather-station/project.yaml")
    assert list(ws).index("parent") == list(ws).index("status") + 1

    _, head = _remote_head(laptop_data)
    # Deleting the item a child project sits under is refused
    resp = _post(client, "/api/projects/demo-nested-rover/items/testing/delete", {"confirm": True})
    assert resp.status_code == 422, resp.text
    assert "demo-weather-station" in resp.json()["detail"]
    # Cycle, self, half-set, missing keys, unknown parent/under
    resp = _post(
        client,
        "/api/projects/demo-nested-rover/parent",
        {"parent": "demo-weather-station", "under": "hardware"},
    )
    assert resp.status_code == 422, resp.text
    assert "cycle" in resp.json()["detail"]
    assert (
        _post(client, url, {"parent": "demo-weather-station", "under": "hardware"}).status_code
        == 400
    )
    assert _post(client, url, {"parent": "demo-robot-arm", "under": None}).status_code == 400
    assert _post(client, url, {"parent": None}).status_code == 400
    assert _post(client, url, {"parent": "Bad_Id", "under": "x"}).status_code == 400
    resp = _post(client, url, {"parent": "no-such-project", "under": "x"})
    assert resp.status_code == 422 and "no-such-project" in resp.json()["detail"]
    resp = _post(client, url, {"parent": "demo-robot-arm", "under": "nowhere"})
    assert resp.status_code == 422 and "nowhere" in resp.json()["detail"]
    assert (
        _post(client, "/api/projects/no-such/parent", {"parent": None, "under": None}).status_code
        == 404
    )
    assert _post(client, url, {"parent": None, "under": None}, headers=None).status_code == 403
    assert _remote_head(laptop_data)[1] == head

    # Clearing removes both keys
    resp = _post(client, url, {"parent": None, "under": None})
    assert resp.status_code == 200, resp.text
    assert (
        _remote_head(laptop_data)[0] == "edit(demo-weather-station): detach from demo-nested-rover"
    )
    ws = _remote_yaml(laptop_data, "projects/demo-weather-station/project.yaml")
    assert "parent" not in ws and "under" not in ws


def test_snapshot_reports_when_each_entry_last_changed(write_env) -> None:
    """The snapshot carries git change times so the UI can order cards by recent activity."""
    client, _laptop_data = write_env
    before = client.get("/api/snapshot").json()["updated_at"]
    assert before["projects"] and before["learning"]
    assert all(isinstance(v, str) and "T" in v for v in before["projects"].values())

    time.sleep(1.1)  # git commit times have one-second resolution
    resp = client.post(
        "/api/projects/demo-budget-cli/status",
        headers={"X-Workbench": "1", "Content-Type": "application/json"},
        json={"status": "paused"},
    )
    assert resp.status_code == 200, resp.text
    after = resp.json()["snapshot"]["updated_at"]["projects"]
    newest = max(after, key=after.get)
    assert newest == "demo-budget-cli"
    assert after["demo-budget-cli"] > before["projects"]["demo-budget-cli"]


INBOX = "projects/demo-inbox/project.yaml"
INBOX_PROJECT_YAML = """schema_version: 1
id: demo-inbox
name: Inbox
status: active
origin: manual
updated: 2026-10-01
phases:
  - id: build
    name: Build
    status: in_progress
suggestions:
  - id: add-telemetry
    name: Add telemetry
    summary: Stream battery and motor data
    under: build
    added: 2026-10-20
  - id: stale-idea
    name: Stale idea
    summary: >-
      A long suggestion summary that holds far more text than the rest of this small project,
      so dropping it would trip the 30% mass-deletion guard if suggestions were counted.
    under: removed-phase
    added: 2026-10-21
  - id: field-testing
    name: Field testing
    added: 2026-10-22
"""


def _push_project(laptop_data: Path, tmp_path: Path, project_id: str, text: str) -> None:
    wt = tmp_path / f"wt_seed_{project_id}"
    create_worktree(laptop_data, wt, ref="origin/main")
    proj_dir = wt / "projects" / project_id
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.yaml").write_text(text, encoding="utf-8")
    commit_all(wt, f"Add {project_id}")
    assert push_head_to_branch(wt, branch="main")
    remove_worktree(laptop_data, wt)


def test_suggestions_keep_and_dismiss(write_env, tmp_path: Path) -> None:
    """Keep moves a suggestion into the tree; dismiss drops it; stale/unknown ids push nothing."""
    client, laptop_data = write_env
    _push_project(laptop_data, tmp_path, "demo-inbox", INBOX_PROJECT_YAML)
    assert client.post("/api/sync", headers=WRITE_HEADERS).json()["ok"] is True
    base = "/api/projects/demo-inbox/suggestions"

    # Keep under an existing phase: a not-started component reusing the suggestion id
    resp = _post(client, f"{base}/add-telemetry/keep", {})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["item_id"] == "add-telemetry"
    inbox = next(p for p in body["snapshot"]["projects"] if p["id"] == "demo-inbox")
    assert [s["id"] for s in inbox["suggestions"]] == ["stale-idea", "field-testing"]
    assert _remote_head(laptop_data)[0] == "edit(demo-inbox): keep suggestion add-telemetry"
    remote = _remote_yaml(laptop_data, INBOX)
    assert remote["phases"][0]["components"] == [
        {
            "id": "add-telemetry",
            "name": "Add telemetry",
            "status": "not_started",
            "summary": "Stream battery and motor data",
        }
    ]

    # Rejections push nothing: stale `under`, unknown ids, bad body, missing header
    _, head = _remote_head(laptop_data)
    resp = _post(client, f"{base}/stale-idea/keep", {})
    assert resp.status_code == 422 and "removed-phase" in resp.json()["detail"]
    assert _post(client, f"{base}/no-such/keep", {}).status_code == 404
    assert _post(client, f"{base}/no-such/dismiss", {}).status_code == 404
    assert _post(client, f"{base}/add-telemetry/dismiss", {}).status_code == 404
    assert _post(client, "/api/projects/no-such/suggestions/x/dismiss", {}).status_code == 404
    assert _post(client, f"{base}/stale-idea/dismiss", {"confirm": True}).status_code == 400
    assert _post(client, f"{base}/stale-idea/dismiss", {}, headers=None).status_code == 403
    assert _remote_head(laptop_data)[1] == head
    assert [s["id"] for s in _remote_yaml(laptop_data, INBOX)["suggestions"]] == [
        "stale-idea",
        "field-testing",
    ]

    # Dismiss: the long summary disappears without tripping the mass-deletion guard
    resp = _post(client, f"{base}/stale-idea/dismiss", {})
    assert resp.status_code == 200, resp.text
    assert _remote_head(laptop_data)[0] == "edit(demo-inbox): dismiss suggestion stale-idea"
    assert [s["id"] for s in _remote_yaml(laptop_data, INBOX)["suggestions"]] == ["field-testing"]

    # Keep without `under`: a new phase; the emptied inbox drops the key entirely
    resp = _post(client, f"{base}/field-testing/keep", {})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_id"] == "field-testing"
    remote = _remote_yaml(laptop_data, INBOX)
    assert [p["id"] for p in remote["phases"]] == ["build", "field-testing"]
    assert remote["phases"][1]["status"] == "not_started"
    assert "suggestions" not in remote


def test_web_ui_location_comes_from_workbench_web_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An installed package (the Docker image) cannot find web/dist next to its source, so the
    server takes the UI location from WORKBENCH_WEB_DIST, and says so when there is no UI."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>workbench ui</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    state = ServerState(remote_url="", state_dir=tmp_path / "state")

    monkeypatch.setenv("WORKBENCH_WEB_DIST", str(dist))
    client = TestClient(create_app(state=state, enable_background_loop=False))
    assert client.get("/").text == "<html>workbench ui</html>"
    assert client.get("/projects/some-id").text == "<html>workbench ui</html>"  # SPA route
    assert client.get("/assets/app.js").text == "console.log(1)"

    monkeypatch.setenv("WORKBENCH_WEB_DIST", str(tmp_path / "missing"))
    with caplog.at_level("WARNING"):
        client = TestClient(create_app(state=state, enable_background_loop=False))
    assert client.get("/").status_code == 404
    assert "Web UI not found" in caplog.text


def test_web_ui_lookup_order_setting_then_package_then_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WORKBENCH_WEB_DIST wins; else the UI shipped inside the wheel; else the checkout's web/dist."""
    from workbench.server import app as app_module

    def ui(name: str) -> Path:
        d = tmp_path / name
        d.mkdir()
        (d / "index.html").write_text(name, encoding="utf-8")
        return d

    packaged, checkout, configured = ui("packaged"), ui("checkout"), ui("configured")
    monkeypatch.setattr(app_module, "DEFAULT_WEB_DIST", checkout)
    monkeypatch.setattr(app_module, "PACKAGED_WEB_DIST", tmp_path / "not-installed")
    monkeypatch.delenv("WORKBENCH_WEB_DIST", raising=False)
    assert app_module._default_web_dist() == checkout
    monkeypatch.setattr(app_module, "PACKAGED_WEB_DIST", packaged)
    assert app_module._default_web_dist() == packaged
    monkeypatch.setenv("WORKBENCH_WEB_DIST", str(configured))
    assert app_module._default_web_dist() == configured


def test_serve_command_uses_laptop_config_and_its_own_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn
    from typer.testing import CliRunner

    from workbench.cli import app as cli_app
    from workbench.config import WorkbenchLocalConfig, save_local_config

    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for var in ("WORKBENCH_DATA_REMOTE", "WORKBENCH_BRANCH", "WORKBENCH_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)
    runner = CliRunner()

    # No config and no env: a clear error, nothing started
    res = runner.invoke(cli_app, ["serve"])
    assert res.exit_code == 1 and "no data repo" in res.output

    save_local_config(
        WorkbenchLocalConfig(data_repo_url="git@example.com:me/data.git", branch="trunk"),
        home_dir=home,
    )
    started: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app, **kw))
    res = runner.invoke(cli_app, ["serve", "--port", "8499"])
    assert res.exit_code == 0, res.output
    assert (started["host"], started["port"]) == ("127.0.0.1", 8499)
    import os

    assert os.environ["WORKBENCH_DATA_REMOTE"] == "git@example.com:me/data.git"
    assert os.environ["WORKBENCH_BRANCH"] == "trunk"
    assert os.environ["WORKBENCH_STATE_DIR"] == str(home / ".workbench" / "server")


def _rover_item_status(laptop_data: Path, item_id: str) -> str:
    rover = _remote_yaml(laptop_data, "projects/demo-nested-rover/project.yaml")
    for phase in rover["phases"]:
        if phase["id"] == item_id:
            return phase["status"]
        found = _find_component_in(phase.get("components", []) or [], item_id)
        if found is not None:
            return found["status"]
    raise AssertionError(f"{item_id} not found")


def test_deleting_the_last_open_part_finishes_its_parents(write_env) -> None:
    """A delete rolls the statuses of the removed part's parents up (edits.roll_up_project_from)."""
    client, laptop_data = write_env
    resp = client.post(
        "/api/projects/demo-nested-rover/items",
        headers=WRITE_HEADERS,
        json={"parent_id": "ekf", "name": "Wiring"},
    )
    assert resp.status_code == 200, resp.text
    new_id = resp.json()["item_id"]
    resp = client.post(
        "/api/projects/demo-nested-rover/items/ekf-local/status",
        headers=WRITE_HEADERS,
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    assert _rover_item_status(laptop_data, "ekf") == "in_progress"  # Wiring is still open

    resp = client.post(
        f"/api/projects/demo-nested-rover/items/{new_id}/delete",
        headers=WRITE_HEADERS,
        json={"confirm": True},
    )
    assert resp.status_code == 200, resp.text
    for item_id in ("ekf", "navigation", "build"):
        assert _rover_item_status(laptop_data, item_id) == "done"


def test_moving_parts_updates_both_parents(write_env) -> None:
    """The part's new parent and the parent it left both follow their parts."""
    client, laptop_data = write_env
    resp = client.post(
        "/api/projects/demo-nested-rover/items/field-trials/status",
        headers=WRITE_HEADERS,
        json={"status": "done"},
    )
    assert resp.status_code == 200, resp.text
    assert _rover_item_status(laptop_data, "testing") == "done"

    # ekf-local (not started) moves from ekf into the done 'testing' phase
    resp = client.post(
        "/api/projects/demo-nested-rover/items/ekf-local/move",
        headers=WRITE_HEADERS,
        json={"parent_id": "testing", "position": 0},
    )
    assert resp.status_code == 200, resp.text
    assert _rover_item_status(laptop_data, "testing") == "in_progress"
    # ekf lost its only part: no parts left, so its status stays as it was
    assert _rover_item_status(laptop_data, "ekf") == "in_progress"
