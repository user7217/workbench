"""Installer for Claude skill/hook, Gemini CLI rules/command/hook, and macOS launchd job (§8, §9)."""

from __future__ import annotations

import json
import plistlib
import shutil
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLAUDE_SKILL_NAMES: tuple[str, ...] = ("workbench-update", "workbench-review")
CLAUDE_SKILL_TEMPLATES = {
    name: REPO_ROOT / "agent" / "claude" / name / "SKILL.md" for name in CLAUDE_SKILL_NAMES
}
GEMINI_MD_TEMPLATE = REPO_ROOT / "agent" / "gemini" / "GEMINI.md"
GEMINI_CMD_TEMPLATES = {
    f"{name}.toml": REPO_ROOT / "agent" / "gemini" / "commands" / f"{name}.toml"
    for name in CLAUDE_SKILL_NAMES
}

# The single skill / command shipped before the update/review split. They are removed on
# install only when they are recognisably ours, never a user's own file of the same name.
LEGACY_CLAUDE_SKILL_NAME = "workbench"
LEGACY_GEMINI_CMD = "workbench.toml"
LEGACY_GEMINI_DESCRIPTIONS: tuple[str, ...] = (
    "Record this session's work and remaining tasks in Workbench",
)

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


def _frontmatter_name(text: str) -> str | None:
    """The `name:` value of a SKILL.md YAML frontmatter block, if there is one."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        key, sep, value = line.partition(":")
        if sep and key.strip() == "name":
            return value.strip().strip("'\"")
    return None


def _remove_legacy_claude_skill(skills_dir: Path) -> Path | None:
    """Remove the old single `workbench` skill if its frontmatter says it is ours."""
    legacy = skills_dir / LEGACY_CLAUDE_SKILL_NAME / "SKILL.md"
    if not legacy.is_file():
        return None
    try:
        if _frontmatter_name(legacy.read_text(encoding="utf-8")) != LEGACY_CLAUDE_SKILL_NAME:
            return None
    except (OSError, UnicodeDecodeError):
        return None
    legacy.unlink()
    try:
        legacy.parent.rmdir()  # only succeeds when the folder is now empty
    except OSError:
        pass
    return legacy


def _remove_legacy_gemini_command(commands_dir: Path) -> Path | None:
    """Remove the old /workbench command if it still carries the description we shipped."""
    legacy = commands_dir / LEGACY_GEMINI_CMD
    if not legacy.is_file():
        return None
    try:
        text = legacy.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    shipped = {f'description = "{d}"' for d in LEGACY_GEMINI_DESCRIPTIONS}
    if not any(line.strip() in shipped for line in text.splitlines()):
        return None
    legacy.unlink()
    return legacy


def install_claude_integration(home_dir: Path, workbench_bin: str = "workbench") -> list[Path]:
    """Install the workbench-update and workbench-review skills and the Stop hook into <home_dir>/.claude."""
    claude_dir = home_dir / ".claude"
    skills_dir = claude_dir / "skills"
    installed: list[Path] = []
    for name, template in CLAUDE_SKILL_TEMPLATES.items():
        skill_dest = skills_dir / name / "SKILL.md"
        skill_dest.parent.mkdir(parents=True, exist_ok=True)
        skill_dest.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        installed.append(skill_dest)
    _remove_legacy_claude_skill(skills_dir)

    settings_path = claude_dir / "settings.json"
    hook_cmd = f"{workbench_bin} hook-check" if workbench_bin != "workbench" else WORKBENCH_HOOK_CMD
    _merge_hook_into_settings(settings_path, event_name="Stop", hook_cmd=hook_cmd)
    return [*installed, settings_path]


def install_gemini_integration(home_dir: Path, workbench_bin: str = "workbench") -> list[Path]:
    """Install Gemini CLI GEMINI.md section, /workbench-update and /workbench-review commands, and AfterAgent hook."""
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

    # 2. Install ~/.gemini/commands/workbench-update.toml and workbench-review.toml
    commands_dir = gemini_dir / "commands"
    commands_dir.mkdir(parents=True, exist_ok=True)
    cmd_paths: list[Path] = []
    for filename, template in GEMINI_CMD_TEMPLATES.items():
        cmd_dest = commands_dir / filename
        cmd_dest.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        cmd_paths.append(cmd_dest)
    _remove_legacy_gemini_command(commands_dir)

    # 3. Install AfterAgent hook in ~/.gemini/settings.json
    settings_path = gemini_dir / "settings.json"
    hook_cmd = f"{workbench_bin} hook-check" if workbench_bin != "workbench" else WORKBENCH_HOOK_CMD
    _merge_hook_into_settings(settings_path, event_name="AfterAgent", hook_cmd=hook_cmd)

    return [gemini_md_path, *cmd_paths, settings_path]


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
