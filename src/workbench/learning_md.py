"""Learning-path Markdown outline format: parse, export, and merge-on-reimport.

Format (every part except the headings is optional):

    # Path Name
    One paragraph description.
    - [Reference label](https://...)

    ## [x] Topic name
    One-line summary.
    - [Label](https://...)

    ### [~] Subtopic name {#explicit-id}

Status markers: `[ ]` not started (also the default with no marker), `[~]` in progress, `[x]` done.
A trailing `{#id}` pins an item's id when it differs from the slug of its name.
"""

from __future__ import annotations

import re
from typing import Any

from workbench.identity import slugify_name

STATUS_BY_MARKER = {" ": "not_started", "~": "in_progress", "x": "done", "X": "done"}
MARKER_BY_STATUS = {"not_started": " ", "in_progress": "~", "done": "x"}
STATUS_RANK = {"not_started": 0, "in_progress": 1, "done": 2}

_HEADING_RE = re.compile(r"^(#{1,3})\s+(?:\[([ xX~])\]\s*)?(.*?)\s*(?:\{#([a-z0-9-]+)\})?\s*$")
_LINK_RE = re.compile(r"^[-*]\s+\[([^\]]+)\]\(([^)\s]+)\)\s*$")


def _new_item(name: str, marker: str | None, explicit_id: str | None) -> dict[str, Any]:
    return {
        "id": explicit_id or slugify_name(name),
        "name": name[:80],
        "status": STATUS_BY_MARKER.get(marker or " ", "not_started"),
        "summary": "",
        "references": [],
    }


def parse_learning_markdown(text: str, fallback_name: str) -> tuple[dict[str, Any], list[str]]:
    """Parse an outline into a learning path dict (without `updated`) plus warnings."""
    warnings: list[str] = []
    path: dict[str, Any] = {
        "schema_version": 1,
        "id": "",
        "name": fallback_name[:80],
        "description": "",
        "status": "active",
        "references": [],
        "topics": [],
    }
    texts: dict[int, list[str]] = {}  # id(item) -> free text lines under it
    current: dict[str, Any] = path
    current_topic: dict[str, Any] | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = _HEADING_RE.match(line)
        if heading and heading.group(3):
            level, marker, name, explicit_id = heading.groups()
            if level == "#":
                path["name"] = name[:80]
                current = path
            elif level == "##":
                current_topic = _new_item(name, marker, explicit_id)
                current_topic["subtopics"] = []
                path["topics"].append(current_topic)
                current = current_topic
            else:
                if current_topic is None:
                    current_topic = _new_item("General", None, None)
                    current_topic["subtopics"] = []
                    path["topics"].append(current_topic)
                current = _new_item(name, marker, explicit_id)
                current_topic["subtopics"].append(current)
            continue
        link = _LINK_RE.match(line)
        if link:
            label, url = link.groups()
            if url.startswith("https://"):
                current["references"].append({"label": label[:40], "url": url})
            else:
                warnings.append(f"Skipped link '{label}': references must start with https://")
            continue
        texts.setdefault(id(current), []).append(line)

    path["id"] = slugify_name(path["name"])
    path["description"] = " ".join(texts.get(id(path), []))[:500]
    for topic in path["topics"]:
        topic["summary"] = " ".join(texts.get(id(topic), []))[:300]
        for sub in topic["subtopics"]:
            sub["summary"] = " ".join(texts.get(id(sub), []))[:300]
    return path, warnings


def _heading(level: str, item: dict[str, Any]) -> str:
    marker = MARKER_BY_STATUS.get(item.get("status", "not_started"), " ")
    suffix = "" if item["id"] == slugify_name(item["name"]) else f" {{#{item['id']}}}"
    return f"{level} [{marker}] {item['name']}{suffix}"


def _body(item: dict[str, Any], text_key: str) -> list[str]:
    lines = [item[text_key]] if item.get(text_key) else []
    lines += [f"- [{ref['label']}]({ref['url']})" for ref in item.get("references") or []]
    return lines


def export_learning_markdown(path: dict[str, Any]) -> str:
    """Render a learning path dict in the outline format parse_learning_markdown reads."""
    lines = [f"# {path['name']}", *_body(path, "description")]
    for topic in path.get("topics") or []:
        lines += ["", _heading("##", topic), *_body(topic, "summary")]
        for sub in topic.get("subtopics") or []:
            lines += ["", _heading("###", sub), *_body(sub, "summary")]
    return "\n".join(lines) + "\n"


def _merge_references(old: list[dict], new: list[dict]) -> list[dict]:
    seen = {ref["url"] for ref in old}
    return list(old) + [ref for ref in new if ref["url"] not in seen]


def _merge_items(old_items: list[dict], new_items: list[dict], child_key: str | None) -> list[dict]:
    old_by_id = {item["id"]: item for item in old_items}
    merged: list[dict] = []
    for new in new_items:
        old = old_by_id.pop(new["id"], None)
        if old is None:
            merged.append(new)
            continue
        item = dict(old)
        item["name"] = new["name"]
        if STATUS_RANK.get(new["status"], 0) > STATUS_RANK.get(old.get("status"), 0):
            item["status"] = new["status"]
        if new.get("summary"):
            item["summary"] = new["summary"]
        item["references"] = _merge_references(old.get("references") or [], new.get("references") or [])
        if child_key:
            item[child_key] = _merge_items(old.get(child_key) or [], new.get(child_key) or [], None)
        merged.append(item)
    # Items missing from the new outline are kept: an outline never deletes progress
    merged.extend(old_by_id.values())
    return merged


def merge_learning_paths(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge a re-imported outline into an existing path: statuses only move forward, nothing is dropped.

    Fields the outline does not carry (status, related_projects, ...) keep their existing values;
    related_projects named by a YAML import are added to the existing list.
    """
    merged = dict(old)
    old_related = list(old.get("related_projects") or [])
    extra_related = [p for p in new.get("related_projects") or [] if p not in old_related]
    if extra_related:
        merged["related_projects"] = old_related + extra_related
    if new.get("description"):
        merged["description"] = new["description"]
    merged["references"] = _merge_references(old.get("references") or [], new.get("references") or [])
    merged["topics"] = _merge_items(old.get("topics") or [], new.get("topics") or [], "subtopics")
    return merged
