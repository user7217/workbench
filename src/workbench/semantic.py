"""Semantic diff checks against previous metadata version (§6.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PROJECT_STATUS_ORDER = {"not_started": 0, "in_progress": 1, "parked": 1, "done": 2}
TOP_STATUS_ORDER = {"planned": 0, "active": 0, "paused": 0, "done": 1, "archived": 2}


@dataclass
class SemanticCheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _collect_string_length(obj: Any, ignore_keys: set[str] | None = None) -> int:
    ignore = ignore_keys or {"id", "schema_version", "status", "origin", "updated", "root_commit", "refs", "uses_phases"}
    total = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ignore:
                continue
            total += _collect_string_length(v, ignore)
    elif isinstance(obj, list):
        for item in obj:
            total += _collect_string_length(item, ignore)
    elif isinstance(obj, str):
        total += len(obj)
    return total


def _flatten_components(items: list[Any]) -> list[dict[str, Any]]:
    """Return items plus every component nested under them via `components:`, depth first."""
    out: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(item)
            out.extend(_flatten_components(item.get("components", []) or []))
    return out


def _group_children(groups: list[Any]) -> list[dict[str, Any]]:
    """All components/topics/subtopics under phase-like groups, including nested components."""
    out: list[dict[str, Any]] = []
    for g in groups or []:
        if isinstance(g, dict):
            for key in ("components", "topics", "subtopics"):
                out.extend(_flatten_components(g.get(key, []) or []))
    return out


def check_semantic_diff(
    old_data: dict[str, Any] | None,
    new_data: dict[str, Any],
    allow_removals: bool = False,
    old_markdowns: dict[str, str] | None = None,
    new_markdowns: dict[str, str] | None = None,
) -> SemanticCheckResult:
    """Compare new metadata dict against previous version at origin/main (§6.3)."""
    res = SemanticCheckResult()
    if old_data is None:
        return res

    # 1. Top-level ID must never change
    old_id = old_data.get("id")
    new_id = new_data.get("id")
    if old_id and new_id and old_id != new_id:
        res.errors.append(f"IDs must not change: top-level id changed from '{old_id}' to '{new_id}'")

    # 2. Check phases & nested components/topics/subtopics if project
    if "phases" in old_data or "phases" in new_data:
        _compare_named_items(
            old_items=old_data.get("phases", []) or [],
            new_items=new_data.get("phases", []) or [],
            entity_label="phase",
            allow_removals=allow_removals,
            res=res,
        )
        old_comps = _group_children(old_data.get("phases", []) or [])
        new_comps = _group_children(new_data.get("phases", []) or [])
        _compare_named_items(
            old_items=old_comps,
            new_items=new_comps,
            entity_label="component",
            allow_removals=allow_removals,
            res=res,
        )

    # 3. Check topics & subtopics (learning path or non-phased project)
    if "topics" in old_data or "topics" in new_data:
        _compare_named_items(
            old_items=old_data.get("topics", []) or [],
            new_items=new_data.get("topics", []) or [],
            entity_label="topic",
            allow_removals=allow_removals,
            res=res,
        )
        old_subs = _group_children(old_data.get("topics", []) or [])
        new_subs = _group_children(new_data.get("topics", []) or [])
        _compare_named_items(
            old_items=old_subs,
            new_items=new_subs,
            entity_label="subtopic",
            allow_removals=allow_removals,
            res=res,
        )

    # 4. Mass deletion check (> 30% of string content deleted)
    old_chars = _collect_string_length(old_data)
    if old_markdowns:
        old_chars += sum(len(v) for v in old_markdowns.values())
    new_chars = _collect_string_length(new_data)
    if new_markdowns:
        new_chars += sum(len(v) for v in new_markdowns.values())

    if old_chars > 0:
        deleted_ratio = (old_chars - new_chars) / old_chars
        if deleted_ratio > 0.30 and not allow_removals:
            pct = int(round(deleted_ratio * 100))
            res.errors.append(
                f"Mass deletion detected: {pct}% of string content removed (>30% limit; pass --allow-removals if intentional)"
            )

    return res


def _compare_named_items(
    old_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
    entity_label: str,
    allow_removals: bool,
    res: SemanticCheckResult,
) -> None:
    old_by_id = {item["id"]: item for item in old_items if isinstance(item, dict) and "id" in item}
    new_by_id = {item["id"]: item for item in new_items if isinstance(item, dict) and "id" in item}

    removed_ids = [oid for oid in old_by_id if oid not in new_by_id]
    added_ids = [nid for nid in new_by_id if nid not in old_by_id]

    added_names_to_id = {
        str(new_by_id[nid].get("name", "")).strip().lower(): nid
        for nid in added_ids
        if new_by_id[nid].get("name")
    }

    for oid in removed_ids:
        old_name = str(old_by_id[oid].get("name", "")).strip()
        if old_name and old_name.lower() in added_names_to_id:
            new_id = added_names_to_id[old_name.lower()]
            res.errors.append(
                f"IDs must not change: {entity_label} '{oid}' was renamed to '{new_id}' (name '{old_name}')"
            )
        elif not allow_removals:
            res.errors.append(
                f"Removed {entity_label} ID '{oid}' is not allowed without --allow-removals"
            )

    # Check status moving backwards (warning only)
    for item_id, new_item in new_by_id.items():
        if item_id in old_by_id:
            old_status = str(old_by_id[item_id].get("status", ""))
            new_status = str(new_item.get("status", ""))
            old_rank = PROJECT_STATUS_ORDER.get(old_status, -1)
            new_rank = PROJECT_STATUS_ORDER.get(new_status, -1)
            if old_rank > new_rank and old_rank >= 0 and new_rank >= 0:
                res.warnings.append(
                    f"Status moved backwards for {entity_label} '{item_id}': {old_status} -> {new_status}"
                )
