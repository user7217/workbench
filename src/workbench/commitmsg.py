"""Standard commit messages for every commit Workbench makes to workbench-data.

Format: ``<kind>(<scope>): <summary>``, or ``<kind>: <summary>`` when there is no single scope.
A scope is a project id, ``<project>/<item>``, ``learning/<id>`` or ``learning/<id>/<item>``.

Kinds (who writes them):
    create   workbench new
    update   workbench set / add (quick per-message updates)
    review   workbench push (full review by an agent)
    edit     dashboard edits through the server
    import   workbench learning import
    attach   workbench attach
    detach   workbench detach
    link     workbench link
    archive  workbench archive-empty
    discover local / GitHub discovery registrations
    undo     workbench undo
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

KINDS: tuple[str, ...] = (
    "create",
    "update",
    "review",
    "edit",
    "import",
    "attach",
    "detach",
    "link",
    "archive",
    "discover",
    "undo",
)

MESSAGE_RE = re.compile(
    r"^(?P<kind>[a-z]+)(?:\((?P<scope>[a-z0-9][a-z0-9/-]*)\))?: (?P<summary>\S.*)$"
)


@dataclass(frozen=True)
class ParsedMessage:
    kind: str
    scope: str | None
    summary: str


def build_message(kind: str, summary: str, scope: str | None = None) -> str:
    """Build a one-line commit subject; newlines in the summary are folded into spaces."""
    if kind not in KINDS:
        raise ValueError(f"Unknown commit kind {kind!r} (expected one of {', '.join(KINDS)})")
    text = " ".join(summary.split()) or kind
    return f"{kind}({scope}): {text}" if scope else f"{kind}: {text}"


def parse_message(subject: str) -> ParsedMessage | None:
    """Parse a commit subject in the standard format; None for legacy or foreign messages."""
    m = MESSAGE_RE.match(subject.strip())
    if not m or m.group("kind") not in KINDS:
        return None
    return ParsedMessage(m.group("kind"), m.group("scope"), m.group("summary"))


def scope_for_paths(paths: Iterable[str]) -> str | None:
    """The single entity scope (project id or learning/<id>) touched by data-repo paths, if any."""
    scopes: set[str] = set()
    for rel in paths:
        parts = rel.split("/")
        if len(parts) >= 3 and parts[0] == "projects":
            scopes.add(parts[1])
        elif len(parts) >= 3 and parts[0] == "learning":
            scopes.add(f"learning/{parts[1]}")
        else:
            return None
    return scopes.pop() if len(scopes) == 1 else None


def review_message(text: str, scope: str | None) -> str:
    """Message for `workbench push`: kept as is if it already uses a known kind, else review(...)."""
    if parse_message(text) is not None:
        return text.strip()
    return build_message("review", text, scope)


def scope_matches(scope: str | None, wanted: str) -> bool:
    """A project filter matches the scope itself or anything nested under it (``wanted/...``)."""
    if not scope:
        return False
    wanted = wanted.strip("/")
    return scope == wanted or scope.startswith(wanted + "/")


def describe_item_change(status: str | None = None, fields: dict[str, object] | None = None) -> str:
    """Short summary of an item edit, e.g. "status done, notes" or "clear summary"."""
    parts: list[str] = []
    if status is not None:
        parts.append(f"status {status}")
    for key, value in (fields or {}).items():
        parts.append(f"clear {key}" if value in ("", []) and key != "name" else key)
    return ", ".join(parts) or "no change"
