"""Unit tests for the suggestions inbox edits (keep/dismiss) and the semantic guard."""

from __future__ import annotations

import pytest

from workbench import edits
from workbench.edits import EditError
from workbench.semantic import check_semantic_diff
from workbench.yamlio import load_yaml_str_roundtrip, to_plain_python

PROJECT = """schema_version: 1
id: inbox
name: Inbox
status: active
origin: manual
updated: 2026-10-01
uses_phases: false
topics:
  - id: docs
    name: Docs
    status: not_started
suggestions:
  - id: docs
    name: Docs
    summary: Write a user guide
    under: docs
    added: 2026-10-20
  - id: gone
    name: Gone
    under: missing
    added: 2026-10-21
  - id: benchmarks
    name: Benchmarks
    added: 2026-10-22
"""


def _doc():
    return load_yaml_str_roundtrip(PROJECT)


def test_keep_under_item_reuses_free_id_or_makes_a_unique_one() -> None:
    doc = _doc()
    # 'docs' is already a topic id, so the kept component gets a suffixed slug
    assert edits.keep_suggestion(doc, "inbox", "docs") == ("docs-2", "component")
    plain = to_plain_python(doc)
    assert plain["topics"][0]["components"] == [
        {"id": "docs-2", "name": "Docs", "status": "not_started", "summary": "Write a user guide"}
    ]
    assert [s["id"] for s in plain["suggestions"]] == ["gone", "benchmarks"]


def test_keep_without_under_adds_a_top_level_group_and_drops_empty_inbox() -> None:
    doc = _doc()
    edits.dismiss_suggestion(doc, "inbox", "docs")
    edits.dismiss_suggestion(doc, "inbox", "gone")
    assert edits.keep_suggestion(doc, "inbox", "benchmarks") == ("benchmarks", "topic")
    plain = to_plain_python(doc)
    assert [t["id"] for t in plain["topics"]] == ["docs", "benchmarks"]
    assert "suggestions" not in plain


def test_keep_with_missing_under_is_422_and_leaves_doc_unchanged() -> None:
    doc = _doc()
    before = to_plain_python(doc)
    with pytest.raises(EditError) as exc:
        edits.keep_suggestion(doc, "inbox", "gone")
    assert exc.value.status_code == 422 and "missing" in str(exc.value)
    assert to_plain_python(doc) == before


@pytest.mark.parametrize("fn", [edits.keep_suggestion, edits.dismiss_suggestion])
def test_unknown_suggestion_is_404(fn) -> None:
    doc = _doc()
    with pytest.raises(EditError) as exc:
        fn(doc, "inbox", "nope")
    assert exc.value.status_code == 404
    with pytest.raises(EditError) as exc:
        fn(doc, "inbox", "Not A Slug")
    assert exc.value.status_code == 400


def test_dismissing_suggestions_never_trips_the_semantic_guard() -> None:
    old = to_plain_python(_doc())
    old["suggestions"][2]["summary"] = "x" * 2000  # far more text than the rest of the file
    new = dict(old)
    new.pop("suggestions")
    res = check_semantic_diff(old, new)
    assert res.ok, res.errors
