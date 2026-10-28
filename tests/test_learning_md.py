"""Tests for the learning-path Markdown format, merge-on-reimport, and export round trip."""

from __future__ import annotations

import re
from pathlib import Path

from typer.testing import CliRunner

from workbench.cli import app
from workbench.config import load_local_config, save_local_config
from workbench.demo_env import init_demo_environment
from workbench.gitops import run_git
from workbench.learning_md import (
    export_learning_markdown,
    merge_learning_paths,
    parse_learning_markdown,
)
from workbench.models import LearningPath

runner = CliRunner()

OUTLINE = """# Control Theory
Feedback control from first principles, aimed at the field robot.
- [Feedback Systems](https://example.com/feedback-systems)

## [x] Linear systems
State-space models and what makes them stable.
- [Lecture notes](https://example.com/linear)

### [x] State-space models
### [~] Stability and eigenvalues
Eigenvalues of A decide whether the system settles.

## [ ] PID control
### Tuning
### [ ] Anti-windup
- [Not https](http://example.com/insecure)
"""


def test_parse_learning_markdown_reads_statuses_summaries_and_links() -> None:
    data, warnings = parse_learning_markdown(OUTLINE, fallback_name="ignored")

    assert data["id"] == "control-theory"
    assert data["name"] == "Control Theory"
    assert data["description"] == "Feedback control from first principles, aimed at the field robot."
    assert data["references"] == [{"label": "Feedback Systems", "url": "https://example.com/feedback-systems"}]

    linear, pid = data["topics"]
    assert (linear["id"], linear["status"]) == ("linear-systems", "done")
    assert linear["summary"] == "State-space models and what makes them stable."
    assert linear["references"][0]["url"] == "https://example.com/linear"
    assert [(s["id"], s["status"]) for s in linear["subtopics"]] == [
        ("state-space-models", "done"),
        ("stability-and-eigenvalues", "in_progress"),
    ]
    assert linear["subtopics"][1]["summary"].startswith("Eigenvalues of A")
    assert [(s["id"], s["status"]) for s in pid["subtopics"]] == [
        ("tuning", "not_started"),  # no marker means not started
        ("anti-windup", "not_started"),
    ]
    assert pid["subtopics"][1]["references"] == []  # non-https link dropped
    assert any("https" in w for w in warnings)

    data["updated"] = "2026-10-28"
    LearningPath.model_validate(data)


def test_merge_never_moves_status_backwards_or_drops_items() -> None:
    old, _ = parse_learning_markdown(OUTLINE, fallback_name="x")
    old["topics"][1]["subtopics"][0]["status"] = "done"  # marked done on the dashboard
    old["topics"][1]["subtopics"][0]["notes"] = "Ziegler-Nichols worked on the cart."

    new, _ = parse_learning_markdown(
        "# Control Theory\n\n## [ ] PID control\n### [ ] Tuning\nPick gains from step response.\n"
        "### [ ] Feedforward\n",
        fallback_name="x",
    )
    merged = merge_learning_paths(old, new)

    pid = next(t for t in merged["topics"] if t["id"] == "pid-control")
    tuning = next(s for s in pid["subtopics"] if s["id"] == "tuning")
    assert tuning["status"] == "done"
    assert tuning["notes"] == "Ziegler-Nichols worked on the cart."
    assert tuning["summary"] == "Pick gains from step response."
    assert [s["id"] for s in pid["subtopics"]] == ["tuning", "feedforward", "anti-windup"]
    assert any(t["id"] == "linear-systems" for t in merged["topics"])  # absent from new outline, kept
    assert merged["description"] == old["description"]  # new outline had none


def test_reimport_keeps_related_projects_and_fields_outside_the_outline() -> None:
    old, _ = parse_learning_markdown(OUTLINE, fallback_name="x")
    old["related_projects"] = ["field-robot"]
    old["status"] = "paused"

    outline, _ = parse_learning_markdown("# Control Theory\n\n## [x] PID control\n", fallback_name="x")
    merged = merge_learning_paths(old, outline)
    assert merged["related_projects"] == ["field-robot"]
    assert merged["status"] == "paused"

    # A YAML import that names related projects adds to the list and never drops existing ones
    yaml_import = dict(outline, related_projects=["drone-lab", "field-robot"])
    assert merge_learning_paths(old, yaml_import)["related_projects"] == ["field-robot", "drone-lab"]
    assert old["related_projects"] == ["field-robot"]  # input not mutated


def test_export_round_trips_through_parse() -> None:
    data, _ = parse_learning_markdown(OUTLINE, fallback_name="x")
    again, _ = parse_learning_markdown(export_learning_markdown(data), fallback_name="x")
    assert again == data


def test_cli_import_merges_existing_path_and_export_prints_outline(
    tmp_path: Path, monkeypatch: any
) -> None:
    info = init_demo_environment(tmp_path / "demo_learning")
    laptop_data = Path(info["laptop_data_dir"])
    monkeypatch.setenv("HOME", str(info["laptop_home"]))
    cfg = load_local_config()
    cfg.privacy_review = "off"  # don't call a real LLM reviewer from tests
    save_local_config(cfg)

    def remote_head() -> str:
        run_git(["fetch", "origin", "main"], cwd=laptop_data)
        return run_git(["rev-parse", "origin/main"], cwd=laptop_data).stdout.strip()

    outline = tmp_path / "control.md"
    outline.write_text(OUTLINE, encoding="utf-8")
    first = runner.invoke(app, ["learning", "import", str(outline)])
    assert first.exit_code == 0, first.output

    # Re-import a shorter outline that un-marks a done item: progress and missing items survive
    outline.write_text("# Control Theory\n\n## [ ] Linear systems\n### [ ] Observers\n", encoding="utf-8")
    second = runner.invoke(app, ["learning", "import", str(outline)])
    assert second.exit_code == 0, second.output

    exported = runner.invoke(app, ["learning", "export", "control-theory"])
    assert exported.exit_code == 0, exported.output
    md = exported.stdout
    assert "## [x] Linear systems" in md
    assert "### [~] Stability and eigenvalues" in md
    assert "### [ ] Observers" in md
    assert "## [ ] PID control" in md

    # Re-importing the export changes nothing and pushes nothing
    head = remote_head()
    outline.write_text(md, encoding="utf-8")
    third = runner.invoke(app, ["learning", "import", str(outline)])
    assert third.exit_code == 0, third.output
    assert remote_head() == head

    missing = runner.invoke(app, ["learning", "export", "no-such-path"])
    assert missing.exit_code == 1


def test_learning_chat_prompt_example_parses_to_a_valid_path() -> None:
    prompt = (Path(__file__).resolve().parent.parent / "agent" / "chat" / "LEARNING_PROMPT.md").read_text(
        encoding="utf-8"
    )
    example = re.search(r"```markdown\n(.*?)```", prompt, re.S).group(1)
    data, warnings = parse_learning_markdown(example, fallback_name="x")
    assert warnings == []
    assert data["topics"]
    data["updated"] = "2026-10-28"
    LearningPath.model_validate(data)
