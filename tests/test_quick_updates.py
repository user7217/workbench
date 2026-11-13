"""Quick per-message updates: show --brief/--item, set/add, focus, commit kinds, history filters,
fingerprint-based hook decisions, and the two-skill installer."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workbench import commitmsg
from workbench.cli import app
from workbench.config import load_local_config, save_local_config
from workbench.demo import init_demo_environment
from workbench.gitops import commit_all, get_head_commit, get_root_commit, run_git
from workbench.agents.hooks import evaluate_hook_input, mark_repo_seen
from workbench.agents.install import run_install
from workbench.yamlio import load_yaml_str_roundtrip, to_plain_python

runner = CliRunner()
WS = "demo-weather-station"


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Demo environment with HOME pointed at the laptop and the LLM reviewer off."""
    info = init_demo_environment(tmp_path / "demo")
    home = Path(info["laptop_home"])
    monkeypatch.setenv("HOME", str(home))
    cfg = load_local_config(home_dir=home)
    cfg.privacy_review = "off"
    save_local_config(cfg, home_dir=home)
    info["home"] = home
    info["data"] = Path(info["laptop_data_dir"])
    info["ws_repo"] = Path(info["code_dir"]) / "weather-station"
    return info


def _remote_subjects(data: Path, n: int = 1) -> list[str]:
    run_git(["fetch", "origin", "main"], cwd=data)
    out = run_git(["log", f"-n{n}", "--format=%s", "origin/main"], cwd=data).stdout
    return out.splitlines()


def _remote_sha(data: Path) -> str:
    run_git(["fetch", "origin", "main"], cwd=data)
    return run_git(["rev-parse", "origin/main"], cwd=data).stdout.strip()


def _remote_project(data: Path, project_id: str) -> dict:
    run_git(["fetch", "origin", "main"], cwd=data)
    text = run_git(["show", f"origin/main:projects/{project_id}/project.yaml"], cwd=data).stdout
    return to_plain_python(load_yaml_str_roundtrip(text))


def _find(nodes: list[dict], item_id: str) -> dict | None:
    for node in nodes:
        if node["id"] == item_id:
            return node
        found = _find(node.get("components") or [], item_id)
        if found is not None:
            return found
    return None


def _invoke(args: list[str]):
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output
    return res


# ---------------------------------------------------------------------------
# show --brief / --item
# ---------------------------------------------------------------------------


def test_show_brief_item_and_child_projects(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(demo["ws_repo"])

    # Plain show is unchanged: the whole file
    full = _invoke(["show"]).output
    assert full == (demo["data"] / "projects" / WS / "project.yaml").read_text(encoding="utf-8")

    brief = _invoke(["show", "--brief"]).output.splitlines()
    assert brief[0] == f"[project] {WS}  active  Weather Station"
    assert "  enclosure  not_started  Weatherproof Enclosure" in brief
    assert "    radiation-shield  not_started  Radiation shield" in brief
    assert all(len(line.split("  ")) >= 3 for line in brief)

    item = _invoke(["show", "--id", WS, "--item", "enclosure"]).output
    node = to_plain_python(load_yaml_str_roundtrip(item))
    assert node["id"] == "enclosure"
    assert [c["id"] for c in node["components"]] == ["radiation-shield", "mast-clamp"]

    sub = _invoke(["show", "--id", WS, "--item", "enclosure", "--brief"]).output.splitlines()
    assert sub == [
        "enclosure  not_started  Weatherproof Enclosure",
        "  radiation-shield  not_started  Radiation shield",
        "  mast-clamp  not_started  Stainless mast mount",
    ]

    missing = runner.invoke(app, ["show", "--id", WS, "--item", "nope"])
    assert missing.exit_code == 1
    assert "not found" in missing.output

    # A child project is listed under the item it sits under
    _invoke(["new", "Shield Lab", "--parent", WS, "--under", "radiation-shield"])
    brief = _invoke(["show", "--id", WS, "--brief"]).output.splitlines()
    idx = brief.index("    radiation-shield  not_started  Radiation shield")
    assert brief[idx + 1] == "      [project] shield-lab  active  Shield Lab"


# ---------------------------------------------------------------------------
# set / add / focus
# ---------------------------------------------------------------------------


def test_set_updates_one_item_on_quick_path(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    data, home, ws_repo = demo["data"], demo["home"], demo["ws_repo"]
    # The LLM reviewer would fail every push; set/add skip it (deterministic checks still run).
    cfg = load_local_config(home_dir=home)
    cfg.privacy_review = "required"
    cfg.reviewer_command = "false"
    save_local_config(cfg, home_dir=home)
    monkeypatch.chdir(ws_repo)

    t0 = time.perf_counter()
    res = _invoke(["set", WS, "radio-tx", "--status", "done", "--notes", "Range test passed; next: duty cycle"])
    elapsed = time.perf_counter() - t0
    sha = _remote_sha(data)
    assert res.output.strip() == f"Updated {WS}/radio-tx: status done, notes ({sha[:8]})"
    assert elapsed < 10
    comp = _find(_remote_project(data, WS)["phases"][1]["components"], "radio-tx")
    assert (comp["status"], comp["notes"]) == ("done", "Range test passed; next: duty cycle")
    assert _remote_subjects(data) == [f"update({WS}/radio-tx): status done, notes"]

    # Focus and the hook's seen state are recorded: the hook does not prompt right after
    assert _invoke(["focus"]).output.strip() == f"Focus: {WS}/radio-tx"
    payload = json.dumps({"session_id": "s-set", "cwd": str(ws_repo), "stop_hook_active": False})
    assert evaluate_hook_input(payload, home_dir=home) is None

    # Phase status + name; empty string clears an optional field
    _invoke(["set", WS, "enclosure", "--status", "in_progress", "--name", "Enclosure"])
    _invoke(["set", WS, "radio-tx", "--notes", ""])
    proj = _remote_project(data, WS)
    assert (proj["phases"][2]["status"], proj["phases"][2]["name"]) == ("in_progress", "Enclosure")
    assert "notes" not in _find(proj["phases"][1]["components"], "radio-tx")
    assert _remote_subjects(data) == [f"update({WS}/radio-tx): clear notes"]

    # A no-op pushes nothing
    head = _remote_sha(data)
    res = _invoke(["set", WS, "radio-tx", "--status", "done"])
    assert res.output.startswith(f"Unchanged {WS}/radio-tx")
    assert _remote_sha(data) == head

    # Rejections push nothing and leave no worktree behind
    for args in (
        ["set", WS, "radio-tx"],
        ["set", WS, "radio-tx", "--status", "finished"],
        ["set", WS, "nope", "--status", "done"],
        ["set", "no-such-project", "radio-tx", "--status", "done"],
        ["set", WS, "radio-tx", "--name", ""],
        ["set", WS, "radio-tx", "--summary", "key AKIAIOSFODNN7EXAMPLE"],
    ):
        res = runner.invoke(app, args)
        assert res.exit_code == 1, (args, res.output)
    assert _remote_sha(data) == head
    work = home / ".workbench" / "work"
    assert not work.exists() or not any(work.iterdir())


def test_add_component_and_new_phase(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    data, ws_repo = demo["data"], demo["ws_repo"]
    monkeypatch.chdir(ws_repo)

    # Under a component (creates its nested components list)
    res = _invoke(
        ["add", WS, "--under", "radio-tx", "--name", "Fix Float timeout",
         "--summary", "rtk_sim never drops to Float", "--notes", "Needs a sim hook"]
    )
    sha = _remote_sha(data)
    assert res.output.strip() == f"Added {WS}/fix-float-timeout under radio-tx ({sha[:8]})"
    radio = _find(_remote_project(data, WS)["phases"][1]["components"], "radio-tx")
    assert radio["components"] == [
        {"id": "fix-float-timeout", "name": "Fix Float timeout", "status": "not_started",
         "summary": "rtk_sim never drops to Float", "notes": "Needs a sim hook"}
    ]
    assert _remote_subjects(data) == [f"update({WS}/radio-tx): add fix-float-timeout"]
    assert _invoke(["focus"]).output.strip() == f"Focus: {WS}/radio-tx"

    # Same name again gets a -2 suffix; an explicit id that is taken is refused
    res = _invoke(["add", WS, "--under", "enclosure", "--name", "Fix Float timeout", "--status", "in_progress"])
    assert f"{WS}/fix-float-timeout-2 under enclosure" in res.output
    dup = runner.invoke(app, ["add", WS, "--under", "enclosure", "--name", "X", "--id", "radio-tx"])
    assert dup.exit_code == 1 and "already used" in dup.output
    missing = runner.invoke(app, ["add", WS, "--under", "nope", "--name", "X"])
    assert missing.exit_code == 1 and "not found" in missing.output

    # No --under: a new phase
    res = _invoke(["add", WS, "--name", "Field deployment"])
    assert f"Added {WS}/field-deployment as a new phase" in res.output
    assert _remote_project(data, WS)["phases"][-1]["id"] == "field-deployment"
    assert _remote_subjects(data) == [f"update({WS}): add phase field-deployment"]
    assert _invoke(["focus"]).output.strip() == f"Focus: {WS}/field-deployment"


def test_depends_on_on_add_and_set(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quick commands can link items: --depends-on on add and set, with checks."""
    data, ws_repo = demo["data"], demo["ws_repo"]
    monkeypatch.chdir(ws_repo)

    _invoke(["add", WS, "--under", "radio-tx", "--name", "Range test", "--depends-on", "sensor-board, i2c-driver"])
    node = _find(_remote_project(data, WS)["phases"][1]["components"], "range-test")
    assert node["depends_on"] == ["sensor-board", "i2c-driver"]

    res = _invoke(["set", WS, "range-test", "--depends-on", "sensor-board"])
    assert "depends_on" in res.output
    assert _find(_remote_project(data, WS)["phases"][1]["components"], "range-test")["depends_on"] == ["sensor-board"]
    assert _remote_subjects(data) == [f"update({WS}/range-test): depends_on"]

    _invoke(["set", WS, "range-test", "--depends-on", ""])
    assert "depends_on" not in _find(_remote_project(data, WS)["phases"][1]["components"], "range-test")
    assert _remote_subjects(data) == [f"update({WS}/range-test): clear depends_on"]

    head = _remote_sha(data)
    for args, expected in (
        (["set", WS, "range-test", "--depends-on", "no-such-item"], "not a component"),
        (["set", WS, "range-test", "--depends-on", "range-test"], "itself"),
        (["set", WS, "enclosure", "--depends-on", "sensor-board"], "only be set on components"),
    ):
        bad = runner.invoke(app, args)
        assert bad.exit_code == 1 and expected in bad.output, bad.output
    assert _remote_sha(data) == head  # nothing pushed


def test_focus_command(demo: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert "Not in a git repository" in _invoke(["focus"]).output
    assert "nothing set" in _invoke(["focus", "--set", WS]).output

    monkeypatch.chdir(demo["ws_repo"])
    assert _invoke(["focus"]).output.strip() == "No focus set for this repo."
    assert _invoke(["focus", "--set", f"{WS}/enclosure"]).output.strip() == f"Focus set to {WS}/enclosure"
    assert _invoke(["focus"]).output.strip() == f"Focus: {WS}/enclosure"
    assert runner.invoke(app, ["focus", "--set", "Bad Id"]).exit_code == 1
    assert runner.invoke(app, ["focus", "--set", "a/b/c"]).exit_code == 1
    assert runner.invoke(app, ["focus", "--set", WS, "--clear"]).exit_code == 1
    assert _invoke(["focus", "--clear"]).output.strip() == "Focus cleared."
    assert _invoke(["focus"]).output.strip() == "No focus set for this repo."


# ---------------------------------------------------------------------------
# Commit messages and history filters
# ---------------------------------------------------------------------------


def test_commitmsg_helpers() -> None:
    assert commitmsg.build_message("update", "status done", "p/i") == "update(p/i): status done"
    assert commitmsg.build_message("archive", "2 empty\nproject(s)") == "archive: 2 empty project(s)"
    with pytest.raises(ValueError):
        commitmsg.build_message("nope", "x")
    parsed = commitmsg.parse_message("edit(learning/dsp/aliasing): status done")
    assert parsed == commitmsg.ParsedMessage("edit", "learning/dsp/aliasing", "status done")
    assert commitmsg.parse_message("archive: 3 empty project(s)").scope is None
    assert commitmsg.parse_message("Mark x done via dashboard") is None
    assert commitmsg.parse_message("fix(p): not a workbench kind") is None
    assert commitmsg.scope_for_paths(["projects/a/project.yaml"]) == "a"
    assert commitmsg.scope_for_paths(["learning/l/path.yaml", "learning/l/topics/t.md"]) == "learning/l"
    assert commitmsg.scope_for_paths(["projects/a/project.yaml", "projects/b/project.yaml"]) is None
    assert commitmsg.review_message("Record session", "a") == "review(a): Record session"
    assert commitmsg.review_message("Record session", None) == "review: Record session"
    assert commitmsg.review_message("update(a/b): status done", "a") == "update(a/b): status done"
    assert commitmsg.scope_matches("p/item", "p") and commitmsg.scope_matches("p", "p")
    assert not commitmsg.scope_matches("p-2/item", "p")
    assert not commitmsg.scope_matches(None, "p")


def test_commit_kinds_and_history_filters(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    data, ws_repo = demo["data"], demo["ws_repo"]
    monkeypatch.chdir(ws_repo)

    _invoke(["new", "Lab Notebook"])
    assert _remote_subjects(data) == ["create(lab-notebook): new project Lab Notebook"]
    _invoke(["new", "Kalman Filters", "--learning"])
    assert _remote_subjects(data) == ["create(learning/kalman-filters): new learning path Kalman Filters"]
    _invoke(["set", WS, "radio-tx", "--status", "done"])

    # push: free text gets review(<project>); a message with a known kind is kept
    wt_file = Path(_invoke(["begin"]).output.strip())
    wt_file.write_text(wt_file.read_text(encoding="utf-8").replace(
        "Weather Station", "Weather Station Mk2", 1), encoding="utf-8")
    _invoke(["push", str(wt_file), "-m", "Reconcile after radio work"])
    assert _remote_subjects(data) == [f"review({WS}): Reconcile after radio work"]

    _invoke(["attach", "--id", "demo-budget-cli", "--parent", WS, "--under", "enclosure"])
    assert _remote_subjects(data) == [f"attach(demo-budget-cli): under {WS}/enclosure"]
    _invoke(["detach", "--id", "demo-budget-cli"])
    assert _remote_subjects(data) == [f"detach(demo-budget-cli): from {WS}"]

    # undo reverts and keeps the reverted commit's scope
    detach_sha = _remote_sha(data)
    _invoke(["undo", detach_sha])
    assert _remote_subjects(data) == [
        f"undo(demo-budget-cli): revert {detach_sha[:8]} (detach(demo-budget-cli): from {WS})"
    ]

    def history(*args: str) -> list[str]:
        return [line.split(" ", 1)[1] for line in _invoke(["history", *args]).output.splitlines()]

    assert history("--kind", "update") == [f"update({WS}/radio-tx): status done"]
    assert history("--kind", "create,review") == [
        f"review({WS}): Reconcile after radio work",
        "create(learning/kalman-filters): new learning path Kalman Filters",
        "create(lab-notebook): new project Lab Notebook",
    ]
    assert history("--project", WS) == [
        f"review({WS}): Reconcile after radio work",
        f"update({WS}/radio-tx): status done",
    ]
    assert history("--project", f"{WS}/radio-tx") == [f"update({WS}/radio-tx): status done"]
    assert history("--project", "learning/kalman-filters") == [
        "create(learning/kalman-filters): new learning path Kalman Filters"
    ]
    assert history("--project", "demo-budget-cli", "-n", "2") == [
        f"undo(demo-budget-cli): revert {detach_sha[:8]} (detach(demo-budget-cli): from {WS})",
        f"detach(demo-budget-cli): from {WS}",
    ]
    # Unfiltered history keeps the old --oneline output, legacy messages included
    assert "Seed initial demo Workbench metadata" in _invoke(["history", "-n", "50"]).output
    bad = runner.invoke(app, ["history", "--kind", "bogus"])
    assert bad.exit_code == 1 and "unknown kind" in bad.output


# ---------------------------------------------------------------------------
# Hook decisions
# ---------------------------------------------------------------------------


def test_hook_decisions_review_update_no_loop_and_speed(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    home, ws_repo = demo["home"], demo["ws_repo"]
    payload = json.dumps({"session_id": "s-1", "cwd": str(ws_repo), "stop_hook_active": False})
    timings: list[float] = []

    def stop() -> dict | None:
        t0 = time.perf_counter()
        out = evaluate_hook_input(payload, home_dir=home)
        timings.append(time.perf_counter() - t0)
        return json.loads(out) if out else None

    # Never seen -> review
    out = stop()
    assert out and out["decision"] == "block" and "workbench-review skill" in out["reason"]
    assert stop() is None  # ignored prompt: same state, no loop

    # Seen and nothing changed -> allow
    assert mark_repo_seen(ws_repo, home_dir=home)
    assert stop() is None

    # A file edit -> update reason with the focus hint
    monkeypatch.chdir(ws_repo)
    _invoke(["focus", "--set", f"{WS}/radio-tx"])
    (ws_repo / "README.md").write_text("changed\n", encoding="utf-8")
    out = stop()
    assert out is not None
    assert out["reason"] == (
        "Before finishing, run the workbench-update skill: record what this message changed "
        f"in Workbench (focus: {WS}/radio-tx), or run `workbench hook-check --mark-seen` if "
        "nothing worth recording changed."
    )
    assert stop() is None  # agent ignores it: not prompted again for the same state

    # The state changes again (untracked file, then an edit to it) -> prompted again each time
    (ws_repo / "notes.txt").write_text("a\n", encoding="utf-8")
    assert "workbench-update" in stop()["reason"]
    (ws_repo / "notes.txt").write_text("ab\n", encoding="utf-8")
    assert "workbench-update" in stop()["reason"]

    # hook-check --mark-seen -> allow
    _invoke(["hook-check", "--mark-seen"])
    assert stop() is None

    # A new commit -> review (with focus hint), once
    commit_all(ws_repo, "Telemetry work")
    out = stop()
    assert out["reason"] == (
        "Before finishing, run the workbench-review skill: a new commit landed, so reconcile "
        f"this project's Workbench entry (focus: {WS}/radio-tx)."
    )
    assert stop() is None
    assert max(timings) < 1.0, timings


def test_hook_upgrade_from_state_without_fingerprints(demo: dict) -> None:
    """Old state.json (last_seen_commit only): no prompt right after upgrade, even on a dirty tree."""
    home, ws_repo = demo["home"], demo["ws_repo"]
    root, head = get_root_commit(ws_repo), get_head_commit(ws_repo)
    (ws_repo / "README.md").write_text("dirty before upgrade\n", encoding="utf-8")
    state_path = home / ".workbench" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({
        "repos": {root: {"last_seen_commit": head, "updated_at": 1.0}},
        "sessions": {"old": {"prompted_at": time.time(), "prompted_head": head, "root_commit": root}},
    }), encoding="utf-8")

    def stop(sid: str) -> str | None:
        return evaluate_hook_input(
            json.dumps({"session_id": sid, "cwd": str(ws_repo), "stop_hook_active": False}), home_dir=home
        )

    assert stop("new") is None
    assert json.loads(state_path.read_text())["repos"][root]["last_seen_fingerprint"].startswith(head)
    (ws_repo / "README.md").write_text("edited after upgrade\n", encoding="utf-8")
    assert "workbench-update" in json.loads(stop("new"))["reason"]
    # An old session entry (prompted_head only) at a different state is prompted normally
    assert stop("old") is not None


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


def test_installer_installs_both_skills_and_removes_only_our_old_ones(tmp_path: Path) -> None:
    home = tmp_path / "home"
    skills = home / ".claude" / "skills"
    commands = home / ".gemini" / "commands"
    (skills / "workbench").mkdir(parents=True)
    (skills / "workbench" / "SKILL.md").write_text(
        "---\nname: workbench\ndescription: old\n---\nbody\n", encoding="utf-8"
    )
    commands.mkdir(parents=True)
    (commands / "workbench.toml").write_text(
        "description = \"Record this session's work and remaining tasks in Workbench\"\n\nprompt = \"x\"\n",
        encoding="utf-8",
    )
    run_install(home, claude=True, gemini=True)

    repo_root = Path(__file__).resolve().parent.parent
    for name in ("workbench-update", "workbench-review"):
        installed = skills / name / "SKILL.md"
        assert installed.read_text() == (repo_root / "src" / "workbench" / "agents" / "assets" / "claude" / name / "SKILL.md").read_text()
        assert (commands / f"{name}.toml").read_text() == (
            repo_root / "src" / "workbench" / "agents" / "assets" / "gemini" / "commands" / f"{name}.toml"
        ).read_text()
    assert not (skills / "workbench").exists()
    assert not (commands / "workbench.toml").exists()

    # A user's own skill/command with the same names is never touched
    home2 = tmp_path / "home2"
    own_skill = home2 / ".claude" / "skills" / "workbench" / "SKILL.md"
    own_skill.parent.mkdir(parents=True)
    own_skill.write_text("---\nname: my-workbench\n---\nmine\n", encoding="utf-8")
    own_cmd = home2 / ".gemini" / "commands" / "workbench.toml"
    own_cmd.parent.mkdir(parents=True)
    own_cmd.write_text('description = "My own thing"\nprompt = "y"\n', encoding="utf-8")
    run_install(home2, claude=True, gemini=True)
    assert own_skill.read_text() == "---\nname: my-workbench\n---\nmine\n"
    assert own_cmd.exists()

    # Our old skill next to other files: SKILL.md goes, the folder (with the user's file) stays
    home3 = tmp_path / "home3"
    old = home3 / ".claude" / "skills" / "workbench"
    old.mkdir(parents=True)
    (old / "SKILL.md").write_text("---\nname: workbench\n---\n", encoding="utf-8")
    (old / "extra.md").write_text("keep", encoding="utf-8")
    run_install(home3, claude=True)
    assert not (old / "SKILL.md").exists() and (old / "extra.md").exists()
