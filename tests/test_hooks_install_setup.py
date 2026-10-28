"""Tests for agent Stop hook, installer, launchd plist, and setup wizard (§14)."""

from __future__ import annotations

import json
import plistlib
import time
from pathlib import Path

from typer.testing import CliRunner

from workbench.cli import app
from workbench.demo_env import init_demo_environment
from workbench.gitops import commit_all
from workbench.hooks import evaluate_hook_input, mark_repo_seen
from workbench.install import run_install

runner = CliRunner()


def test_hook_blocks_once_per_session_allows_active_outside_mark_seen_and_fast(
    tmp_path: Path, monkeypatch: any
) -> None:
    """Hook blocks once per session on new commits or >50 changed lines; allows when stop_hook_active, outside repo, after mark-seen, on error; runs < 1s (§9.3, §14)."""
    demo_dir = tmp_path / "demo_hook"
    info = init_demo_environment(demo_dir)
    laptop_home = Path(info["laptop_home"])
    ws_repo = Path(info["code_dir"]) / "weather-station"
    monkeypatch.setenv("HOME", str(laptop_home))

    # 1. Measure speed & block on first prompt in session when repo has unseen commits
    t0 = time.perf_counter()
    stdin_payload = json.dumps(
        {
            "session_id": "sess-101",
            "cwd": str(ws_repo),
            "stop_hook_active": False,
        }
    )
    out1 = evaluate_hook_input(stdin_payload, home_dir=laptop_home)
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"Hook took {elapsed:.3f}s (> 1s limit)"
    assert out1 is not None
    parsed1 = json.loads(out1)
    assert parsed1["decision"] == "block"
    assert "workbench skill" in parsed1["reason"]

    # 2. Second invocation in the SAME session_id is allowed (prompted once per session)
    out2 = evaluate_hook_input(stdin_payload, home_dir=laptop_home)
    assert out2 is None

    # 3. Allow when stop_hook_active is True even for a brand new session
    out_active = evaluate_hook_input(
        json.dumps(
            {
                "session_id": "sess-102",
                "cwd": str(ws_repo),
                "stop_hook_active": True,
            }
        ),
        home_dir=laptop_home,
    )
    assert out_active is None

    # 4. Allow when cwd is outside a git repository
    non_repo = tmp_path / "not_a_git_repo"
    non_repo.mkdir()
    out_outside = evaluate_hook_input(
        json.dumps(
            {
                "session_id": "sess-103",
                "cwd": str(non_repo),
                "stop_hook_active": False,
            }
        ),
        home_dir=laptop_home,
    )
    assert out_outside is None

    # 5. After mark-seen, a new session is allowed if no new commits or < 50 lines changed
    assert mark_repo_seen(ws_repo, home_dir=laptop_home) is True
    out_seen = evaluate_hook_input(
        json.dumps(
            {
                "session_id": "sess-104",
                "cwd": str(ws_repo),
                "stop_hook_active": False,
            }
        ),
        home_dir=laptop_home,
    )
    assert out_seen is None

    # 6. If > 50 uncommitted lines are added, hook blocks for a new session
    (ws_repo / "big_change.c").write_text("\n".join(f"// line {i}" for i in range(60)) + "\n", encoding="utf-8")
    out_uncommitted = evaluate_hook_input(
        json.dumps(
            {
                "session_id": "sess-105",
                "cwd": str(ws_repo),
                "stop_hook_active": False,
            }
        ),
        home_dir=laptop_home,
    )
    assert out_uncommitted is not None
    assert json.loads(out_uncommitted)["decision"] == "block"

    # 7. Any malformed JSON or internal error fails open (returns None / exit 0)
    assert evaluate_hook_input("{not valid json", home_dir=laptop_home) is None


def test_hook_reprompts_same_session_only_when_head_moved_since_last_push(
    tmp_path: Path, monkeypatch: any
) -> None:
    """Within one session the hook re-prompts after new commits land past the last push/mark-seen, but never loops (§9.3)."""
    info = init_demo_environment(tmp_path / "demo_reprompt")
    laptop_home = Path(info["laptop_home"])
    ws_repo = Path(info["code_dir"]) / "weather-station"
    monkeypatch.setenv("HOME", str(laptop_home))

    payload = json.dumps({"session_id": "sess-201", "cwd": str(ws_repo), "stop_hook_active": False})

    def stop() -> str | None:
        return evaluate_hook_input(payload, home_dir=laptop_home)

    def add_commit(name: str) -> None:
        (ws_repo / f"{name}.c").write_text(f"// {name}\n", encoding="utf-8")
        commit_all(ws_repo, f"Add {name}")

    # First stop with unseen commits prompts; an immediate second stop does not loop
    assert stop() is not None
    assert stop() is None

    # Skill runs and pushes (push marks the repo seen): nothing new, so no prompt
    assert mark_repo_seen(ws_repo, home_dir=laptop_home) is True
    assert stop() is None

    # Later work in the SAME session lands a commit: prompt again
    add_commit("later_work")
    out = stop()
    assert out is not None
    assert json.loads(out)["decision"] == "block"

    # Agent ignores the prompt (no push, no mark-seen): no re-prompt until HEAD moves again
    assert stop() is None
    add_commit("more_work")
    assert stop() is not None

    # Uncommitted edits alone never re-prompt an already-prompted session
    assert mark_repo_seen(ws_repo, home_dir=laptop_home) is True
    (ws_repo / "big_change.c").write_text("\n".join(f"// line {i}" for i in range(60)) + "\n", encoding="utf-8")
    assert stop() is None


def test_installer_merges_existing_hooks_is_idempotent_and_touches_only_target_home(
    tmp_path: Path,
) -> None:
    """Installer merges into existing settings.json without removing other hooks, is idempotent, and touches only --home (§8, §14)."""
    target_home = tmp_path / "custom_home"
    claude_dir = target_home / ".claude"
    claude_dir.mkdir(parents=True)

    # Seed existing settings.json with both a PreToolUse hook and an existing Stop hook
    existing_settings = {
        "theme": "dark",
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": "echo pre-tool"}],
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [{"type": "command", "command": "echo existing-stop-hook"}],
                }
            ],
        },
    }
    settings_file = claude_dir / "settings.json"
    settings_file.write_text(json.dumps(existing_settings, indent=2), encoding="utf-8")

    # Run install twice to verify idempotency
    res1 = runner.invoke(
        app,
        ["install", "--home", str(target_home), "--claude", "--gemini", "--launchd"],
    )
    assert res1.exit_code == 0, res1.output

    res2 = runner.invoke(
        app,
        ["install", "--home", str(target_home), "--claude", "--gemini", "--launchd"],
    )
    assert res2.exit_code == 0, res2.output

    # Verify Claude settings.json preserved existing hooks and added workbench hook exactly once
    merged = json.loads(settings_file.read_text(encoding="utf-8"))
    assert merged["theme"] == "dark"
    assert len(merged["hooks"]["PreToolUse"]) == 1
    stop_groups = merged["hooks"]["Stop"]
    assert len(stop_groups) == 2
    commands = [
        h["command"]
        for g in stop_groups
        for h in g.get("hooks", [])
    ]
    assert commands == ["echo existing-stop-hook", "workbench hook-check"]

    # Verify Claude skill, Gemini files, and launchd plist exist inside target_home
    assert (target_home / ".claude" / "skills" / "workbench" / "SKILL.md").exists()
    assert (target_home / ".gemini" / "GEMINI.md").exists()
    assert (target_home / ".gemini" / "commands" / "workbench.toml").exists()
    assert (target_home / ".gemini" / "settings.json").exists()

    plist_path = target_home / "Library" / "LaunchAgents" / "com.workbench.discover.plist"
    assert plist_path.exists()
    with plist_path.open("rb") as f:
        plist_data = plistlib.load(f)
    assert plist_data["Label"] == "com.workbench.discover"
    assert plist_data["StartCalendarInterval"] == {"Hour": 12, "Minute": 0}


def test_setup_wizard_against_fakes(tmp_path: Path, monkeypatch: any) -> None:
    """Test `workbench setup` against fake bare repo and fake home directory (§12)."""
    demo_dir = tmp_path / "demo_setup"
    info = init_demo_environment(demo_dir)
    remote_bare = info["remote_bare"]

    setup_home = tmp_path / "wizard_home"
    setup_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(setup_home))

    user_input = "\n".join(
        [
            remote_bare,
            "demouser",
            "demo@example.invalid",
            str(info["code_dir"]),
            "yes",
        ]
    ) + "\n"

    res = runner.invoke(app, ["setup", "--home", str(setup_home)], input=user_input)
    assert res.exit_code == 0, res.output
    assert "Setup complete." in res.output
    assert (setup_home / ".workbench" / "config.toml").exists()
    assert (setup_home / ".workbench" / "data" / "workbench.yaml").exists()
    assert (setup_home / ".claude" / "skills" / "workbench" / "SKILL.md").exists()
    assert (setup_home / ".gemini" / "commands" / "workbench.toml").exists()
