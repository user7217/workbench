"""The `web-skill` command: the claude.ai skill for the Workbench connector, as an upload-ready zip."""

from __future__ import annotations

import zipfile
from pathlib import Path

import typer

from workbench.agents.install import ASSETS_DIR

app = typer.Typer()

WEB_SKILL_NAME = "workbench-web"
WEB_SKILL_TEMPLATE = ASSETS_DIR / "web" / "SKILL.md"
# A fixed timestamp keeps the zip identical from one run to the next
_ZIP_DATE = (2026, 1, 1, 0, 0, 0)

NEXT_STEPS = """\
Remaining steps in claude.ai (docs/web-connector.md has the details):
  1. Customize > Connectors > Add custom connector: name it Workbench, enter the server URL
     (WORKBENCH_MCP_PUBLIC_URL followed by /mcp), keep the detected sign-in settings, click Add.
  2. Click Connect and sign in with the allowed GitHub account; approve the consent page.
  3. Customize > Skills: upload {zip_path} and turn the skill on.
  4. In a chat, enable the Workbench connector from the + menu if it is not on already."""


def write_web_skill_zip(out: Path) -> Path:
    """Write the skill zip (a `workbench-web/` folder holding SKILL.md) to `out`."""
    out.parent.mkdir(parents=True, exist_ok=True)
    folder = zipfile.ZipInfo(f"{WEB_SKILL_NAME}/", date_time=_ZIP_DATE)
    folder.external_attr = (0o40755 << 16) | 0x10
    skill = zipfile.ZipInfo(f"{WEB_SKILL_NAME}/SKILL.md", date_time=_ZIP_DATE)
    skill.external_attr = 0o100644 << 16
    skill.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(folder, b"")
        zf.writestr(skill, WEB_SKILL_TEMPLATE.read_bytes())
    return out


@app.command("web-skill")
def cmd_web_skill(
    out: Path = typer.Option(
        Path(f"{WEB_SKILL_NAME}.zip"), "--out", help="Where to write the skill zip"
    ),
) -> None:
    """Write the claude.ai skill for the Workbench connector as a zip ready to upload."""
    path = write_web_skill_zip(out)
    typer.echo(f"Wrote {path}")
    typer.echo(NEXT_STEPS.format(zip_path=path.name))
