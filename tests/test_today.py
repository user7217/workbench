"""Tests for Today's Work: the today.yaml model and validation, the snapshot key, and POST /api/today."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from workbench.cli import app as cli_app
from workbench.commitmsg import parse_message
from workbench.demo import init_demo_environment
from workbench.gitops import (
    commit_all,
    create_worktree,
    init_bare_repo,
    init_repo,
    push_head_to_branch,
    remove_worktree,
    run_git,
)
from workbench.models import MAX_TODAY_ENTRIES, Today
from workbench.semantic import check_semantic_diff
from workbench.server.app import create_app
from workbench.server.sync import ServerState
from workbench.server.writes.today import describe_change
from workbench.validate import (
    is_allowed_repo_relative_path,
    validate_any_file,
    validate_changed_paths,
    validate_data_repo,
)
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    to_plain_python,
)

runner = CliRunner()
WRITE_HEADERS = {"X-Workbench": "1", "Content-Type": "application/json"}
EMPTY_TODAY = {"enabled": True, "day": None, "entries": []}

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
    components:
      - id: ik-solver
        name: IK solver
        status: not_started
"""

LEARNING_YAML = """schema_version: 1
id: ros2-basics
name: ROS 2 Basics
status: active
updated: 2026-11-01
topics:
  - id: tf2
    name: tf2
    status: not_started
"""

TODAY_YAML = """schema_version: 1
day: 2026-11-10
entries:
  - kind: project
    id: robot-arm
    item: kinematics
    added: 2026-11-09
  - kind: learning
    id: ros2-basics
    added: "2026-11-10"
"""


def _write_repo(root: Path, today: str | None = None) -> Path:
    """A minimal valid workbench-data checkout, optionally with a today.yaml."""
    (root / "projects" / "robot-arm").mkdir(parents=True)
    (root / "learning" / "ros2-basics").mkdir(parents=True)
    (root / "workbench.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / "projects" / "robot-arm" / "project.yaml").write_text(PROJECT_YAML, encoding="utf-8")
    (root / "learning" / "ros2-basics" / "path.yaml").write_text(LEARNING_YAML, encoding="utf-8")
    if today is not None:
        (root / "today.yaml").write_text(today, encoding="utf-8")
    return root


def _entries_yaml(count: int) -> str:
    lines = [f"  - kind: project\n    id: p-{i}\n    added: 2026-11-10\n" for i in range(count)]
    return "schema_version: 1\nday: 2026-11-10\nentries:\n" + "".join(lines)


# ---------------------------------------------------------------------------
# Model and repository validation
# ---------------------------------------------------------------------------


def test_repo_without_today_file_is_valid(tmp_path: Path) -> None:
    res = validate_data_repo(_write_repo(tmp_path / "repo"))
    assert res.ok, res.errors


def test_valid_today_file_validates_alone_and_in_a_repo(tmp_path: Path) -> None:
    repo = _write_repo(tmp_path / "repo", TODAY_YAML)
    res = validate_data_repo(repo)
    assert res.ok, res.errors
    assert validate_any_file(repo / "today.yaml").ok

    today = Today.model_validate(to_plain_python(load_yaml_str_roundtrip(TODAY_YAML)))
    assert today.day == "2026-11-10"
    assert [(e.kind, e.id, e.item, e.added) for e in today.entries] == [
        ("project", "robot-arm", "kinematics", "2026-11-09"),
        ("learning", "ros2-basics", None, "2026-11-10"),
    ]


def test_today_file_defaults_and_null_day() -> None:
    assert Today.model_validate({}).model_dump() == {
        "schema_version": 1,
        "enabled": True,
        "day": None,
        "entries": [],
    }
    assert Today.model_validate({"schema_version": 1, "day": None, "entries": []}).day is None


def test_only_the_root_today_file_is_an_allowed_path() -> None:
    assert is_allowed_repo_relative_path("today.yaml")
    assert validate_changed_paths(["today.yaml"]).ok
    for rel in ("today.yml", "projects/today.yaml", "projects/robot-arm/today.yaml", "notes.yaml"):
        assert not is_allowed_repo_relative_path(rel), rel
    res = validate_changed_paths(["tomorrow.yaml"])
    assert not res.ok and "today.yaml" in res.errors[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            TODAY_YAML
            + "  - kind: project\n    id: robot-arm\n    item: kinematics\n    added: 2026-11-10\n",
            "Duplicate today entry: project 'robot-arm/kinematics'",
            id="duplicate-entry",
        ),
        pytest.param(
            "schema_version: 1\nentries:\n  - kind: project\n    id: Robot_Arm\n    added: 2026-11-10\n",
            "today.yaml:entries.0.id",
            id="bad-slug",
        ),
        pytest.param(
            "schema_version: 1\nentries:\n  - kind: project\n    id: robot-arm\n    item: Not A Slug\n"
            "    added: 2026-11-10\n",
            "today.yaml:entries.0.item",
            id="bad-item-slug",
        ),
        pytest.param(TODAY_YAML + "note: hello\n", "today.yaml:note", id="unknown-key"),
        pytest.param(
            "schema_version: 1\nentries:\n  - kind: project\n    id: robot-arm\n    added: 2026-11-10\n"
            "    done: true\n",
            "today.yaml:entries.0.done",
            id="unknown-entry-key",
        ),
        pytest.param(
            "schema_version: 1\nentries:\n  - kind: task\n    id: robot-arm\n    added: 2026-11-10\n",
            "today.yaml:entries.0.kind",
            id="bad-kind",
        ),
        pytest.param(
            "schema_version: 1\nday: tomorrow\nentries: []\n", "today.yaml:day", id="bad-day"
        ),
        pytest.param(
            "schema_version: 1\nentries:\n  - kind: project\n    id: robot-arm\n",
            "today.yaml:entries.0.added",
            id="missing-added",
        ),
        pytest.param(
            _entries_yaml(MAX_TODAY_ENTRIES + 1), "today.yaml:entries", id="too-many-entries"
        ),
        pytest.param("schema_version: 2\nentries: []\n", "today.yaml:schema_version", id="version"),
        pytest.param("- just\n- a list\n", "must be a mapping", id="not-a-mapping"),
        pytest.param("entries: [unclosed\n", "YAML load error", id="yaml-syntax"),
        pytest.param(
            "schema_version: 1\nentries: []\nnote: /Users/someone/file.txt\n",
            "local_path_users",
            id="privacy-scan",
        ),
    ],
)
def test_invalid_today_file_is_rejected(tmp_path: Path, text: str, expected: str) -> None:
    repo = _write_repo(tmp_path / "repo", text)
    res = validate_data_repo(repo)
    assert not res.ok
    assert any(expected in err for err in res.errors), res.errors
    assert not validate_any_file(repo / "today.yaml").ok


def test_exactly_the_maximum_number_of_entries_is_valid(tmp_path: Path) -> None:
    # Also a list made only of dangling entries: none of the p-N projects exist
    res = validate_data_repo(_write_repo(tmp_path / "repo", _entries_yaml(MAX_TODAY_ENTRIES)))
    assert res.ok, res.errors


def test_dangling_entries_are_accepted(tmp_path: Path) -> None:
    text = (
        "schema_version: 1\nday: 2026-11-10\nentries:\n"
        "  - kind: project\n    id: no-such-project\n    added: 2026-11-10\n"
        "  - kind: project\n    id: robot-arm\n    item: removed-item\n    added: 2026-11-10\n"
        "  - kind: learning\n    id: no-such-path\n    item: nothing\n    added: 2026-11-10\n"
    )
    res = validate_data_repo(_write_repo(tmp_path / "repo", text))
    assert res.ok, res.errors


def test_same_id_under_different_kind_or_item_is_not_a_duplicate() -> None:
    entries = [
        {"kind": "project", "id": "shared", "added": "2026-11-10"},
        {"kind": "learning", "id": "shared", "added": "2026-11-10"},
        {"kind": "project", "id": "shared", "item": "one", "added": "2026-11-10"},
        {"kind": "project", "id": "shared", "item": "two", "added": "2026-11-10"},
    ]
    assert len(Today.model_validate({"entries": entries}).entries) == 4
    with pytest.raises(ValidationError):
        Today.model_validate({"entries": [entries[0], dict(entries[0])]})


def test_describe_change_summaries() -> None:
    day = "2026-11-10"
    arm = ("project", "robot-arm", None)
    kin = ("project", "robot-arm", "kinematics")
    tf2 = ("learning", "ros2-basics", "tf2")
    ros = ("learning", "ros2-basics", None)
    assert describe_change([], [kin], day) == "add robot-arm/kinematics"
    assert describe_change([kin], [kin, ros], day) == "add learning/ros2-basics"
    assert describe_change([kin, tf2], [kin], day) == "remove learning/ros2-basics/tf2"
    assert describe_change([arm, kin], [kin], day) == "remove robot-arm"
    assert describe_change([kin], [], day) == "clear"
    assert describe_change([], [], day) == "clear"
    assert describe_change([], [arm, kin, tf2], day) == "set 3 entries for 2026-11-10"
    assert describe_change([kin], [tf2], day) == "set 1 entry for 2026-11-10"
    # Reordering or confirming the same list for a new day changes no entry
    assert describe_change([arm, kin], [kin, arm], day) == "set 2 entries for 2026-11-10"
    for summary in ("add robot-arm/kinematics", "clear", "set 3 entries for 2026-11-10"):
        parsed = parse_message(f"edit(today): {summary}")
        assert parsed is not None and (parsed.kind, parsed.scope) == ("edit", "today")


# ---------------------------------------------------------------------------
# Server: snapshot and POST /api/today
# ---------------------------------------------------------------------------


def _make_remote(tmp_path: Path, today: str | None = None) -> tuple[Path, Path]:
    """A bare remote seeded with the minimal repo; returns (bare remote, seeding checkout)."""
    bare = tmp_path / "remote.git"
    init_bare_repo(bare)
    seed = tmp_path / "seed"
    init_repo(seed)
    _write_repo(seed, today)
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


def _post_today(client: TestClient, body: object, headers: dict | None = WRITE_HEADERS):
    return client.post("/api/today", headers=headers or {}, json=body)


@pytest.fixture
def today_env(tmp_path: Path):
    """A server on a repo that has no today.yaml yet."""
    bare, seed = _make_remote(tmp_path)
    state = ServerState(remote_url=str(bare), state_dir=tmp_path / "state", branch="main")
    with TestClient(create_app(state=state, enable_background_loop=False)) as client:
        assert client.get("/api/status").json()["last_error"] is None
        yield client, seed, state


def test_snapshot_has_empty_today_without_the_file(today_env) -> None:
    client, _, _ = today_env
    assert client.get("/api/snapshot").json()["today"] == EMPTY_TODAY


def test_snapshot_reports_a_pushed_today_file(tmp_path: Path) -> None:
    bare, _ = _make_remote(tmp_path, TODAY_YAML)
    state = ServerState(remote_url=str(bare), state_dir=tmp_path / "state", branch="main")
    with TestClient(create_app(state=state, enable_background_loop=False)) as client:
        assert client.get("/api/status").json()["last_error"] is None
        # Dates are strings whether or not the YAML quoted them; an absent item has no key
        assert client.get("/api/snapshot").json()["today"] == {
            "enabled": True,
            "day": "2026-11-10",
            "entries": [
                {"kind": "project", "id": "robot-arm", "item": "kinematics", "added": "2026-11-09"},
                {"kind": "learning", "id": "ros2-basics", "added": "2026-11-10"},
            ],
        }


def test_snapshot_cached_by_an_older_version_gets_the_empty_default(tmp_path: Path) -> None:
    state = ServerState(remote_url="", state_dir=tmp_path / "state", branch="main")
    assert state.get_snapshot_dict()["today"] == EMPTY_TODAY
    old = {"projects": [], "learning": [], "topic_markdowns": {}, "commit": "abc1234"}
    state.state_dir.mkdir(parents=True)
    state.snapshot_path.write_text(json.dumps(old), encoding="utf-8")
    assert state.load_snapshot_from_disk() is True
    with TestClient(create_app(state=state, enable_background_loop=False)) as client:
        snap = client.get("/api/snapshot").json()
    assert snap["today"] == EMPTY_TODAY and snap["commit"] == "abc1234"


def test_post_today_creates_keeps_added_and_stores_body_order(today_env) -> None:
    client, seed, _ = today_env

    # 1. First write creates today.yaml; every entry is new, so `added` is the day
    first = _post_today(
        client,
        {
            "day": "2026-11-09",
            "entries": [{"kind": "project", "id": "robot-arm", "item": "kinematics"}],
        },
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ok"] is True and body["status"]["last_error"] is None
    assert body["snapshot"]["today"] == {
        "enabled": True,
        "day": "2026-11-09",
        "entries": [
            {"kind": "project", "id": "robot-arm", "item": "kinematics", "added": "2026-11-09"}
        ],
    }
    assert body["snapshot"]["commit"] == body["status"]["last_good_commit"]
    assert _remote_subjects(seed)[0] == "edit(today): add robot-arm/kinematics"
    # Dates are written as plain YAML dates, like `updated` in project files
    assert _remote_today_text(seed) == (
        "schema_version: 1\nday: 2026-11-09\nentries:\n"
        "  - kind: project\n    id: robot-arm\n    item: kinematics\n    added: 2026-11-09\n"
    )

    # 2. Next day: two new entries around the old one; body order is stored, old `added` is kept
    second = _post_today(
        client,
        {
            "day": "2026-11-10",
            "entries": [
                {"kind": "learning", "id": "ros2-basics", "item": None},
                {"kind": "project", "id": "robot-arm", "item": "kinematics"},
                {"kind": "project", "id": "robot-arm"},
            ],
        },
    )
    assert second.status_code == 200, second.text
    expected = {
        "day": "2026-11-10",
        "entries": [
            {"kind": "learning", "id": "ros2-basics", "added": "2026-11-10"},
            {"kind": "project", "id": "robot-arm", "item": "kinematics", "added": "2026-11-09"},
            {"kind": "project", "id": "robot-arm", "added": "2026-11-10"},
        ],
    }
    assert second.json()["snapshot"]["today"] == {"enabled": True, **expected}
    assert client.get("/api/snapshot").json()["today"] == {"enabled": True, **expected}
    assert _remote_today(seed) == {"schema_version": 1, **expected}
    assert _remote_subjects(seed)[0] == "edit(today): set 3 entries for 2026-11-10"

    # 3. One more entry
    four = [
        {"kind": "learning", "id": "ros2-basics"},
        {"kind": "project", "id": "robot-arm", "item": "kinematics"},
        {"kind": "project", "id": "robot-arm"},
        {"kind": "learning", "id": "ros2-basics", "item": "tf2"},
    ]
    third = _post_today(
        client,
        {
            "day": "2026-11-10",
            "entries": four,
        },
    )
    assert third.status_code == 200, third.text
    assert _remote_subjects(seed)[0] == "edit(today): add learning/ros2-basics/tf2"
    # A stored date is never written as a YAML alias of another one
    assert "&" not in _remote_today_text(seed) and "*" not in _remote_today_text(seed)

    # 4. Sending the stored list again changes nothing and makes no commit
    head_before = _remote_subjects(seed)
    again = _post_today(client, {"day": "2026-11-10", "entries": four})
    assert again.status_code == 200, again.text
    assert again.json()["snapshot"]["today"]["entries"][3]["added"] == "2026-11-10"
    assert _remote_subjects(seed) == head_before


def test_post_today_removing_and_clearing_is_never_blocked(today_env) -> None:
    client, seed, _ = today_env
    many = [{"kind": "project", "id": f"gone-project-{i}"} for i in range(MAX_TODAY_ENTRIES)]
    filled = _post_today(client, {"day": "2026-11-10", "entries": many})
    assert filled.status_code == 200, filled.text
    assert (
        _remote_subjects(seed)[0] == f"edit(today): set {MAX_TODAY_ENTRIES} entries for 2026-11-10"
    )

    # Dropping one entry
    one_less = _post_today(client, {"day": "2026-11-10", "entries": many[1:]})
    assert one_less.status_code == 200, one_less.text
    assert _remote_subjects(seed)[0] == "edit(today): remove gone-project-0"

    # Dropping nearly everything at once (far past the 30% mass-deletion limit for projects)
    most_gone = _post_today(client, {"day": "2026-11-10", "entries": many[-2:]})
    assert most_gone.status_code == 200, most_gone.text
    assert len(most_gone.json()["snapshot"]["today"]["entries"]) == 2

    # Replacing with an empty list keeps the file, with no entries
    cleared = _post_today(client, {"day": "2026-11-11", "entries": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["snapshot"]["today"] == {
        "enabled": True,
        "day": "2026-11-11",
        "entries": [],
    }
    assert _remote_today(seed) == {"schema_version": 1, "day": "2026-11-11", "entries": []}
    assert _remote_subjects(seed)[0] == "edit(today): clear"
    assert client.get("/api/status").json()["last_error"] is None


def test_post_today_accepts_entries_for_things_that_do_not_exist(today_env) -> None:
    client, seed, _ = today_env
    resp = _post_today(
        client,
        {
            "day": "2026-11-10",
            "entries": [
                {"kind": "project", "id": "no-such-project", "item": "nothing"},
                {"kind": "learning", "id": "no-such-path"},
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(_remote_today(seed)["entries"]) == 2


def test_post_today_keeps_comments_and_quoted_dates_of_an_existing_file(tmp_path: Path) -> None:
    text = "# my list\n" + TODAY_YAML
    bare, seed = _make_remote(tmp_path, text)
    state = ServerState(remote_url=str(bare), state_dir=tmp_path / "state", branch="main")
    with TestClient(create_app(state=state, enable_background_loop=False)) as client:
        resp = _post_today(
            client, {"day": "2026-11-12", "entries": [{"kind": "learning", "id": "ros2-basics"}]}
        )
        assert resp.status_code == 200, resp.text
    assert _remote_today_text(seed) == (
        "# my list\nschema_version: 1\nday: 2026-11-12\nentries:\n"
        '  - kind: learning\n    id: ros2-basics\n    added: "2026-11-10"\n'
    )
    assert _remote_subjects(seed)[0] == "edit(today): remove robot-arm/kinematics"


def test_post_today_header_rules(today_env) -> None:
    client, seed, _ = today_env
    body = {"day": "2026-11-10", "entries": []}
    head_before = _remote_subjects(seed)

    missing = client.post("/api/today", json=body)
    assert missing.status_code == 403
    assert missing.json()["detail"] == "Missing or invalid X-Workbench header"
    wrong = client.post("/api/today", headers={"X-Workbench": "0"}, json=body)
    assert wrong.status_code == 403
    not_json = client.post(
        "/api/today", headers={"X-Workbench": "1", "Content-Type": "text/plain"}, content="{}"
    )
    assert not_json.status_code == 415
    broken = client.post("/api/today", headers=WRITE_HEADERS, content="{not json")
    assert broken.status_code == 400 and broken.json()["detail"] == "Invalid JSON body"
    as_list = _post_today(client, [body])
    assert as_list.status_code == 400 and as_list.json()["detail"] == "Body must be a JSON object"

    assert _remote_subjects(seed) == head_before
    assert client.get("/api/snapshot").json()["today"] == EMPTY_TODAY


_ENTRY = {"kind": "project", "id": "robot-arm"}


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        pytest.param(
            {"day": "2026-11-10", "entries": [], "note": "x"},
            "Unsupported field(s): note (allowed: day, entries)",
            id="unknown-key",
        ),
        pytest.param({"entries": []}, "'day' is required", id="missing-day"),
        pytest.param({"day": "2026-11-10"}, "'entries' is required", id="missing-entries"),
        pytest.param(
            {"day": "10/11/2026", "entries": []},
            "Invalid day: '10/11/2026' (expected YYYY-MM-DD)",
            id="bad-day-format",
        ),
        pytest.param(
            {"day": "2026-02-31", "entries": []},
            "Invalid day: '2026-02-31' (expected YYYY-MM-DD)",
            id="impossible-day",
        ),
        pytest.param(
            {"day": None, "entries": []}, "Invalid day: None (expected YYYY-MM-DD)", id="null-day"
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": {}}, "'entries' must be a list", id="entries-not-list"
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": ["robot-arm"]},
            "Entry 0 must be a JSON object",
            id="entry-not-object",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{**_ENTRY, "added": "2026-11-01"}]},
            "Unsupported field(s): added (allowed: kind, id, item)",
            id="unknown-entry-key",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{"kind": "task", "id": "robot-arm"}]},
            "Invalid kind: 'task' (expected one of project, learning)",
            id="bad-kind",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{"id": "robot-arm"}]},
            "Invalid kind: None (expected one of project, learning)",
            id="missing-kind",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{"kind": "project", "id": "Robot Arm"}]},
            "Invalid id: 'Robot Arm'",
            id="bad-id",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{"kind": "project"}]},
            "Invalid id: None",
            id="missing-id",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{**_ENTRY, "item": "a" * 49}]},
            f"Invalid item: '{'a' * 49}'",
            id="item-too-long",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [{**_ENTRY, "item": ""}]},
            "Invalid item: ''",
            id="empty-item",
        ),
        pytest.param(
            {
                "day": "2026-11-10",
                "entries": [_ENTRY, {**_ENTRY, "item": "x"}, {**_ENTRY, "item": "x"}],
            },
            "Duplicate entry: project 'robot-arm/x'",
            id="duplicate",
        ),
        pytest.param(
            {"day": "2026-11-10", "entries": [_ENTRY, {**_ENTRY, "item": None}]},
            "Duplicate entry: project 'robot-arm'",
            id="duplicate-null-item",
        ),
        pytest.param(
            {
                "day": "2026-11-10",
                "entries": [
                    {"kind": "project", "id": f"p-{i}"} for i in range(MAX_TODAY_ENTRIES + 1)
                ],
            },
            "Too many entries: 61 (at most 60)",
            id="too-many",
        ),
    ],
)
def test_post_today_rejects_bad_bodies(today_env, body: dict, detail: str) -> None:
    client, seed, _ = today_env
    head_before = _remote_subjects(seed)
    resp = _post_today(client, body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == detail
    assert _remote_subjects(seed) == head_before
    assert client.get("/api/snapshot").json()["today"] == EMPTY_TODAY


def test_post_today_replaces_an_unreadable_stored_file(tmp_path: Path) -> None:
    """A hand-damaged today.yaml stops syncing; the next write from the dashboard repairs it."""
    bare, seed = _make_remote(tmp_path, "entries: [unclosed\n")
    state = ServerState(remote_url=str(bare), state_dir=tmp_path / "state", branch="main")
    with TestClient(create_app(state=state, enable_background_loop=False)) as client:
        assert "today.yaml" in client.get("/api/status").json()["last_error"]["message"]
        resp = _post_today(client, {"day": "2026-11-10", "entries": [_ENTRY]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"]["last_error"] is None
        assert resp.json()["snapshot"]["today"]["entries"] == [{**_ENTRY, "added": "2026-11-10"}]
    assert _remote_today(seed)["schema_version"] == 1


def test_project_edits_still_work_next_to_a_today_file(tmp_path: Path) -> None:
    bare, seed = _make_remote(tmp_path, TODAY_YAML)
    state = ServerState(remote_url=str(bare), state_dir=tmp_path / "state", branch="main")
    with TestClient(create_app(state=state, enable_background_loop=False)) as client:
        resp = client.post(
            "/api/projects/robot-arm/items/ik-solver/status",
            headers=WRITE_HEADERS,
            json={"status": "done"},
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["snapshot"]["today"]["entries"]) == 2
    assert _remote_today_text(seed) == TODAY_YAML


# ---------------------------------------------------------------------------
# CLI push pipeline
# ---------------------------------------------------------------------------


def _push_to_main(data_dir: Path, tmp_path: Path, name: str, today: str | None) -> None:
    """Commit a new today.yaml (None deletes it) straight to origin/main, as the server would."""
    wt = tmp_path / f"wt_{name}"
    create_worktree(data_dir, wt, ref="origin/main")
    if today is None:
        (wt / "today.yaml").unlink()
    else:
        (wt / "today.yaml").write_text(today, encoding="utf-8")
    commit_all(wt, f"edit(today): {name}")
    assert push_head_to_branch(wt, branch="main")
    remove_worktree(data_dir, wt)
    run_git(["fetch", "origin", "main"], cwd=data_dir)


def _begin(project_id: str) -> Path:
    result = runner.invoke(cli_app, ["begin", "--id", project_id])
    assert result.exit_code == 0, result.output
    return Path(result.stdout.strip())


def _edit_description(project_file: Path, text: str) -> None:
    doc = load_yaml_roundtrip(project_file)
    doc["description"] = text
    dump_yaml_roundtrip(doc, project_file)


def test_cli_push_is_not_blocked_by_today_changes_made_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree opened before the dashboard changed today.yaml still pushes its project edit."""
    info = init_demo_environment(tmp_path / "demo")
    data_dir = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", info["laptop_home"])
    one_entry = "schema_version: 1\nday: 2026-11-10\nentries:\n  - kind: project\n    id: demo-robot-arm\n    added: 2026-11-10\n"

    # Entries left the list after the worktree was opened
    file_a = _begin("demo-budget-cli")
    _push_to_main(data_dir, tmp_path, "fewer", one_entry)
    _edit_description(file_a, "Budget CLI, edited while the today list shrank.")
    push_a = runner.invoke(cli_app, ["push", str(file_a), "-m", "Update budget cli"])
    assert push_a.exit_code == 0, push_a.output

    # The whole file went away after the worktree was opened
    file_b = _begin("demo-budget-cli")
    _push_to_main(data_dir, tmp_path, "gone", None)
    _edit_description(file_b, "Budget CLI, edited while the today list was deleted.")
    push_b = runner.invoke(cli_app, ["push", str(file_b), "-m", "Update budget cli again"])
    assert push_b.exit_code == 0, push_b.output

    # The file was created after the worktree was opened (it is missing from the worktree)
    file_c = _begin("demo-budget-cli")
    _push_to_main(data_dir, tmp_path, "back", one_entry)
    _edit_description(file_c, "Budget CLI, edited while the today list was created.")
    push_c = runner.invoke(cli_app, ["push", str(file_c), "-m", "Update budget cli once more"])
    assert push_c.exit_code == 0, push_c.output

    run_git(["fetch", "origin", "main"], cwd=data_dir)
    assert run_git(["show", "origin/main:today.yaml"], cwd=data_dir).stdout == one_entry
    budget = run_git(
        ["show", "origin/main:projects/demo-budget-cli/project.yaml"], cwd=data_dir
    ).stdout
    assert "edited while the today list was created" in budget


def test_cli_push_validates_an_edited_today_file_without_removal_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    info = init_demo_environment(tmp_path / "demo")
    data_dir = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", info["laptop_home"])

    # Emptying the list in a worktree needs no --allow-removals
    wt_today = _begin("demo-budget-cli").parent.parent.parent / "today.yaml"
    wt_today.write_text("schema_version: 1\nday: 2026-11-10\nentries: []\n", encoding="utf-8")
    cleared = runner.invoke(cli_app, ["push", str(wt_today.parent), "-m", "Clear today"])
    assert cleared.exit_code == 0, cleared.output
    run_git(["fetch", "origin", "main"], cwd=data_dir)
    assert "entries: []" in run_git(["show", "origin/main:today.yaml"], cwd=data_dir).stdout
    assert validate_data_repo(data_dir).ok

    # An invalid today.yaml is still refused
    wt_bad = _begin("demo-budget-cli").parent.parent.parent / "today.yaml"
    wt_bad.write_text("schema_version: 1\nentries:\n  - kind: task\n    id: x\n", encoding="utf-8")
    refused = runner.invoke(cli_app, ["push", str(wt_bad.parent), "-m", "Bad today"])
    assert refused.exit_code == 1
    assert "today.yaml:entries.0.kind" in refused.output

    # `workbench validate` accepts the repository and the file on its own
    assert runner.invoke(cli_app, ["validate", str(data_dir)]).exit_code == 0
    assert runner.invoke(cli_app, ["validate", str(data_dir / "today.yaml")]).exit_code == 0


def test_project_guards_are_unchanged() -> None:
    """The semantic guard still protects project ids and text; only today.yaml is exempt."""
    old = to_plain_python(load_yaml_str_roundtrip(PROJECT_YAML))
    without_phase = {**old, "phases": []}
    res = check_semantic_diff(old, without_phase)
    assert any("Removed phase ID 'kinematics'" in err for err in res.errors), res.errors
    without_text = {**old, "description": ""}
    assert any("Mass deletion" in err for err in check_semantic_diff(old, without_text).errors)


def test_demo_environment_seeds_a_valid_today_list(tmp_path: Path) -> None:
    import datetime

    info = init_demo_environment(tmp_path / "demo")
    data_dir = Path(info["laptop_data_dir"])
    res = validate_data_repo(data_dir)
    assert res.ok, res.errors
    today = to_plain_python(load_yaml_str_roundtrip((data_dir / "today.yaml").read_text("utf-8")))
    assert today["day"] == datetime.date.today().isoformat()
    assert [(e["kind"], e["id"], e["item"]) for e in today["entries"]] == [
        ("project", "demo-robot-arm", "kinematics"),
        ("learning", "demo-robotics-kinematics", "poe-kinematics"),
    ]
    # Both entries point at real demo items
    project = (data_dir / "projects" / "demo-robot-arm" / "project.yaml").read_text("utf-8")
    path = (data_dir / "learning" / "demo-robotics-kinematics" / "path.yaml").read_text("utf-8")
    assert "- id: kinematics" in project and "- id: poe-kinematics" in path
