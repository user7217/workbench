"""Local configuration management (~/.workbench/config.toml)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


@dataclass
class WorkbenchLocalConfig:
    data_repo_url: str = ""
    branch: str = "main"
    scan_roots: list[str] = field(
        default_factory=lambda: ["~/Documents", "~/code", "~/projects"]
    )
    github_owners: list[str] = field(default_factory=list)
    user_emails: list[str] = field(default_factory=list)
    privacy_review: Literal["auto", "required", "off"] = "auto"
    reviewer_command: str = "claude -p --model haiku"
    github_api_base: str = "https://api.github.com"

    def to_toml(self) -> str:
        def _fmt_str(s: str) -> str:
            escaped = s.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'

        def _fmt_list(items: list[str]) -> str:
            return "[" + ", ".join(_fmt_str(x) for x in items) + "]"

        lines = [
            f"data_repo_url = {_fmt_str(self.data_repo_url)}",
            f"branch = {_fmt_str(self.branch)}",
            f"scan_roots = {_fmt_list(self.scan_roots)}",
            f"github_owners = {_fmt_list(self.github_owners)}",
            f"user_emails = {_fmt_list(self.user_emails)}",
            f"privacy_review = {_fmt_str(self.privacy_review)}",
            f"reviewer_command = {_fmt_str(self.reviewer_command)}",
            f"github_api_base = {_fmt_str(self.github_api_base)}",
        ]
        return "\n".join(lines) + "\n"


def get_workbench_dir(home_dir: Path | None = None, env: dict[str, str] | None = None) -> Path:
    """Return ~/.workbench directory based on explicit home_dir or HOME in env."""
    if home_dir is not None:
        return home_dir / ".workbench"
    lookup_env = os.environ if env is None else env
    home_str = lookup_env.get("HOME")
    if home_str:
        return Path(home_str) / ".workbench"
    return Path.home() / ".workbench"


def load_local_config(
    home_dir: Path | None = None, env: dict[str, str] | None = None
) -> WorkbenchLocalConfig:
    """Load ~/.workbench/config.toml if present, or return defaults."""
    wb_dir = get_workbench_dir(home_dir=home_dir, env=env)
    cfg_path = wb_dir / "config.toml"
    if not cfg_path.exists():
        return WorkbenchLocalConfig()
    data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    review_mode = data.get("privacy_review", "auto")
    if review_mode not in ("auto", "required", "off"):
        review_mode = "auto"
    return WorkbenchLocalConfig(
        data_repo_url=str(data.get("data_repo_url", "")),
        branch=str(data.get("branch", "main")),
        scan_roots=list(data.get("scan_roots", ["~/Documents", "~/code", "~/projects"])),
        github_owners=list(data.get("github_owners", [])),
        user_emails=list(data.get("user_emails", [])),
        privacy_review=review_mode,
        reviewer_command=str(data.get("reviewer_command", "claude -p --model haiku")),
        github_api_base=str(data.get("github_api_base", "https://api.github.com")),
    )


def save_local_config(
    config: WorkbenchLocalConfig,
    home_dir: Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Write config to ~/.workbench/config.toml."""
    wb_dir = get_workbench_dir(home_dir=home_dir, env=env)
    wb_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = wb_dir / "config.toml"
    cfg_path.write_text(config.to_toml(), encoding="utf-8")
    return cfg_path
