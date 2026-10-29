"""Pure edits of a project/learning round-trip YAML document, shared by the server and the CLI.

Nothing here touches git or the filesystem: each function takes the round-trip document
(ruamel CommentedMap) and changes it in place, raising EditError when the input is invalid
or the target item does not exist. `status_code` classifies the failure (400 bad input,
404 not found, 422 conflicts with the document) and is what the server reports over HTTP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator, get_args

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from workbench.identity import slugify_name
from workbench.models import (
    SLUG_PATTERN,
    Component,
    LearningItemStatus,
    Phase,
    PhaseStatus,
    Project,
    Subtopic,
    Topic,
)

_SLUG_RE = re.compile(SLUG_PATTERN)
PHASE_STATUSES: tuple[str, ...] = get_args(PhaseStatus)
LEARNING_ITEM_STATUSES: tuple[str, ...] = get_args(LearningItemStatus)

# Lists that hold groups (top level) and nested items inside a project or learning file.
GROUP_KEYS: tuple[str, ...] = ("phases", "topics")
GROUP_CHILD_KEYS: tuple[str, ...] = ("components", "topics", "subtopics")
ITEM_LIST_KEYS: tuple[str, ...] = ("phases", "topics", "subtopics", "components")


class EditError(RuntimeError):
    """An edit could not be applied; status_code is 400/404/422 (the HTTP status the server reports)."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------


def require_slug(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 48 or not _SLUG_RE.match(value):
        raise EditError(f"Invalid {label}: {value!r}", status_code=400)
    return value


def require_status(value: object, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise EditError(
            f"Invalid status: {value!r} (expected one of {', '.join(allowed)})",
            status_code=400,
        )
    return value


def require_position(value: object, label: str = "position") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EditError(f"'{label}' must be a non-negative integer", status_code=400)
    return value


def check_position(position: int, length: int) -> None:
    if position > length:
        raise EditError(f"Position {position} is out of range (0..{length})", status_code=400)


# ---------------------------------------------------------------------------
# Item lookup
# ---------------------------------------------------------------------------


def children(node: Any, keys: tuple[str, ...]) -> list[Any]:
    out: list[Any] = []
    for key in keys:
        for child in node.get(key) or []:
            if hasattr(child, "get"):
                out.append(child)
    return out


def collect_item_ids(node: Any) -> set[str]:
    """Ids of every group/component/topic/subtopic nested anywhere under a plain or YAML node."""
    ids: set[str] = set()
    if not hasattr(node, "get"):
        return ids
    for key in ITEM_LIST_KEYS:
        kids = node.get(key)
        if not isinstance(kids, list):
            continue
        for child in kids:
            if hasattr(child, "get"):
                if isinstance(child.get("id"), str):
                    ids.add(child["id"])
                ids |= collect_item_ids(child)
    return ids


def subtree_ids(node: Any) -> set[str]:
    return {node["id"]} | collect_item_ids(node)


@dataclass
class Location:
    """Where an item sits: its node, the YAML list holding it, that list's key, and the list's owner."""

    node: Any
    seq: Any
    key: str
    owner: Any
    is_group: bool

    @property
    def index(self) -> int:
        return next(i for i, n in enumerate(self.seq) if n is self.node)


def _locate_in(owner: Any, keys: tuple[str, ...], item_id: str) -> Location | None:
    for key in keys:
        seq = owner.get(key)
        if not isinstance(seq, list):
            continue
        for node in seq:
            if not hasattr(node, "get"):
                continue
            if node.get("id") == item_id:
                return Location(node, seq, key, owner, is_group=False)
            found = _locate_in(node, ("components",), item_id)
            if found is not None:
                return found
    return None


def locate_project_item(doc: Any, item_id: str) -> Location | None:
    """Find a group (phases/topics) first, then a component at any depth."""
    for key in GROUP_KEYS:
        seq = doc.get(key)
        if not isinstance(seq, list):
            continue
        for node in seq:
            if hasattr(node, "get") and node.get("id") == item_id:
                return Location(node, seq, key, doc, is_group=True)
    for group in children(doc, GROUP_KEYS):
        found = _locate_in(group, GROUP_CHILD_KEYS, item_id)
        if found is not None:
            return found
    return None


def require_project_item(doc: Any, project_id: str, item_id: str) -> Location:
    loc = locate_project_item(doc, item_id)
    if loc is None:
        raise EditError(f"Item '{item_id}' not found in project '{project_id}'", status_code=404)
    return loc


def find_learning_item(doc: Any, item_id: str) -> Any | None:
    for topic in children(doc, ("topics",)):
        if topic.get("id") == item_id:
            return topic
        for sub in children(topic, ("subtopics",)):
            if sub.get("id") == item_id:
                return sub
    return None


def iter_component_nodes(doc: Any) -> Iterator[Any]:
    """Every component node (any depth) under the project's groups."""

    def walk(nodes: list[Any]) -> Iterator[Any]:
        for node in nodes:
            yield node
            yield from walk(children(node, ("components",)))

    for group in children(doc, GROUP_KEYS):
        yield from walk(children(group, GROUP_CHILD_KEYS))


# ---------------------------------------------------------------------------
# Field writes
# ---------------------------------------------------------------------------


def yaml_text(value: str) -> str:
    """Multi-line text is written as a YAML literal block (LF line endings) so the file stays readable."""
    value = value.replace("\r\n", "\n")
    return LiteralScalarString(value) if "\n" in value else value


def field_order(model: type) -> list[str]:
    return list(model.model_fields)


def set_key(node: Any, key: str, value: Any, order: list[str]) -> None:
    """Set node[key]; a new key goes after the keys that precede it in the model's field order."""
    if key in node:
        node[key] = value
        return
    rank = order.index(key) if key in order else len(order)
    pos = 0
    for i, existing in enumerate(node.keys()):
        if existing in order and order.index(existing) < rank:
            pos = i + 1
    node.insert(pos, key, value)


def apply_text_fields(node: Any, fields: dict[str, Any], order: list[str]) -> None:
    """Write text fields; an empty value removes optional keys (models default them to "")."""
    for key, value in fields.items():
        if key == "name":
            # Required; an empty name fails model validation rather than being dropped.
            set_key(node, "name", yaml_text(value), order)
        elif key == "items":
            if value:
                set_key(node, "items", CommentedSeq(yaml_text(v) for v in value), order)
            else:
                node.pop("items", None)
        elif value == "":
            node.pop(key, None)
        else:
            set_key(node, key, yaml_text(value), order)


def child_list(node: Any, order: list[str]) -> Any:
    """The node's `components` list, created in model field order if missing."""
    seq = node.get("components")
    if not isinstance(seq, list):
        seq = CommentedSeq()
        set_key(node, "components", seq, order)
    return seq


def unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = base[: 48 - len(suffix)].rstrip("-") + suffix
        if candidate not in taken:
            return candidate
        n += 1


def slug_for_item(name: str) -> str:
    # slugify_name falls back to "project"; an item with no usable characters is "item".
    return slugify_name(name) if re.search(r"[a-z0-9]", name.lower()) else "item"


# ---------------------------------------------------------------------------
# Project item mutations
# ---------------------------------------------------------------------------


def set_project_item_status(doc: Any, project_id: str, item_id: str, status: str) -> None:
    """Set the status of a phase/topic or a component at any depth."""
    status = require_status(status, PHASE_STATUSES)
    require_project_item(doc, project_id, item_id).node["status"] = status


def set_project_item_fields(doc: Any, project_id: str, item_id: str, fields: dict[str, Any]) -> None:
    """Write text fields (name/summary/notes/description/items) of a group or a component."""
    loc = require_project_item(doc, project_id, item_id)
    if loc.is_group and "items" in fields:
        raise EditError("'items' can only be set on components, not phases/topics", 400)
    apply_text_fields(loc.node, fields, field_order(Phase if loc.is_group else Component))


def _check_dependencies(doc: Any, project_id: str, item_id: str | None, deps: list[str]) -> list[str]:
    """Dependencies must be other existing components of the same project, without repeats."""
    known = {
        str(c.get("id"))
        for c in iter_component_nodes(doc)
        if c.get("id") is not None
    }
    out: list[str] = []
    for dep in deps:
        require_slug(dep, "depends_on id")
        if dep == item_id:
            raise EditError(f"'{item_id}' cannot depend on itself", status_code=400)
        if dep not in known:
            raise EditError(
                f"depends_on '{dep}' is not a component of project '{project_id}'", status_code=404
            )
        if dep not in out:
            out.append(dep)
    return out


def set_project_item_dependencies(doc: Any, project_id: str, item_id: str, deps: list[str]) -> None:
    """Replace a component's depends_on list (an empty list removes it). Phases/topics have none."""
    loc = require_project_item(doc, project_id, item_id)
    if loc.is_group:
        raise EditError("depends_on can only be set on components, not phases/topics", 400)
    deps = _check_dependencies(doc, project_id, item_id, deps)
    if deps:
        seq = CommentedSeq(deps)
        seq.fa.set_flow_style()
        set_key(loc.node, "depends_on", seq, field_order(Component))
    elif "depends_on" in loc.node:
        del loc.node["depends_on"]


def add_project_item(
    doc: Any,
    project_id: str,
    *,
    parent_id: str | None,
    name: str,
    item_id: str | None = None,
    status: str = "not_started",
    summary: str = "",
    notes: str = "",
    position: int | None = None,
    depends_on: list[str] | None = None,
) -> tuple[str, str]:
    """Add a group (parent_id None) or a component under a group/component.

    The id is `item_id` if given (must be unused), else a slug of the name made unique with
    -2/-3... suffixes. Returns (new id, label) where label is "phase", "topic" or "component".
    """
    if parent_id is not None:
        require_slug(parent_id, "parent_id")
    if not isinstance(name, str) or not name.strip():
        raise EditError("'name' is required and must be a non-empty string", status_code=400)
    if item_id is not None:
        require_slug(item_id, "id")
    status = require_status(status, PHASE_STATUSES)
    if position is not None:
        position = require_position(position)

    if parent_id is None:
        key = "phases" if doc.get("uses_phases", True) is not False else "topics"
        seq = doc.get(key)
        if not isinstance(seq, list):
            seq = CommentedSeq()
            set_key(doc, key, seq, field_order(Project))
        label = "phase" if key == "phases" else "topic"
    else:
        parent = locate_project_item(doc, parent_id)
        if parent is None:
            raise EditError(
                f"Parent item '{parent_id}' not found in project '{project_id}'", status_code=404
            )
        seq = child_list(parent.node, field_order(Phase if parent.is_group else Component))
        label = "component"

    taken = collect_item_ids(doc)
    if item_id is not None:
        if item_id in taken:
            raise EditError(
                f"Id '{item_id}' is already used in project '{project_id}'", status_code=422
            )
        new_id = item_id
    else:
        new_id = unique_id(slug_for_item(name), taken)

    pos = len(seq) if position is None else position
    check_position(pos, len(seq))
    node = CommentedMap()
    node["id"] = new_id
    node["name"] = name
    node["status"] = status
    if summary:
        node["summary"] = yaml_text(summary)
    if notes:
        node["notes"] = yaml_text(notes)
    if depends_on:
        if parent_id is None:
            raise EditError("depends_on can only be set on components, not phases/topics", 400)
        deps = CommentedSeq(_check_dependencies(doc, project_id, new_id, depends_on))
        deps.fa.set_flow_style()
        node["depends_on"] = deps
    seq.insert(pos, node)
    return new_id, label


# ---------------------------------------------------------------------------
# Suggestions inbox
# ---------------------------------------------------------------------------


def add_suggestion(
    doc: Any, project_id: str, *, name: str, summary: str = "", under: str | None, added: str
) -> str:
    """File a suggestion in the project's inbox; returns its id. `under` must be an existing item."""
    if not isinstance(name, str) or not name.strip():
        raise EditError("'name' is required and must be a non-empty string", status_code=400)
    if under is not None:
        require_project_item(doc, project_id, require_slug(under, "under"))
    seq = doc.get("suggestions")
    if not isinstance(seq, list):
        seq = CommentedSeq()
        set_key(doc, "suggestions", seq, field_order(Project))
    taken = {str(s.get("id")) for s in seq if isinstance(s, dict)}
    new_id = unique_id(slug_for_item(name), taken)
    node = CommentedMap()
    node["id"] = new_id
    node["name"] = name
    if summary:
        node["summary"] = yaml_text(summary)
    if under is not None:
        node["under"] = under
    node["added"] = added
    seq.append(node)
    return new_id


def _locate_suggestion(doc: Any, project_id: str, suggestion_id: str) -> tuple[Any, int]:
    """(suggestions list, index) of a suggestion; EditError 404 when the project has no such suggestion."""
    require_slug(suggestion_id, "suggestion id")
    seq = doc.get("suggestions")
    if isinstance(seq, list):
        for index, node in enumerate(seq):
            if hasattr(node, "get") and node.get("id") == suggestion_id:
                return seq, index
    raise EditError(
        f"Suggestion '{suggestion_id}' not found in project '{project_id}'", status_code=404
    )


def _remove_suggestion(doc: Any, seq: Any, index: int) -> Any:
    """Pop one suggestion; drop the `suggestions` key once the list is empty. Returns the node."""
    node = seq.pop(index)
    if not seq:
        doc.pop("suggestions", None)
    return node


def keep_suggestion(doc: Any, project_id: str, suggestion_id: str) -> tuple[str, str]:
    """Move a suggestion from the inbox into the tree as a not-started item.

    It goes under its `under` item, or becomes a new phase/topic when `under` is unset. The
    suggestion's id is reused when free, else a unique slug of its name. Returns (new id, label).
    If `under` no longer exists the document is left unchanged and EditError 422 is raised.
    """
    seq, index = _locate_suggestion(doc, project_id, suggestion_id)
    sug = seq[index]
    under = sug.get("under")
    if under is not None and locate_project_item(doc, str(under)) is None:
        raise EditError(
            f"Cannot keep suggestion '{suggestion_id}': the item it goes under ('{under}') "
            f"no longer exists in project '{project_id}'; dismiss it or re-file it",
            status_code=422,
        )
    item_id = suggestion_id if suggestion_id not in collect_item_ids(doc) else None
    new_id, label = add_project_item(
        doc,
        project_id,
        parent_id=str(under) if under is not None else None,
        name=str(sug.get("name") or ""),
        item_id=item_id,
        status="not_started",
        summary=str(sug.get("summary") or ""),
    )
    _remove_suggestion(doc, seq, index)
    return new_id, label


def dismiss_suggestion(doc: Any, project_id: str, suggestion_id: str) -> None:
    """Drop a suggestion from the project's inbox without adding it to the tree."""
    seq, index = _locate_suggestion(doc, project_id, suggestion_id)
    _remove_suggestion(doc, seq, index)


# ---------------------------------------------------------------------------
# Learning item mutations
# ---------------------------------------------------------------------------


def require_learning_item(doc: Any, path_id: str, item_id: str) -> Any:
    item = find_learning_item(doc, item_id)
    if item is None:
        raise EditError(f"Item '{item_id}' not found in learning path '{path_id}'", status_code=404)
    return item


def set_learning_item_status(doc: Any, path_id: str, item_id: str, status: str) -> None:
    status = require_status(status, LEARNING_ITEM_STATUSES)
    require_learning_item(doc, path_id, item_id)["status"] = status


def set_learning_item_fields(doc: Any, path_id: str, item_id: str, fields: dict[str, Any]) -> None:
    is_topic = any(t.get("id") == item_id for t in children(doc, ("topics",)))
    item = require_learning_item(doc, path_id, item_id)
    apply_text_fields(item, fields, field_order(Topic if is_topic else Subtopic))
