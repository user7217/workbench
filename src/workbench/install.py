"""Installer for Claude skill/hook, Gemini CLI rules/command/hook, and macOS launchd job (§8, §9)."""

from __future__ import annotations

import json
import plistlib
import shutil
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLAUDE_SKILL_TEMPLATE = REPO_ROOT / "agent" / "claude" / "SKILL.md"
GEMINI_MD_TEMPLATE = REPO_ROOT / "agent" / "gemini" / "GEMINI.md"
GEMINI_CMD_TEMPLATE = REPO_ROOT / "agent" / "gemini" / "commands" / "workbench.toml"

WORKBENCH_HOOK_CMD = "workbench hook-check"
LAUNCHD_LABEL = "com.workbench.discover"


def _merge_hook_into_settings(
    settings_path: Path,
    event_name: str,
    hook_cmd: str = WORKBENCH_HOOK_CMD,
) -> None:
    """Idempotently merge a command hook into a JSON settings file without removing existing hooks."""
    data: dict[str, Any] = {}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except json.JSONDecodeError:
            data = {}

    hooks_obj = data.setdefault("hooks", {})
    if not isinstance(hooks_obj, dict):
        hooks_obj = {}
        data["hooks"] = hooks_obj

    event_list = hooks_obj.setdefault(event_name, [])
    if not isinstance(event_list, list):
        event_list = []
        hooks_obj[event_name] = event_list

    # Check if hook_cmd is already registered in any matcher group for event_name
    already_present = False
    for group in event_list:
        if not isinstance(group, dict):
            continue
        inner_hooks = group.get("hooks", [])
        if not isinstance(inner_hooks, list):
            continue
        for h in inner_hooks:
            if isinstance(h, dict) and h.get("type") == "command" and h.get("command") == hook_cmd:
                already_present = True
                break
        if already_present:
            break

    if not already_present:
        event_list.append(
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": hook_cmd,
                    }
                ],
            }
        )

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def install_claude_integration(home_dir: Path, workbench_bin: str = "workbench") -> list[Path]:
    """Install Claude Code skill and Stop hook into <home_dir>/.claude."""
    claude_dir = home_dir / ".claude"
    skill_dest = claude_dir / "skills" / "workbench" / "SKILL.md"
    skill_dest.parent.mkdir(parents=True, exist_ok=True)
    skill_dest.write_text(CLAUDE_SKILL_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    settings_path = claude_dir / "settings.json"
    hook_cmd = f"{workbench_bin} hook-check" if workbench_bin != "workbench" else WORKBENCH_HOOK_CMD
    _merge_hook_into_settings(settings_path, event_name="Stop", hook_cmd=hook_cmd)
    return [skill_dest, settings_path]


def install_gemini_integration(home_dir: Path, workbench_bin: str = "workbench") -> list[Path]:
    """Install Gemini CLI GEMINI.md section, /workbench command, and AfterAgent hook into <home_dir>/.gemini."""
    gemini_dir = home_dir / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)

    # 1. Merge section into GEMINI.md idempotently
    gemini_md_path = gemini_dir / "GEMINI.md"
    section_text = GEMINI_MD_TEMPLATE.read_text(encoding="utf-8").strip()
    if gemini_md_path.exists():
        existing = gemini_md_path.read_text(encoding="utf-8")
        if "<!-- WORKBENCH:START -->" in existing and "<!-- WORKBENCH:END -->" in existing:
            start_idx = existing.index("<!-- WORKBENCH:START -->")
            end_idx = existing.index("<!-- WORKBENCH:END -->") + len("<!-- WORKBENCH:END -->")
            updated = existing[:start_idx] + section_text + existing[end_idx:]
            gemini_md_path.write_text(updated, encoding="utf-8")
        else:
            sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
            gemini_md_path.write_text(existing + sep + section_text + "\n", encoding="utf-8")
    else:
        gemini_md_path.write_text(section_text + "\n", encoding="utf-8")

    # 2. Install ~/.gemini/commands/workbench.toml
    cmd_dest = gemini_dir / "commands" / "workbench.toml"
    cmd_dest.parent.mkdir(parents=True, exist_ok=True)
    cmd_dest.write_text(GEMINI_CMD_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    # 3. Install AfterAgent hook in ~/.gemini/settings.json
    settings_path = gemini_dir / "settings.json"
    hook_cmd = f"{workbench_bin} hook-check" if workbench_bin != "workbench" else WORKBENCH_HOOK_CMD
    _merge_hook_into_settings(settings_path, event_name="AfterAgent", hook_cmd=hook_cmd)

    return [gemini_md_path, cmd_dest, settings_path]


def install_launchd_job(home_dir: Path, workbench_bin: str | None = None) -> Path:
    """Write macOS LaunchAgent plist to <home_dir>/Library/LaunchAgents/com.workbench.discover.plist (§9.4)."""
    exe = workbench_bin or shutil.which("workbench") or "workbench"
    logs_dir = home_dir / ".workbench" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    plist_dir = home_dir / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LAUNCHD_LABEL}.plist"

    plist_data = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [exe, "discover", "--local"],
        "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": 12, "Minute": 0},
        "StandardOutPath": str(logs_dir / "discover.out.log"),
        "StandardErrorPath": str(logs_dir / "discover.err.log"),
        "EnvironmentVariables": {
            "HOME": str(home_dir),
        },
    }
    with plist_path.open("wb") as f:
        plistlib.dump(plist_data, f)

    return plist_path


def run_install(
    home_dir: Path,
    claude: bool = False,
    gemini: bool = False,
    launchd: bool = False,
    workbench_bin: str = "workbench",
) -> list[Path]:
    """Install selected integrations into home_dir (defaults to all three if none specified)."""
    if not (claude or gemini or launchd):
        claude = gemini = launchd = True

    installed: list[Path] = []
    if claude:
        installed.extend(install_claude_integration(home_dir, workbench_bin=workbench_bin))
    if gemini:
        installed.extend(install_gemini_integration(home_dir, workbench_bin=workbench_bin))
    if launchd:
        installed.append(install_launchd_job(home_dir, workbench_bin=workbench_bin))
    return installed
