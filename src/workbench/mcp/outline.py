"""Compact, readable text for the model: the project list and one project or learning path.

Each line carries an item's status, name and id, which is what the write tools need; long
summaries and notes are cut short. Input is the agent API's JSON, read defensively so a field
the dashboard adds or leaves out does not break the outline.
"""

from __future__ import annotations

from typing import Any

SUMMARY_LIMIT = 160
NOTES_LIMIT = 240
INDENT = "  "


def _clip(text: Any, limit: int) -> str:
    one_line = " ".join(str(text or "").split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1].rstrip() + "…"


def _line(depth: int, node: dict[str, Any]) -> str:
    line = f"{INDENT * depth}- [{node.get('status', '?')}] {node.get('name', '?')} ({node.get('id', '?')})"
    summary = _clip(node.get("summary"), SUMMARY_LIMIT)
    return f"{line}: {summary}" if summary else line


def _details(depth: int, node: dict[str, Any]) -> list[str]:
    pad = INDENT * (depth + 1)
    out: list[str] = []
    needs = node.get("depends_on") or []
    if needs:
        out.append(f"{pad}needs: {', '.join(str(n) for n in needs)}")
    notes = _clip(node.get("notes"), NOTES_LIMIT)
    if notes:
        out.append(f"{pad}notes: {notes}")
    return out


def _component_lines(components: list[dict[str, Any]], depth: int) -> list[str]:
    out: list[str] = []
    for comp in components:
        out.append(_line(depth, comp))
        out.extend(_details(depth, comp))
        for entry in comp.get("items") or []:
            out.append(f"{INDENT * (depth + 1)}· {_clip(entry, SUMMARY_LIMIT)}")
        out.extend(_component_lines(comp.get("components") or [], depth + 1))
    return out


def format_index(index: dict[str, Any]) -> str:
    projects = index.get("projects") or []
    learning = index.get("learning") or []
    lines = ["Projects (id: name [status]):"]
    if not projects:
        lines.append("  (none)")
    for proj in projects:
        line = f"- {proj.get('id')}: {proj.get('name')} [{proj.get('status')}]"
        if proj.get("parent"):
            line += f" (part of {proj['parent']})"
        lines.append(line)
    lines.append("")
    lines.append("Learning paths (id: name [status]):")
    if not learning:
        lines.append("  (none)")
    for path in learning:
        lines.append(f"- {path.get('id')}: {path.get('name')} [{path.get('status')}]")
    return "\n".join(lines)


def format_project(project: dict[str, Any]) -> str:
    head = f"Project: {project.get('name')} ({project.get('id')}) [{project.get('status')}]"
    lines = [head]
    if project.get("parent"):
        lines.append(f"Part of: {project['parent']}")
    description = _clip(project.get("description"), NOTES_LIMIT)
    if description:
        lines.append(f"Description: {description}")

    groups = list(project.get("phases") or []) + list(project.get("topics") or [])
    group_word = "Phases" if project.get("uses_phases", True) else "Topics"
    lines.append("")
    lines.append(f"{group_word} and their items ([status] name (id): summary):")
    if not groups:
        lines.append("  (none yet: add_item with no parent_id creates the first one)")
    for group in groups:
        lines.append(_line(0, group))
        lines.extend(_details(0, group))
        children = (
            list(group.get("components") or [])
            + list(group.get("topics") or [])
            + list(group.get("subtopics") or [])
        )
        lines.extend(_component_lines(children, 1))

    suggestions = project.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append(
            f"Inbox: {len(suggestions)} suggestion(s) waiting for the owner to keep or dismiss:"
        )
        for sug in suggestions:
            line = f"- {sug.get('name')} ({sug.get('id')})"
            if sug.get("under"):
                line += f" under {sug['under']}"
            lines.append(line)
    return "\n".join(lines)


def format_learning_path(path: dict[str, Any]) -> str:
    lines = [f"Learning path: {path.get('name')} ({path.get('id')}) [{path.get('status')}]"]
    description = _clip(path.get("description"), NOTES_LIMIT)
    if description:
        lines.append(f"Description: {description}")
    related = path.get("related_projects") or []
    if related:
        lines.append(f"Related projects: {', '.join(str(r) for r in related)}")
    lines.append("")
    lines.append("Topics and subtopics ([status] name (id): summary):")
    topics = path.get("topics") or []
    if not topics:
        lines.append("  (none)")
    for topic in topics:
        lines.append(_line(0, topic))
        lines.extend(_details(0, topic))
        for sub in topic.get("subtopics") or []:
            lines.append(_line(1, sub))
            lines.extend(_details(1, sub))
    return "\n".join(lines)
