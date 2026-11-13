"""Status roll-up: a parent's status follows its parts (edits.roll_up_project / roll_up_learning)."""

from __future__ import annotations

from typing import Any

import pytest

from workbench import edits
from workbench.yamlio import load_yaml_str_roundtrip

PROJECT = """schema_version: 1
id: rover
name: Rover
status: active
phases:
  - id: build
    name: Build
    status: in_progress
    components:
      - id: chassis
        name: Chassis
        status: done
      - id: drive
        name: Drive
        status: in_progress
        components:
          - id: motors
            name: Motors
            status: done
          - id: encoders
            name: Encoders
            status: not_started
  - id: test
    name: Test
    status: done
    components:
      - id: bench
        name: Bench test
        status: done
"""

LEARNING = """schema_version: 1
id: dsp
name: DSP
status: active
topics:
  - id: sampling
    name: Sampling
    status: in_progress
    subtopics:
      - id: nyquist
        name: Nyquist
        status: done
      - id: aliasing
        name: Aliasing
        status: not_started
  - id: filters
    name: Filters
    status: not_started
"""


def _doc(text: str) -> Any:
    return load_yaml_str_roundtrip(text)


def _status(doc: Any, item_id: str) -> str:
    loc = edits.locate_project_item(doc, item_id)
    assert loc is not None
    return str(loc.node["status"])


def test_last_part_done_finishes_every_ancestor_in_turn() -> None:
    doc = _doc(PROJECT)
    changes = edits.set_project_item_status(doc, "rover", "encoders", "done")
    assert changes == [("drive", "done"), ("build", "done")]
    assert _status(doc, "drive") == "done"
    assert _status(doc, "build") == "done"
    assert edits.describe_roll_up(changes) == "; drive done, build done"


def test_a_part_left_open_keeps_the_parent_as_it_is() -> None:
    doc = _doc(PROJECT)
    assert edits.set_project_item_status(doc, "rover", "encoders", "in_progress") == []
    assert _status(doc, "drive") == "in_progress"
    assert _status(doc, "build") == "in_progress"


def test_reopening_a_part_reopens_done_ancestors() -> None:
    doc = _doc(PROJECT)
    changes = edits.set_project_item_status(doc, "rover", "bench", "not_started")
    assert changes == [("test", "in_progress")]
    assert _status(doc, "test") == "in_progress"


@pytest.mark.parametrize("status", ["parked", "not_started"])
def test_only_done_counts_as_finished(status: str) -> None:
    doc = _doc(PROJECT)
    edits.set_project_item_status(doc, "rover", "encoders", status)
    assert _status(doc, "drive") == "in_progress"


def test_the_changed_item_itself_is_not_overridden() -> None:
    doc = _doc(PROJECT)
    # Set by hand while a part is still open: the parent's own status is the user's choice
    assert edits.set_project_item_status(doc, "rover", "drive", "done") == [("build", "done")]
    assert _status(doc, "drive") == "done"
    assert _status(doc, "encoders") == "not_started"


def test_parents_outside_the_changed_branch_are_left_alone() -> None:
    doc = _doc(PROJECT)
    # 'test' is done with a done part; mark a sibling phase's part and check 'test' is untouched,
    # even after making it inconsistent by hand
    edits.locate_project_item(doc, "test").node["status"] = "parked"
    edits.set_project_item_status(doc, "rover", "encoders", "done")
    assert _status(doc, "test") == "parked"


def test_adding_an_unfinished_part_reopens_a_done_parent() -> None:
    doc = _doc(PROJECT)
    edits.add_project_item(doc, "rover", parent_id="test", name="Field test")
    assert _status(doc, "test") == "in_progress"


def test_adding_a_done_part_can_finish_the_parent() -> None:
    doc = _doc(PROJECT)
    edits.set_project_item_status(doc, "rover", "encoders", "done")
    edits.set_project_item_status(doc, "rover", "drive", "in_progress")  # by hand
    edits.add_project_item(doc, "rover", parent_id="drive", name="Wiring", status="done")
    assert _status(doc, "drive") == "done"


def test_roll_up_from_an_item_that_lost_its_open_part() -> None:
    doc = _doc(PROJECT)
    drive = edits.locate_project_item(doc, "drive").node
    drive["components"].pop(1)  # encoders, the only open part
    assert edits.roll_up_project_from(doc, "drive") == [("drive", "done"), ("build", "done")]
    assert edits.roll_up_project_from(doc, "gone") == []


def test_learning_topic_follows_its_subtopics() -> None:
    doc = _doc(LEARNING)
    assert edits.set_learning_item_status(doc, "dsp", "aliasing", "done") == [("sampling", "done")]
    assert edits.set_learning_item_status(doc, "dsp", "nyquist", "in_progress") == [
        ("sampling", "in_progress")
    ]
    # A topic set directly is left as set
    assert edits.set_learning_item_status(doc, "dsp", "filters", "done") == []
