"""Machine setup commands: install, the go-live wizard and the Today's Work switch."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import typer

from workbench.agents.install import run_install
from workbench.cli._common import _fail
from workbench.config import WorkbenchLocalConfig, load_local_config, save_local_config
from workbench.gitops import clone_repo, ls_remote

app = typer.Typer()


@app.command("install")
def cmd_install(
    home: Path = typer.Option(..., "--home", help="Target home directory for installation"),
    claude: bool = typer.Option(False, "--claude", help="Install Claude skill and Stop hook"),
    gemini: bool = typer.Option(
        False, "--gemini", help="Install Gemini CLI rules, command, and hook"
    ),
    launchd: bool = typer.Option(
        False, "--launchd", help="Install macOS launchd local discovery job"
    ),
) -> None:
    """Install agent skill, hooks, and launchd job into the specified home directory."""
    target_home = home.resolve()
    installed = run_install(
        home_dir=target_home,
        claude=claude,
        gemini=gemini,
        launchd=launchd,
    )
    for p in installed:
        typer.echo(f"Installed: {p}")


@app.command("setup")
def cmd_setup(
    home: Path | None = typer.Option(
        None, "--home", help="Target home directory (defaults to current HOME)"
    ),
) -> None:
    """Interactive go-live wizard."""
    target_home = (
        home.resolve() if home else Path(os.environ.get("HOME", str(Path.home()))).resolve()
    )

    data_repo_url = typer.prompt("Workbench data repository URL").strip()
    owners_raw = typer.prompt("GitHub owner username(s) (comma-separated)").strip()
    emails_raw = typer.prompt("Git author email(s) for local discovery (comma-separated)").strip()
    roots_raw = typer.prompt(
        "Local scan roots (comma-separated)", default="~/Documents, ~/code, ~/projects"
    ).strip()
    llm_review = typer.confirm(
        "Also send changed text to an LLM privacy reviewer (Claude Haiku) on `workbench push`? "
        "The built-in secret and path scan always runs",
        default=False,
    )
    today_on = typer.confirm(
        "Turn on Today's Work? It adds a Today tab holding the work chosen for the day, "
        "with the current and next step highlighted",
        default=_stored_today(target_home) == "on",
    )

    typer.echo(f"Checking repository access for {data_repo_url}...")
    if not ls_remote(data_repo_url):
        typer.echo(
            f"Error: cannot access '{data_repo_url}' via `git ls-remote`. "
            "Verify the repository exists and your SSH key or credential has access.",
            err=True,
        )
        raise typer.Exit(code=2)

    owners = [x.strip() for x in owners_raw.split(",") if x.strip()]
    emails = [x.strip() for x in emails_raw.split(",") if x.strip()]
    roots = [x.strip() for x in roots_raw.split(",") if x.strip()]

    cfg = WorkbenchLocalConfig(
        data_repo_url=data_repo_url,
        branch="main",
        scan_roots=roots,
        github_owners=owners,
        user_emails=emails,
        privacy_review="auto" if llm_review else "off",
        today="on" if today_on else "off",
    )
    cfg_path = save_local_config(cfg, home_dir=target_home)
    typer.echo(f"Wrote configuration to {cfg_path}")

    data_dir = target_home / ".workbench" / "data"
    if not data_dir.exists():
        clone_repo(data_repo_url, data_dir, branch="main")
        typer.echo(f"Cloned data repository to {data_dir}")

    planned_paths = [
        target_home / ".claude" / "skills" / "workbench-update" / "SKILL.md",
        target_home / ".claude" / "skills" / "workbench-review" / "SKILL.md",
        target_home / ".claude" / "settings.json",
        target_home / ".gemini" / "GEMINI.md",
        target_home / ".gemini" / "commands" / "workbench-update.toml",
        target_home / ".gemini" / "commands" / "workbench-review.toml",
        target_home / ".gemini" / "settings.json",
        target_home / "Library" / "LaunchAgents" / "com.workbench.discover.plist",
    ]
    typer.echo("\nWorkbench setup will create or merge the following files:")
    for pp in planned_paths:
        typer.echo(f"  - {pp}")

    confirm = typer.prompt("Type 'yes' to install agent integrations and launchd job").strip()
    if confirm != "yes":
        typer.echo("Skipped agent installation (you can run `workbench install` later).")
        return

    installed = run_install(home_dir=target_home, claude=True, gemini=True, launchd=True)
    for p in installed:
        typer.echo(f"Installed: {p}")
    typer.echo("Setup complete.")


def _stored_today(home_dir: Path) -> str:
    """The `today` setting already in config.toml ("on" without a readable config file)."""
    try:
        return load_local_config(home_dir=home_dir).today
    except (OSError, tomllib.TOMLDecodeError):
        return WorkbenchLocalConfig().today


_TODAY_RESTART_NOTE = (
    "Restart the dashboard server for this to take effect (the desktop app does that itself)."
)


@app.command("today")
def cmd_today(
    state: str | None = typer.Argument(
        None, metavar="[on|off]", help="Turn Today's Work on or off; without it, print the state"
    ),
) -> None:
    """Show whether Today's Work is on, or turn it on or off (stored in ~/.workbench/config.toml)."""
    if state not in (None, "on", "off"):
        raise typer.BadParameter(f"'{state}' is not 'on' or 'off'", param_hint="[on|off]")
    try:
        cfg = load_local_config()
        if state is None:
            typer.echo(cfg.today)
            return
        cfg.today = state
        save_local_config(cfg)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # One line on stderr: the desktop app shows it in a dialog
        _fail(f"cannot read or write ~/.workbench/config.toml: {exc}")
    typer.echo(f"Today's Work is {state}. {_TODAY_RESTART_NOTE}")
