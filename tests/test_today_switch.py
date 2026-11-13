"""Tests for the Today's Work on/off switch: `enabled` in today.yaml, POST /api/today/enabled,
POST /api/today refused while off, and the removal of the old per-server switches (config key,
setup question, `workbench today`, WORKBENCH_TODAY, the snapshot's `features`)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from workbench.cli import app as cli_app
from workbench.cli import setup as wizard
from workbench.config import WorkbenchLocalConfig, load_local_config, save_local_config
from workbench.gitops import commit_all, init_bare_repo, init_repo, push_head_to_branch, run_git
from workbench.models import Today
from workbench.server.app import create_app
from workbench.server.sync import ServerState
from workbench.yamlio import load_yaml_str_roundtrip, to_plain_python

runner = CliRunner()
WRITE_HEADERS = {"X-Workbench": "1", "Content-Type": "application/json"}
OFF_DETAIL = "Today's Work is turned off"
ENTRY = {"kind": "project", "id": "robot-arm", "item": "kinematics", "added": "2026-11-09"}
STORED_TODAY = {"enabled": True, "day": "2026-11-10", "entries": [ENTRY]}

PROJECT_YAML = """schema_version: 1
id: robot-arm
name: Robot Arm
description: A desktop manipulator with six joints, built to learn kinematics, planning and closed-loop control.
status: active
origin: manual
updated: 2026-11-01
phases:
  - id: kinematics
    name: Kinematics
    status: in_progress
"""

TODAY_YAML = """schema_version: 1
# chosen in the morning review
day: 2026-11-10
entries:
  - kind: project
    id: robot-arm
    item: kinematics
    added: 2026-11-09
"""


def _config_file(home: Path) -> Path:
    return home / ".workbench" / "config.toml"


# ---------------------------------------------------------------------------
# The old per-server switches are gone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", ['today = "off"', 'today = "on"', "today = false", 'today = "x"'])
def test_config_with_a_leftover_today_key_loads_and_drops_it_on_save(
    tmp_path: Path, line: str
) -> None:
    home = tmp_path / "home"
    _config_file(home).parent.mkdir(parents=True)
    _config_file(home).write_text(
        f'data_repo_url = "git@example.com:me/data.git"\nbranch = "trunk"\n{line}\n',
        encoding="utf-8",
    )
    cfg = load_local_config(home_dir=home)
    assert (cfg.data_repo_url, cfg.branch) == ("git@example.com:me/data.git", "trunk")
    assert not hasattr(cfg, "today")

    save_local_config(cfg, home_dir=home)
    text = _config_file(home).read_text(encoding="utf-8")
    assert "today" not in text
    assert load_local_config(home_dir=home).branch == "trunk"


def test_config_no_longer_writes_a_today_key(tmp_path: Path) -> None:
    home = tmp_path / "home"
    save_local_config(WorkbenchLocalConfig(data_repo_url="x"), home_dir=home)
    assert "today" not in _config_file(home).read_text(encoding="utf-8")


def test_setup_wizard_no_longer_asks_about_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wizard, "ls_remote", lambda url: True)
    monkeypatch.setattr(wizard, "clone_repo", lambda *args, **kwargs: None)
    home = tmp_path / "home"
    answers = ["git@example.com:me/data.git", "me", "me@example.invalid", "", "", "no"]
    res = runner.invoke(cli_app, ["setup", "--home", str(home)], input="\n".join(answers) + "\n")
    assert res.exit_code == 0, res.output
    assert "Today" not in res.output
    assert "Skipped agent installation" in res.output
    assert "today" not in _config_file(home).read_text(encoding="utf-8")


def test_today_command_is_gone() -> None:
    res = runner.invoke(cli_app, ["today"])
    assert res.exit_code == 2
    assert "No such command" in res.output


def test_serve_no_longer_sets_a_today_variable(
    isolate_environment: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    _config_file(isolate_environment).parent.mkdir(parents=True)
    _config_file(isolate_environment).write_text(
        'data_repo_url = "git@example.com:me/data.git"\ntoday = "off"\n', encoding="utf-8"
    )
    started: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(app=app))
    res = runner.invoke(cli_app, ["serve"])
    assert res.exit_code == 0, res.output
    assert "WORKBENCH_TODAY" not in os.environ
    assert not hasattr(started["app"].state.workbench, "today_enabled")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def test_enabled_defaults_to_true_and_must_be_a_boolean() -> None:
    assert Today.model_validate({}).enabled is True
    assert Today.model_validate({"enabled": False}).enabled is False
    with pytest.raises(ValueError):
        Today.model_validate({"enabled": "off"})


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


def _make_remote(tmp_path: Path, today_yaml: str | None = TODAY_YAML) -> tuple[Path, Path]:
    """A bare remote with one project (and a today.yaml); returns (bare remote, seeding checkout)."""
    bare = tmp_path / "remote.git"
    init_bare_repo(bare)
    seed = tmp_path / "seed"
    init_repo(seed)
    (seed / "projects" / "robot-arm").mkdir(parents=True)
    (seed / "workbench.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (seed / "projects" / "robot-arm" / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    if today_yaml is not None:
        (seed / "today.yaml").write_text(today_yaml, encoding="utf-8")
    commit_all(seed, "Seed commit")
    run_git(["remote", "add", "origin", str(bare)], cwd=seed)
    assert push_head_to_branch(seed, branch="main")
    return bare, seed


def _remote_subjects(seed: Path) -> list[str]:
    run_git(["fetch", "origin", "main"], cwd=seed)
    return run_git(["log", "origin/main", "--format=%s"], cwd=seed).stdout.splitlines()


def _remote_today_text(seed: Path) -> str:
    run_git(["fetch", "origin", "main"], cwd=seed)
    return run_git(["show", "origin/main:today.yaml"], cwd=seed).stdout


def _remote_today(seed: Path) -> dict:
    return to_plain_python(load_yaml_str_roundtrip(_remote_today_text(seed)))


def _client(bare: Path, state_dir: Path) -> TestClient:
    state = ServerState(remote_url=str(bare), state_dir=state_dir, branch="main")
    return TestClient(create_app(state=state, enable_background_loop=False))


def _set_enabled(client: TestClient, body: object):
    return client.post("/api/today/enabled", headers=WRITE_HEADERS, json=body)


def _post_today(client: TestClient, entries: list[dict]):
    body = {"day": "2026-11-11", "entries": entries}
    return client.post("/api/today", headers=WRITE_HEADERS, json=body)


def test_snapshot_carries_enabled_and_no_features(tmp_path: Path) -> None:
    bare, _ = _make_remote(tmp_path)
    with _client(bare, tmp_path / "state") as client:
        assert client.get("/api/status").json()["last_error"] is None
        snap = client.get("/api/snapshot").json()
        assert snap["today"] == STORED_TODAY
        assert "features" not in snap


def test_turning_off_keeps_the_list_and_refuses_entry_changes(tmp_path: Path) -> None:
    bare, seed = _make_remote(tmp_path)
    with _client(bare, tmp_path / "state") as client:
        off = _set_enabled(client, {"enabled": False})
        assert off.status_code == 200, off.text
        body = off.json()
        assert body["ok"] is True
        # The entries are still handed out; the dashboard hides them
        assert body["snapshot"]["today"] == {**STORED_TODAY, "enabled": False}
        assert client.get("/api/snapshot").json()["today"]["enabled"] is False
        assert _remote_subjects(seed)[0] == "edit(today): turn off"
        # Only the switch changes: the day, the entries and the comment are kept
        assert _remote_today(seed) == {"schema_version": 1, **STORED_TODAY, "enabled": False}
        assert "# chosen in the morning review" in _remote_today_text(seed)

        before = _remote_subjects(seed)
        refused = _post_today(client, [])
        assert refused.status_code == 403
        assert refused.json() == {"detail": OFF_DETAIL}
        refused_add = _post_today(client, [{"kind": "project", "id": "robot-arm"}])
        assert refused_add.status_code == 403
        assert _remote_subjects(seed) == before
        assert _remote_today(seed)["entries"] == [ENTRY]

        # Other writes still work
        status = client.post(
            "/api/projects/robot-arm/status", headers=WRITE_HEADERS, json={"status": "paused"}
        )
        assert status.status_code == 200, status.text
        assert status.json()["snapshot"]["today"]["enabled"] is False


def test_turning_back_on_is_allowed_and_entry_changes_work_again(tmp_path: Path) -> None:
    bare, seed = _make_remote(tmp_path, TODAY_YAML.replace("day:", "enabled: false\nday:"))
    with _client(bare, tmp_path / "state") as client:
        assert client.get("/api/snapshot").json()["today"]["enabled"] is False
        assert _post_today(client, []).status_code == 403

        on = _set_enabled(client, {"enabled": True})
        assert on.status_code == 200, on.text
        assert on.json()["snapshot"]["today"] == STORED_TODAY
        assert _remote_subjects(seed)[0] == "edit(today): turn on"
        assert _remote_today(seed)["enabled"] is True

        cleared = _post_today(client, [])
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["snapshot"]["today"] == {
            "enabled": True,
            "day": "2026-11-11",
            "entries": [],
        }
        assert _remote_subjects(seed)[0] == "edit(today): clear"
        # An entry change leaves the switch as it is
        assert _remote_today(seed)["enabled"] is True


def test_setting_the_stored_value_commits_nothing(tmp_path: Path) -> None:
    bare, seed = _make_remote(tmp_path, TODAY_YAML.replace("day:", "enabled: true\nday:"))
    with _client(bare, tmp_path / "state") as client:
        before = _remote_subjects(seed)
        same = _set_enabled(client, {"enabled": True})
        assert same.status_code == 200, same.text
        assert same.json()["snapshot"]["today"] == STORED_TODAY
        assert _remote_subjects(seed) == before


def test_switch_creates_today_yaml_when_missing(tmp_path: Path) -> None:
    bare, seed = _make_remote(tmp_path, today_yaml=None)
    with _client(bare, tmp_path / "state") as client:
        assert client.get("/api/snapshot").json()["today"] == {
            "enabled": True,
            "day": None,
            "entries": [],
        }
        off = _set_enabled(client, {"enabled": False})
        assert off.status_code == 200, off.text
        assert off.json()["snapshot"]["today"] == {"enabled": False, "day": None, "entries": []}
        assert _remote_today_text(seed) == "schema_version: 1\nenabled: false\n"
        assert _remote_subjects(seed)[0] == "edit(today): turn off"


def test_a_switch_made_elsewhere_is_respected_before_the_server_syncs(tmp_path: Path) -> None:
    """The 403 check reads today.yaml from origin inside the write, not the cached snapshot."""
    bare, seed = _make_remote(tmp_path)
    with _client(bare, tmp_path / "state") as client:
        assert client.get("/api/snapshot").json()["today"]["enabled"] is True
        # Another device turns it off and pushes; this server has not synced yet
        (seed / "today.yaml").write_text(
            TODAY_YAML.replace("day:", "enabled: false\nday:"), encoding="utf-8"
        )
        commit_all(seed, "edit(today): turn off")
        assert push_head_to_branch(seed, branch="main")
        assert client.get("/api/snapshot").json()["today"]["enabled"] is True

        refused = _post_today(client, [])
        assert refused.status_code == 403 and refused.json() == {"detail": OFF_DETAIL}
        assert _remote_subjects(seed)[0] == "edit(today): turn off"


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({}, "'enabled' is required"),
        ({"enabled": "false"}, "'enabled' must be true or false"),
        ({"enabled": 0}, "'enabled' must be true or false"),
        ({"enabled": None}, "'enabled' must be true or false"),
        ({"enabled": True, "day": "2026-11-11"}, "day"),
        ([True], "Body must be a JSON object"),
    ],
)
def test_switch_rejects_bad_bodies(tmp_path: Path, body: object, detail: str) -> None:
    bare, seed = _make_remote(tmp_path)
    with _client(bare, tmp_path / "state") as client:
        before = _remote_subjects(seed)
        resp = _set_enabled(client, body)
        assert resp.status_code == 400, resp.text
        assert detail in resp.json()["detail"]
        assert _remote_subjects(seed) == before


def test_switch_requires_the_write_headers(tmp_path: Path) -> None:
    bare, _ = _make_remote(tmp_path)
    with _client(bare, tmp_path / "state") as client:
        no_header = client.post("/api/today/enabled", json={"enabled": False})
        assert no_header.status_code == 403
        assert no_header.json()["detail"] == "Missing or invalid X-Workbench header"
        not_json = client.post(
            "/api/today/enabled",
            headers={"X-Workbench": "1", "Content-Type": "text/plain"},
            content='{"enabled": false}',
        )
        assert not_json.status_code == 415


def test_turned_off_file_is_still_validated(tmp_path: Path) -> None:
    bare, seed = _make_remote(tmp_path, TODAY_YAML.replace("day:", "enabled: false\nday:"))
    with _client(bare, tmp_path / "state") as client:
        good_commit = client.get("/api/status").json()["last_good_commit"]
        (seed / "today.yaml").write_text(
            "schema_version: 1\nenabled: false\nentries: nope\n", encoding="utf-8"
        )
        commit_all(seed, "Break today.yaml")
        assert push_head_to_branch(seed, branch="main")

        assert client.post("/api/sync", headers=WRITE_HEADERS).json()["ok"] is False
        status = client.get("/api/status").json()
        assert status["last_error"]["file"] == "today.yaml"
        assert status["last_good_commit"] == good_commit
