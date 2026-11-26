"""`workbench web-skill`: the claude.ai skill zip for the Workbench connector."""

from __future__ import annotations

import zipfile
from pathlib import Path

from typer.testing import CliRunner

import workbench
from workbench.cli import app
from workbench.cli.web import WEB_SKILL_NAME, WEB_SKILL_TEMPLATE


def _frontmatter(text: str) -> dict[str, str]:
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    fields = {}
    for line in block.strip().splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_web_skill_writes_an_uploadable_zip(tmp_path: Path) -> None:
    out = tmp_path / "out" / "skill.zip"
    result = CliRunner().invoke(app, ["web-skill", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "Add custom connector" in result.output and "Customize > Skills" in result.output

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        # claude.ai looks for <skill-name>/SKILL.md, never a SKILL.md at the zip root
        assert all(n.startswith(f"{WEB_SKILL_NAME}/") for n in names)
        assert f"{WEB_SKILL_NAME}/SKILL.md" in names
        text = zf.read(f"{WEB_SKILL_NAME}/SKILL.md").decode("utf-8")

    meta = _frontmatter(text)
    assert set(meta) == {"name", "description"}
    assert meta["name"] == WEB_SKILL_NAME  # must match the folder name
    assert 0 < len(meta["description"]) <= 200  # claude.ai's limit for uploaded skills
    for tool in ("list_projects", "show_project", "set_item_status", "add_item", "suggest_item"):
        assert tool in text


def test_web_skill_zip_is_reproducible(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(app, ["web-skill", "--out", str(tmp_path / "a.zip")])
    runner.invoke(app, ["web-skill", "--out", str(tmp_path / "b.zip")])
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_web_skill_ships_inside_the_package() -> None:
    package_dir = Path(workbench.__file__).resolve().parent
    assert WEB_SKILL_TEMPLATE.is_file()
    assert package_dir in WEB_SKILL_TEMPLATE.resolve().parents
