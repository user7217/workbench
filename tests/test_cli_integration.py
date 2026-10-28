"""Integration tests for CLI commands, concurrent worktrees, rebase conflicts, push race retries, LLM reviewer, and undo (§14)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from typer.testing import CliRunner

from workbench.cli import app
from workbench.config import load_local_config, save_local_config
from workbench.demo_env import init_demo_environment
from workbench.gitops import commit_all, create_worktree, push_head_to_branch, remove_worktree, run_git
from workbench.validate import validate_data_repo
from workbench.yamlio import (
    dump_yaml_roundtrip,
    load_yaml_roundtrip,
    load_yaml_str_roundtrip,
    to_plain_python,
)

runner = CliRunner()


def test_cli_begin_push_concurrent_and_conflict_and_undo(
    tmp_path: Path, monkeypatch: any
) -> None:
    """Test begin/push, two concurrent begins, conflicting edits to same project, --allow-removals, and undo (§14)."""
    demo_dir = tmp_path / "demo"
    info = init_demo_environment(demo_dir)
    laptop_home = Path(info["laptop_home"])
    monkeypatch.setenv("HOME", str(laptop_home))

    # 1. Two concurrent `begin`s on different projects both succeed
    r_begin1 = runner.invoke(app, ["begin", "--id", "demo-weather-station"])
    assert r_begin1.exit_code == 0, r_begin1.output
    wt_file1 = Path(r_begin1.stdout.strip())
    assert wt_file1.exists()

    r_begin2 = runner.invoke(app, ["begin", "--id", "demo-budget-cli"])
    assert r_begin2.exit_code == 0, r_begin2.output
    wt_file2 = Path(r_begin2.stdout.strip())
    assert wt_file2.exists()
    assert wt_file1.parent.parent.parent != wt_file2.parent.parent.parent

    # Edit both and push both
    doc1 = load_yaml_roundtrip(wt_file1)
    doc1["description"] = "Updated solar weather station description."
    dump_yaml_roundtrip(doc1, wt_file1)

    doc2 = load_yaml_roundtrip(wt_file2)
    doc2["description"] = "Updated budget CLI description."
    dump_yaml_roundtrip(doc2, wt_file2)

    r_push1 = runner.invoke(app, ["push", str(wt_file1), "-m", "Update weather station"])
    assert r_push1.exit_code == 0, r_push1.output
    assert not wt_file1.parent.parent.parent.exists()  # worktree removed on success

    r_push2 = runner.invoke(app, ["push", str(wt_file2), "-m", "Update budget cli"])
    assert r_push2.exit_code == 0, r_push2.output
    assert not wt_file2.parent.parent.parent.exists()

    # 2. Conflicting edit to the same project fails cleanly on the second push and keeps its worktree
    ca = runner.invoke(app, ["begin", "--id", "demo-weather-station"])
    cb = runner.invoke(app, ["begin", "--id", "demo-weather-station"])
    file_a = Path(ca.stdout.strip())
    file_b = Path(cb.stdout.strip())
    wt_b = file_b.parent.parent.parent

    doc_a = load_yaml_roundtrip(file_a)
    doc_a["description"] = "Conflict edit A on description line."
    dump_yaml_roundtrip(doc_a, file_a)

    doc_b = load_yaml_roundtrip(file_b)
    doc_b["description"] = "Conflict edit B on the exact same description line."
    dump_yaml_roundtrip(doc_b, file_b)

    push_a = runner.invoke(app, ["push", str(file_a), "-m", "Edit A"])
    assert push_a.exit_code == 0, push_a.output

    push_b = runner.invoke(app, ["push", str(file_b), "-m", "Edit B"])
    assert push_b.exit_code != 0
    assert "Rebase conflict" in push_b.output
    assert "workbench begin" in push_b.output
    assert wt_b.exists(), "Worktree must be kept when rebase conflict occurs"

    # 3. Removing a component fails without --allow-removals and succeeds with --allow-removals
    r_rem = runner.invoke(app, ["begin", "--id", "demo-budget-cli"])
    rem_file = Path(r_rem.stdout.strip())
    rem_doc = load_yaml_roundtrip(rem_file)
    rem_doc["phases"][1]["components"] = []
    dump_yaml_roundtrip(rem_doc, rem_file)

    push_no_flag = runner.invoke(app, ["push", str(rem_file), "-m", "Remove tree-formatter"])
    assert push_no_flag.exit_code == 1
    assert "--allow-removals" in push_no_flag.output

    # Now push with --allow-removals
    push_with_flag = runner.invoke(
        app, ["push", str(rem_file), "-m", "Remove tree-formatter", "--allow-removals"]
    )
    assert push_with_flag.exit_code == 0, push_with_flag.output

    # 4. Test `workbench history` and `workbench undo <commit>`
    hist = runner.invoke(app, ["history", "demo-budget-cli"])
    assert hist.exit_code == 0
    latest_commit = hist.stdout.splitlines()[0].split()[0]

    undo_res = runner.invoke(app, ["undo", latest_commit])
    assert undo_res.exit_code == 0, undo_res.output
    assert "Reverted" in undo_res.output

    # Verify component `tree-formatter` is restored after undo
    show_res = runner.invoke(app, ["show", "--id", "demo-budget-cli"])
    assert show_res.exit_code == 0
    assert "tree-formatter" in show_res.stdout


def test_cli_push_race_retries_and_llm_reviewer(
    tmp_path: Path, monkeypatch: any
) -> None:
    """Test push race retry logic and LLM reviewer pass/fail/unavailable behavior (§7.2, §8, §14)."""
    demo_dir = tmp_path / "demo_reviewer"
    info = init_demo_environment(demo_dir)
    laptop_home = Path(info["laptop_home"])
    monkeypatch.setenv("HOME", str(laptop_home))

    # 1. Create a fake reviewer script that returns {"verdict": "fail", ...}
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    fail_reviewer = bin_dir / "fake-reviewer-fail"
    fail_reviewer.write_text(
        '#!/bin/sh\ncat >/dev/null\necho \'{"verdict": "fail", "issues": [{"field": "notes", "reason": "Contains internal client codename"}]}\'\n',
        encoding="utf-8",
    )
    fail_reviewer.chmod(fail_reviewer.stat().st_mode | stat.S_IEXEC)

    pass_reviewer = bin_dir / "fake-reviewer-pass"
    pass_reviewer.write_text(
        '#!/bin/sh\ncat >/dev/null\necho \'{"verdict": "pass", "issues": []}\'\n',
        encoding="utf-8",
    )
    pass_reviewer.chmod(pass_reviewer.stat().st_mode | stat.S_IEXEC)

    cfg = load_local_config(home_dir=laptop_home)
    cfg.privacy_review = "required"
    cfg.reviewer_command = str(fail_reviewer)
    save_local_config(cfg, home_dir=laptop_home)

    r_begin = runner.invoke(app, ["begin", "--id", "demo-weather-station"])
    wt_file = Path(r_begin.stdout.strip())
    doc = load_yaml_roundtrip(wt_file)
    doc["phases"][0]["notes"] = "Updated notes for project."
    dump_yaml_roundtrip(doc, wt_file)

    # Reviewer fail verdict blocks push
    r_fail = runner.invoke(app, ["push", str(wt_file), "-m", "Try push with failing review"])
    assert r_fail.exit_code == 1
    assert "Contains internal client codename" in r_fail.output

    # Reviewer unavailable in 'auto' warns and continues
    cfg.privacy_review = "auto"
    cfg.reviewer_command = "nonexistent-reviewer-binary-xyz"
    save_local_config(cfg, home_dir=laptop_home)

    # Simulate a push race on the first push_head_to_branch call to test retry loop
    import workbench.cli as cli_mod

    real_push = cli_mod.push_head_to_branch
    call_count = {"n": 0}

    def flaky_push(wt_path: Path, branch: str = "main", env: dict | None = None) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return False  # Simulate race on first attempt
        return real_push(wt_path, branch=branch, env=env)

    monkeypatch.setattr(cli_mod, "push_head_to_branch", flaky_push)

    r_auto = runner.invoke(app, ["push", str(wt_file), "-m", "Push with auto warning and retry"])
    assert r_auto.exit_code == 0, r_auto.output
    assert "skipping LLM review" in r_auto.output
    assert call_count["n"] == 2  # Verified retry happened!


def test_archive_empty_archives_only_empty_projects_in_one_commit(
    tmp_path: Path, monkeypatch: any
) -> None:
    """archive-empty archives active discovered projects with no phases and no topics, honours --keep and --dry-run."""
    info = init_demo_environment(tmp_path / "demo_archive")
    laptop_data = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(info["laptop_home"]))

    # Seed one more empty active project and one empty project that is already archived
    wt_seed = tmp_path / "wt_seed"
    create_worktree(laptop_data, wt_seed, ref="origin/main")
    seeds = [
        ("empty-keep-me", "active", "discovered"),
        ("empty-already-archived", "archived", "discovered"),
        ("empty-manual", "active", "manual"),
    ]
    for pid, status, origin in seeds:
        pdir = wt_seed / "projects" / pid
        pdir.mkdir(parents=True)
        (pdir / "project.yaml").write_text(
            f"schema_version: 1\nid: {pid}\nname: {pid}\ndescription: ''\nstatus: {status}\n"
            f"origin: {origin}\nupdated: 2026-10-20\nrepos: []\nlinks: []\nphases: []\ntopics: []\n",
            encoding="utf-8",
        )
    commit_all(wt_seed, "Seed empty projects")
    assert push_head_to_branch(wt_seed, branch="main")
    remove_worktree(laptop_data, wt_seed)

    def remote_head() -> str:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        return run_git(["rev-parse", "origin/main"], cwd=laptop_data).stdout.strip()

    def remote_status(pid: str) -> str:
        text = run_git(["show", f"origin/main:projects/{pid}/project.yaml"], cwd=laptop_data).stdout
        return next(line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("status:"))

    head_before = remote_head()

    # --dry-run lists what would be archived and changes nothing
    dry = runner.invoke(app, ["archive-empty", "--dry-run", "--keep", "empty-keep-me"])
    assert dry.exit_code == 0, dry.output
    assert "demo-notes-sync" in dry.stdout
    assert "empty-keep-me" not in dry.stdout
    assert "empty-already-archived" not in dry.stdout
    assert "demo-esp32-audio-dsp" not in dry.stdout  # no phases, but has topics
    assert "empty-manual" not in dry.stdout  # created by hand, e.g. with `workbench new`
    assert remote_head() == head_before

    # Real run archives only empty, active, non-kept projects in a single commit
    res = runner.invoke(app, ["archive-empty", "--keep", "empty-keep-me"])
    assert res.exit_code == 0, res.output
    assert remote_status("demo-notes-sync") == "archived"
    assert remote_status("empty-keep-me") == "active"
    assert remote_status("demo-esp32-audio-dsp") == "active"
    assert remote_status("empty-manual") == "active"
    count = run_git(["rev-list", "--count", f"{head_before}..origin/main"], cwd=laptop_data).stdout.strip()
    assert count == "1"

    # Nothing left to archive on a second run
    again = runner.invoke(app, ["archive-empty", "--keep", "empty-keep-me"])
    assert again.exit_code == 0, again.output
    assert "No empty projects" in again.stdout


def test_link_attaches_repo_to_umbrella_project_and_drops_empty_stub(
    tmp_path: Path, monkeypatch: any
) -> None:
    """link --id adds the current repo to a project, removes an empty discovered stub, refuses real conflicts."""
    info = init_demo_environment(tmp_path / "demo_link")
    laptop_data = Path(info["laptop_data_dir"])
    code_dir = Path(info["code_dir"])
    monkeypatch.setenv("HOME", str(info["laptop_home"]))
    cfg = load_local_config()
    cfg.privacy_review = "off"  # `new` runs the full push pipeline; don't call a real reviewer
    save_local_config(cfg)

    def remote_head() -> str:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        return run_git(["rev-parse", "origin/main"], cwd=laptop_data).stdout.strip()

    def remote_file(rel: str) -> str | None:
        proc = run_git(["show", f"origin/main:{rel}"], cwd=laptop_data, check=False)
        return proc.stdout if proc.returncode == 0 else None

    created = runner.invoke(app, ["new", "Field Robotics"])
    assert created.exit_code == 0, created.output

    # Unknown target project is rejected
    monkeypatch.chdir(code_dir / "notes-sync")
    missing = runner.invoke(app, ["link", "--id", "no-such-project"])
    assert missing.exit_code == 1
    assert "workbench new" in missing.output

    # Linking a repo claimed only by an empty discovered stub moves it in one commit
    head_before = remote_head()
    linked = runner.invoke(app, ["link", "--id", "field-robotics"])
    assert linked.exit_code == 0, linked.output
    assert remote_head() != head_before
    assert remote_file("projects/demo-notes-sync/project.yaml") is None
    umbrella = remote_file("projects/field-robotics/project.yaml")
    ns_root = run_git(["rev-list", "--max-parents=0", "HEAD"], cwd=code_dir / "notes-sync").stdout.split()[0]
    assert ns_root in umbrella
    assert "demouser/notes-sync" in umbrella

    # The repo now resolves to the umbrella project
    shown = runner.invoke(app, ["show"])
    assert shown.exit_code == 0, shown.output
    assert "id: field-robotics" in shown.stdout

    # Linking again is a no-op
    head_linked = remote_head()
    again = runner.invoke(app, ["link", "--id", "field-robotics"])
    assert again.exit_code == 0, again.output
    assert "already linked" in again.stdout
    assert remote_head() == head_linked

    # A repo claimed by a project with real content is refused, nothing pushed
    monkeypatch.chdir(code_dir / "weather-station")
    conflict = runner.invoke(app, ["link", "--id", "field-robotics"])
    assert conflict.exit_code == 1
    assert "demo-weather-station" in conflict.output
    assert remote_head() == head_linked


def test_new_attach_detach_child_projects(tmp_path: Path, monkeypatch: any) -> None:
    """new --parent/--under/--status, attach and detach set a child project's single parent safely."""
    info = init_demo_environment(tmp_path / "demo_children")
    laptop_data = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(info["laptop_home"]))
    cfg = load_local_config()
    cfg.privacy_review = "off"  # don't call a real LLM reviewer from tests
    save_local_config(cfg)

    def remote_head() -> str:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        return run_git(["rev-parse", "origin/main"], cwd=laptop_data).stdout.strip()

    def remote_project(pid: str) -> dict:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        text = run_git(["show", f"origin/main:projects/{pid}/project.yaml"], cwd=laptop_data).stdout
        return to_plain_python(load_yaml_str_roundtrip(text))

    head = remote_head()

    # Bad flag combinations and unknown targets are refused before anything is pushed
    only_parent = runner.invoke(app, ["new", "Gripper Lab", "--parent", "demo-robot-arm"])
    assert only_parent.exit_code == 1
    assert "--parent and --under" in only_parent.output
    bad_status = runner.invoke(app, ["new", "Gripper Lab", "--status", "someday"])
    assert bad_status.exit_code == 1
    assert "planned" in bad_status.output
    no_parent = runner.invoke(
        app, ["new", "Gripper Lab", "--parent", "no-such-project", "--under", "mechanics"]
    )
    assert no_parent.exit_code == 1
    assert "no-such-project" in no_parent.output
    no_item = runner.invoke(
        app, ["new", "Gripper Lab", "--parent", "demo-robot-arm", "--under", "nowhere"]
    )
    assert no_item.exit_code == 1
    assert "nowhere" in no_item.output and "demo-robot-arm" in no_item.output
    assert remote_head() == head

    # A planned child project under a component of its parent
    created = runner.invoke(
        app,
        ["new", "Gripper Lab", "--parent", "demo-robot-arm", "--under", "base-turntable",
         "--status", "planned"],
    )
    assert created.exit_code == 0, created.output
    child = remote_project("gripper-lab")
    assert (child["status"], child["parent"], child["under"]) == (
        "planned", "demo-robot-arm", "base-turntable"
    )

    # attach an existing project under a phase of another
    attached = runner.invoke(
        app, ["attach", "--id", "demo-weather-station", "--parent", "demo-robot-arm",
              "--under", "mechanics"],
    )
    assert attached.exit_code == 0, attached.output
    ws = remote_project("demo-weather-station")
    assert (ws["parent"], ws["under"]) == ("demo-robot-arm", "mechanics")
    assert list(ws).index("parent") == list(ws).index("status") + 1

    # Re-attaching to the same place is a no-op
    head_attached = remote_head()
    again = runner.invoke(
        app, ["attach", "--id", "demo-weather-station", "--parent", "demo-robot-arm",
              "--under", "mechanics"],
    )
    assert again.exit_code == 0, again.output
    assert "already under" in again.output
    assert remote_head() == head_attached

    # A cycle (robot-arm under its own child) and unknown items are refused, nothing pushed
    cycle = runner.invoke(
        app, ["attach", "--id", "demo-robot-arm", "--parent", "demo-weather-station",
              "--under", "hardware"],
    )
    assert cycle.exit_code == 1
    assert "cycle" in cycle.output
    assert "demo-robot-arm -> demo-weather-station -> demo-robot-arm" in cycle.output
    self_parent = runner.invoke(
        app, ["attach", "--id", "demo-robot-arm", "--parent", "demo-robot-arm", "--under", "mechanics"]
    )
    assert self_parent.exit_code == 1
    assert "own parent" in self_parent.output
    bad_under = runner.invoke(
        app, ["attach", "--id", "demo-budget-cli", "--parent", "demo-robot-arm", "--under", "nowhere"]
    )
    assert bad_under.exit_code == 1
    assert "nowhere" in bad_under.output
    missing_child = runner.invoke(
        app, ["attach", "--id", "ghost", "--parent", "demo-robot-arm", "--under", "mechanics"]
    )
    assert missing_child.exit_code == 1
    assert "ghost" in missing_child.output
    assert remote_head() == head_attached

    # detach removes both keys; detaching again is a no-op
    detached = runner.invoke(app, ["detach", "--id", "demo-weather-station"])
    assert detached.exit_code == 0, detached.output
    ws = remote_project("demo-weather-station")
    assert "parent" not in ws and "under" not in ws
    head_detached = remote_head()
    detached_again = runner.invoke(app, ["detach", "--id", "demo-weather-station"])
    assert detached_again.exit_code == 0, detached_again.output
    assert "nothing to detach" in detached_again.output
    assert remote_head() == head_detached

    # The whole repo still validates, and no stray worktrees are left behind
    run_git(["reset", "--hard", "origin/main"], cwd=laptop_data)
    assert validate_data_repo(laptop_data).ok
    work_dir = Path(info["laptop_home"]) / ".workbench" / "work"
    assert not work_dir.exists() or not any(work_dir.iterdir())


def test_new_runs_full_push_pipeline(tmp_path: Path, monkeypatch: any) -> None:
    """`new` goes through the shared push pipeline (rebase/retry, repo-wide validation) like push."""
    info = init_demo_environment(tmp_path / "demo_new")
    laptop_home = Path(info["laptop_home"])
    laptop_data = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(laptop_home))

    def remote_head() -> str:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        return run_git(["rev-parse", "origin/main"], cwd=laptop_data).stdout.strip()

    def remote_text(rel: str) -> str | None:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        proc = run_git(["show", f"origin/main:{rel}"], cwd=laptop_data, check=False)
        return proc.stdout if proc.returncode == 0 else None

    cfg = load_local_config(home_dir=laptop_home)
    # A push race is retried and the entry lands with origin=manual, no warnings
    cfg.privacy_review = "off"
    save_local_config(cfg, home_dir=laptop_home)
    import workbench.cli as cli_mod

    real_push = cli_mod.push_head_to_branch
    calls = {"n": 0}

    def flaky_push(wt_path: Path, branch: str = "main", env: dict | None = None) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return False
        return real_push(wt_path, branch=branch, env=env)

    monkeypatch.setattr(cli_mod, "push_head_to_branch", flaky_push)
    created = runner.invoke(app, ["new", "Lab Notebook"])
    assert created.exit_code == 0, created.output
    assert calls["n"] == 2
    assert "Created project 'lab-notebook'" in created.output
    assert "Warning" not in created.output
    proj = to_plain_python(load_yaml_str_roundtrip(remote_text("projects/lab-notebook/project.yaml")))
    assert (proj["origin"], proj["status"]) == ("manual", "active")
    log = run_git(["log", "-1", "--format=%s", "origin/main"], cwd=laptop_data).stdout.strip()
    assert log == "Create project lab-notebook"

    # Learning paths go through the same pipeline
    learning = runner.invoke(app, ["new", "Kalman Filters", "--learning"])
    assert learning.exit_code == 0, learning.output
    assert "Created learning path 'kalman-filters'" in learning.output
    assert remote_text("learning/kalman-filters/path.yaml") is not None

    # Duplicates are refused before anything is pushed
    head = remote_head()
    dup = runner.invoke(app, ["new", "Lab Notebook"])
    assert dup.exit_code == 1
    assert "already exists" in dup.output
    assert remote_head() == head

    run_git(["reset", "--hard", "origin/main"], cwd=laptop_data)
    assert validate_data_repo(laptop_data).ok
