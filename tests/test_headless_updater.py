"""Headless per-message updater: transcript reading, reply parsing, applying a plan, the hook's
headless branch, and `workbench auto-update` end to end with a fake agent command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workbench.agents import updater
from workbench.agents.hooks import evaluate_hook_input
from workbench.cli import app
from workbench.config import load_local_config, save_local_config
from workbench.demo import init_demo_environment
from workbench.gitops import run_git
from workbench.yamlio import load_yaml_str_roundtrip, to_plain_python

runner = CliRunner()
WS = "demo-weather-station"


def _line(entry: dict) -> str:
    return json.dumps(entry) + "\n"


def _user(text, **extra) -> str:
    return _line({"type": "user", "message": {"role": "user", "content": text}, **extra})


def _assistant(text: str, **extra) -> str:
    return _line(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            **extra,
        }
    )


def _tool_result() -> str:
    return _line(
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
        }
    )


# ---------------------------------------------------------------------------
# Reading the last exchange
# ---------------------------------------------------------------------------


def test_claude_transcript_gives_last_user_message_and_its_reply(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        _user("first ask")
        + _assistant("first answer")
        + _user("fix the radio <system-reminder>ignore me</system-reminder>")
        + _assistant("Looking.")
        + _tool_result()
        + _user("meta note", isMeta=True)
        + _assistant("subagent chatter", isSidechain=True)
        + _assistant("Fixed it; tests pass.")
        + "not json\n",
        encoding="utf-8",
    )
    ex = updater.exchange_from_hook({"transcript_path": str(path)})
    assert ex is not None
    assert ex.user == "fix the radio"
    assert ex.reply == "Looking.\n\nFixed it; tests pass."
    assert ex.previous_reply == "first answer"


def test_transcript_size_pins_the_turn_the_hook_fired_for(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_user("message one") + _assistant("reply one"), encoding="utf-8")
    size_at_hook = path.stat().st_size
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            _assistant("reply one, flushed late") + _user("message two") + _assistant("reply two")
        )
    ex = updater.exchange_from_hook({"transcript_path": str(path)}, transcript_size=size_at_hook)
    assert ex is not None
    assert ex.user == "message one"
    assert ex.reply == "reply one\n\nreply one, flushed late"


def test_gemini_payload_and_missing_transcript() -> None:
    ex = updater.exchange_from_hook({"prompt": "add a test", "prompt_response": "Added."})
    assert ex is not None and (ex.user, ex.reply) == ("add a test", "Added.")
    assert updater.exchange_from_hook({"transcript_path": "/nope/missing.jsonl"}) is None
    assert updater.exchange_from_hook({}) is None


def test_cut_keeps_both_ends() -> None:
    text = "A" * 50 + "B" * 50
    out = updater.cut(text, 20)
    assert out.startswith("A" * 8) and out.endswith("B" * 12) and "[... cut ...]" in out
    assert updater.cut("short", 20) == "short"


# ---------------------------------------------------------------------------
# Parsing and applying the agent's reply
# ---------------------------------------------------------------------------


def test_parse_reply_tolerates_fences_and_rejects_garbage() -> None:
    plan = updater.parse_reply(
        '```json\n{"summary": "radio done", "ops": [{"op": "set"}], "suggestions": []}\n```'
    )
    assert plan.summary == "radio done" and len(plan.ops) == 1
    with pytest.raises(updater.UpdaterError):
        updater.parse_reply("no json here")
    with pytest.raises(updater.UpdaterError):
        updater.parse_reply('{"ops": "nope"}')
    many = json.dumps({"ops": [{"op": "set"}] * 20})
    assert len(updater.parse_reply(many).ops) == updater.MAX_OPS


def _doc() -> object:
    return load_yaml_str_roundtrip(
        """\
id: demo
name: Demo
status: active
updated: '2026-10-01'
phases:
  - id: build
    name: Build
    status: in_progress
    components:
      - id: radio
        name: Radio link
        status: in_progress
"""
    )


def test_apply_plan_sets_adds_and_files_suggestions() -> None:
    doc = _doc()
    plan = updater.Plan(
        summary="radio done, retry logic next",
        ops=[
            {"op": "set", "item": "radio", "status": "done", "notes": "Range test passed."},
            {
                "op": "add",
                "under": "radio",
                "name": "Retry logic",
                "summary": "Resend lost packets",
            },
            {"op": "add", "under": "retry-logic", "name": "Backoff timer", "depends_on": ["radio"]},
        ],
        suggestions=[
            {"name": "Solar charging", "summary": "Run without mains", "under": "build"},
            {"name": "Radio link"},  # already an item
        ],
    )
    applied = updater.apply_plan(doc, "demo", plan, "2026-10-29")
    plain = to_plain_python(doc)
    radio = plain["phases"][0]["components"][0]
    assert radio["status"] == "done" and radio["notes"] == "Range test passed."
    retry = radio["components"][0]
    assert (retry["id"], retry["status"]) == ("retry-logic", "not_started")
    assert retry["components"][0]["depends_on"] == ["radio"]
    assert plain["suggestions"] == [
        {
            "id": "solar-charging",
            "name": "Solar charging",
            "summary": "Run without mains",
            "under": "build",
            "added": "2026-10-29",
        }
    ]
    assert applied.changes == [
        "radio: done, notes updated",
        "add retry-logic under radio",
        "add backoff-timer under retry-logic",
        "suggest solar-charging",
    ]
    assert len(applied.skipped) == 1 and "already on the dashboard" in applied.skipped[0]
    assert updater.commit_scope_and_summary("demo", plan, applied) == (
        "demo",
        "radio done, retry logic next",
    )


def test_apply_plan_skips_anything_but_set_and_add_on_existing_items() -> None:
    doc = _doc()
    plan = updater.Plan(
        summary="",
        ops=[
            {"op": "remove", "item": "radio"},
            {"op": "set", "item": "radio", "name": "Renamed"},  # renaming is not allowed
            {"op": "set", "item": "ghost", "status": "done"},
            {"op": "set", "item": "radio", "status": "finished"},
            {"op": "add", "name": "Top level phase"},  # needs an existing parent
            {"op": "add", "under": "build", "name": "Radio link"},  # duplicate name
            {"op": "set", "item": "radio", "notes": "x" * 5000},
        ],
        suggestions=[{"name": "Somewhere", "under": "ghost"}],
    )
    applied = updater.apply_plan(doc, "demo", plan, "2026-10-29")
    plain = to_plain_python(doc)
    radio = plain["phases"][0]["components"][0]
    assert radio["name"] == "Radio link" and radio["status"] == "in_progress"
    assert len(radio["notes"]) == 1000 and radio["notes"].endswith("...")
    assert "suggestions" not in plain
    assert len(applied.skipped) == 7
    assert applied.changes == ["radio: notes updated"]
    assert updater.commit_scope_and_summary("demo", plan, applied) == (
        "demo/radio",
        "radio: notes updated",
    )


# ---------------------------------------------------------------------------
# Hook: headless branch
# ---------------------------------------------------------------------------


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
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


def _set_mode(home: Path, mode: str, command: str | None = None) -> None:
    cfg = load_local_config(home_dir=home)
    cfg.update_mode = mode  # type: ignore[assignment]
    if command is not None:
        cfg.updater_command = command
    save_local_config(cfg, home_dir=home)


def test_headless_hook_starts_one_job_per_change_and_never_blocks(
    demo: dict, tmp_path: Path
) -> None:
    home, repo = demo["home"], demo["ws_repo"]
    _set_mode(home, "headless", f"{sys.executable} -c pass")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_user("hi") + _assistant("hello"), encoding="utf-8")
    payload = json.dumps(
        {
            "session_id": "s1",
            "cwd": str(repo),
            "transcript_path": str(transcript),
            "stop_hook_active": False,
        }
    )
    jobs: list[dict] = []

    def hook() -> str | None:
        return evaluate_hook_input(
            payload, home_dir=home, launcher=lambda job, h, e: jobs.append(job)
        )

    # Never seen: a job starts, the session is not blocked
    assert hook() is None
    assert len(jobs) == 1
    assert jobs[0]["transcript_size"] == transcript.stat().st_size
    assert jobs[0]["repo_top"] == str(repo.resolve()) or Path(jobs[0]["repo_top"]).samefile(repo)

    # Same state again: nothing to do
    assert hook() is None
    assert len(jobs) == 1

    # A file changes: one more job, previous HEAD passed along; a new commit too (no review prompt)
    (repo / "notes.txt").write_text("x", encoding="utf-8")
    assert hook() is None
    assert len(jobs) == 2
    run_git(["add", "notes.txt"], cwd=repo)
    run_git(["commit", "-m", "notes"], cwd=repo)
    assert hook() is None
    assert len(jobs) == 3
    assert jobs[2]["previous_commit"] == jobs[1]["head"]


def test_headless_hook_guards_and_fallbacks(demo: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    home, repo = demo["home"], demo["ws_repo"]
    payload = json.dumps({"session_id": "s2", "cwd": str(repo), "stop_hook_active": False})
    jobs: list[dict] = []
    launcher = lambda job, h, e: jobs.append(job)  # noqa: E731

    # Inside the updater's own agent run: never act
    _set_mode(home, "headless", f"{sys.executable} -c pass")
    monkeypatch.setenv(updater.UPDATER_ENV_VAR, "1")
    assert evaluate_hook_input(payload, home_dir=home, launcher=launcher) is None
    monkeypatch.delenv(updater.UPDATER_ENV_VAR)
    assert jobs == []

    # Off: nothing at all
    _set_mode(home, "off")
    assert evaluate_hook_input(payload, home_dir=home, launcher=launcher) is None
    assert jobs == []

    # Headless with a command that is not installed: falls back to asking the session
    _set_mode(home, "headless", "no-such-agent-cli -p")
    out = evaluate_hook_input(payload, home_dir=home, launcher=launcher)
    assert out is not None and json.loads(out)["decision"] == "block"
    assert jobs == []


# ---------------------------------------------------------------------------
# workbench auto-update end to end
# ---------------------------------------------------------------------------


def _fake_agent(tmp_path: Path, reply: dict) -> str:
    """An updater command that ignores the prompt (saved for inspection) and prints `reply`."""
    script = tmp_path / "fake_agent.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path({str(tmp_path / 'prompt.txt')!r}).write_text(sys.stdin.read())\n"
        f"print({json.dumps(reply)!r})\n",
        encoding="utf-8",
    )
    return f"{sys.executable} {script}"


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


def test_auto_update_job_records_the_message_in_one_commit(
    demo: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import workbench.cli.agents as cli

    monkeypatch.setattr(cli, "JOB_START_DELAY_SECONDS", 0)
    home, repo, data = demo["home"], demo["ws_repo"], demo["data"]
    reply = {
        "summary": "radio range test passed",
        "ops": [
            {
                "op": "set",
                "item": "radio-tx",
                "status": "done",
                "notes": "Range test passed; next: duty cycle",
            }
        ],
        "suggestions": [
            {"name": "Solar charging", "summary": "Run without mains", "under": "enclosure"}
        ],
    }
    _set_mode(home, "headless", _fake_agent(tmp_path, reply))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        _user("run the range test") + _assistant("Range test passed."), encoding="utf-8"
    )
    (repo / "radio.c").write_text("// tx\n", encoding="utf-8")

    job = home / ".workbench" / "jobs" / "1.json"
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_text(
        json.dumps(
            {
                "payload": {"transcript_path": str(transcript)},
                "repo_top": str(repo),
                "focus": None,
                "previous_commit": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)  # the job must not depend on the cwd
    res = runner.invoke(app, ["auto-update", "--job", str(job)])
    assert res.exit_code == 0, res.output
    assert not job.exists()

    log = (home / ".workbench" / "logs" / "updater.log").read_text(encoding="utf-8")
    assert f"{WS} update({WS}/radio-tx): radio range test passed (" in log

    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert "run the range test" in prompt and "Range test passed." in prompt
    assert "id: radio-tx" in prompt  # the full entry
    assert "radio.c" in prompt  # repo changes

    proj = _remote_project(data, WS)
    radio = _find(
        proj["phases"][0]["components"]
        + [c for p in proj["phases"] for c in p.get("components", [])],
        "radio-tx",
    )
    assert radio is not None and radio["status"] == "done"
    assert [s["id"] for s in proj["suggestions"]] == ["solar-charging"]
    subject = run_git(["log", "-n1", "--format=%s", "origin/main"], cwd=data).stdout.strip()
    assert subject == f"update({WS}/radio-tx): radio range test passed"


def test_auto_update_dry_run_and_nothing_to_record(
    demo: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, repo, data = demo["home"], demo["ws_repo"], demo["data"]
    before = run_git(["rev-parse", "origin/main"], cwd=data).stdout.strip()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(_user("rename a variable") + _assistant("Renamed."), encoding="utf-8")
    monkeypatch.chdir(repo)

    _set_mode(
        home,
        "headless",
        _fake_agent(
            tmp_path, {"summary": "x", "ops": [{"op": "set", "item": "radio-tx", "status": "done"}]}
        ),
    )
    res = runner.invoke(app, ["auto-update", "--transcript", str(transcript), "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "--- prompt" in res.output and "would record: radio-tx: done" in res.output

    _set_mode(
        home, "headless", _fake_agent(tmp_path, {"summary": "", "ops": [], "suggestions": []})
    )
    res = runner.invoke(app, ["auto-update", "--transcript", str(transcript)])
    assert res.exit_code == 0, res.output
    assert "Workbench: no update needed" in res.output

    run_git(["fetch", "origin", "main"], cwd=data)
    assert run_git(["rev-parse", "origin/main"], cwd=data).stdout.strip() == before
