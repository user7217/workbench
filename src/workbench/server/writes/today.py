"""Today's Work: replace the stored list of chosen roots, or turn it on or off (today.yaml).

The on/off switch is `enabled` in today.yaml, so it applies to every device and server reading
the data repository. Turning it off keeps the list; while it is off the list cannot be changed.
Only the roots are stored; the UI expands each into its children and prerequisites. Entries
are not looked up in the repository, and none of the project deletion guards apply: entries
leaving the list is the normal case.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any, get_args

from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from workbench.edits import field_order as _field_order
from workbench.edits import require_slug as _require_slug
from workbench.edits import set_key as _set_key
from workbench.models import DATE_PATTERN, MAX_TODAY_ENTRIES, Today, TodayKind
from workbench.server.sync import ServerState
from workbench.server.writes._transaction import (
    WriteError,
    _edit_message,
    _reject_unknown_keys,
    _require_body,
    _run_transaction,
)
from workbench.validate import TODAY_FILE, validate_today_file
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_roundtrip, parse_iso_date_for_yaml

_BODY_KEYS: tuple[str, ...] = ("day", "entries")
_ENABLED_KEYS: tuple[str, ...] = ("enabled",)
TODAY_OFF_DETAIL = "Today's Work is turned off"
_ENTRY_KEYS: tuple[str, ...] = ("kind", "id", "item")
_KINDS: tuple[str, ...] = get_args(TodayKind)
_DATE_RE = re.compile(DATE_PATTERN)
_SCOPE = "today"

# (kind, id, item): what makes two entries the same entry
EntryKey = tuple[str, str, str | None]


def entry_ref(key: EntryKey) -> str:
    """An entry as a commit scope: <project>[/<item>] or learning/<path>[/<item>]."""
    kind, entity_id, item = key
    ref = entity_id if kind == "project" else f"learning/{entity_id}"
    return f"{ref}/{item}" if item else ref


# ---------------------------------------------------------------------------
# Request body checks
# ---------------------------------------------------------------------------


def _require_day(value: object) -> str:
    """A real calendar day as YYYY-MM-DD."""
    if isinstance(value, str) and _DATE_RE.match(value):
        try:
            parse_iso_date_for_yaml(value)
        except ValueError:
            pass
        else:
            return value
    raise WriteError(f"Invalid day: {value!r} (expected YYYY-MM-DD)", status_code=400)


def _require_entry(value: object, index: int) -> EntryKey:
    if not isinstance(value, dict):
        raise WriteError(f"Entry {index} must be a JSON object", status_code=400)
    _reject_unknown_keys(value, _ENTRY_KEYS)
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        raise WriteError(
            f"Invalid kind: {kind!r} (expected one of {', '.join(_KINDS)})", status_code=400
        )
    entity_id = _require_slug(value.get("id"), "id")
    item = value.get("item")
    if item is not None:
        _require_slug(item, "item")
    return kind, entity_id, item


def _require_entries(value: object) -> list[EntryKey]:
    if not isinstance(value, list):
        raise WriteError("'entries' must be a list", status_code=400)
    if len(value) > MAX_TODAY_ENTRIES:
        raise WriteError(
            f"Too many entries: {len(value)} (at most {MAX_TODAY_ENTRIES})", status_code=400
        )
    keys: list[EntryKey] = []
    for index, raw in enumerate(value):
        key = _require_entry(raw, index)
        if key in keys:
            raise WriteError(f"Duplicate entry: {key[0]} '{entry_ref(key)}'", status_code=400)
        keys.append(key)
    return keys


# ---------------------------------------------------------------------------
# Document edit
# ---------------------------------------------------------------------------


def _load_today_doc(path: Path) -> CommentedMap:
    """The stored today.yaml, or a new document when it is missing or unreadable.

    The write replaces the whole list, so a damaged file is rebuilt instead of blocking it.
    """
    if path.is_file():
        try:
            doc = load_yaml_roundtrip(path)
        except YAMLError:
            doc = None
        if isinstance(doc, CommentedMap):
            return doc
    return CommentedMap(schema_version=1)


def _is_date_value(value: object) -> bool:
    """A YAML date, or a quoted YYYY-MM-DD string (both forms are valid in today.yaml)."""
    if isinstance(value, datetime.date):
        return not isinstance(value, datetime.datetime)
    return isinstance(value, str) and bool(_DATE_RE.match(value))


def _stored_added(doc: CommentedMap) -> dict[EntryKey, Any]:
    """The `added` value of every stored entry, as loaded, so a kept entry is written back unchanged."""
    out: dict[EntryKey, Any] = {}
    entries = doc.get("entries")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not _is_date_value(entry.get("added")):
            continue
        kind, entity_id, item = entry.get("kind"), entry.get("id"), entry.get("item")
        if isinstance(kind, str) and isinstance(entity_id, str) and isinstance(item, str | None):
            out[(kind, entity_id, item)] = entry["added"]
    return out


def _entry_node(key: EntryKey, added: Any) -> CommentedMap:
    kind, entity_id, item = key
    node = CommentedMap(kind=kind, id=entity_id)
    if item is not None:
        node["item"] = item
    node["added"] = added
    return node


def replace_entries(doc: CommentedMap, day: str, keys: list[EntryKey]) -> list[EntryKey]:
    """Set the day and the entries (in the given order) in place; returns the keys stored before.

    An entry that was already stored keeps its `added` date; a new one gets `day`.
    """
    stored = _stored_added(doc)
    order = _field_order(Today)
    _set_key(doc, "schema_version", 1, order)
    # One date object per place it is written: ruamel turns a shared object into a YAML alias
    _set_key(doc, "day", parse_iso_date_for_yaml(day), order)
    nodes = CommentedSeq(
        _entry_node(key, stored[key] if key in stored else parse_iso_date_for_yaml(day))
        for key in keys
    )
    _set_key(doc, "entries", nodes, order)
    return list(stored)


def describe_change(old: list[EntryKey], new: list[EntryKey], day: str) -> str:
    """Commit summary: "clear", "add <entry>", "remove <entry>", or "set N entries for <day>"."""
    if not new:
        return "clear"
    added = [key for key in new if key not in old]
    removed = [key for key in old if key not in new]
    if len(added) == 1 and not removed:
        return f"add {entry_ref(added[0])}"
    if len(removed) == 1 and not added:
        return f"remove {entry_ref(removed[0])}"
    noun = "entry" if len(new) == 1 else "entries"
    return f"set {len(new)} {noun} for {day}"


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def _is_turned_off(doc: CommentedMap) -> bool:
    """Whether the stored list has Today's Work turned off (`enabled: false`; missing means on)."""
    return doc.get("enabled") is False


def set_today(state: ServerState, body: object) -> dict[str, Any]:
    """Replace the Today's Work list with the body's entries; creates today.yaml if missing.

    Refused with 403 while Today's Work is turned off. The check reads today.yaml as it is on
    origin, inside the write, so a switch made on another device is respected.
    """
    data = _require_body(body)
    _reject_unknown_keys(data, _BODY_KEYS)
    for key in _BODY_KEYS:
        if key not in data:
            raise WriteError(f"'{key}' is required", status_code=400)
    day = _require_day(data["day"])
    keys = _require_entries(data["entries"])

    def edit(wt_dir: Path) -> str:
        target = wt_dir / TODAY_FILE
        doc = _load_today_doc(target)
        if _is_turned_off(doc):
            raise WriteError(TODAY_OFF_DETAIL, status_code=403)
        old_keys = replace_entries(doc, day, keys)
        dump_yaml_roundtrip(doc, target)

        vres, _ = validate_today_file(target)
        if not vres.ok:
            raise WriteError(f"Validation failed: {'; '.join(vres.errors)}", status_code=422)
        return _edit_message(describe_change(old_keys, keys, day), _SCOPE)

    return _run_transaction(state, _SCOPE, edit)


def set_today_enabled(state: ServerState, body: object) -> dict[str, Any]:
    """Turn Today's Work on or off (`enabled` in today.yaml); the day and the entries are kept.

    Body: {"enabled": true|false}. Creates today.yaml if missing. Always allowed, so a list
    that is turned off can be turned back on. Setting the value already stored commits nothing.
    """
    data = _require_body(body)
    _reject_unknown_keys(data, _ENABLED_KEYS)
    if "enabled" not in data:
        raise WriteError("'enabled' is required", status_code=400)
    enabled = data["enabled"]
    if not isinstance(enabled, bool):
        raise WriteError(f"'enabled' must be true or false, not {enabled!r}", status_code=400)

    def edit(wt_dir: Path) -> str:
        target = wt_dir / TODAY_FILE
        doc = _load_today_doc(target)
        order = _field_order(Today)
        _set_key(doc, "schema_version", 1, order)
        _set_key(doc, "enabled", enabled, order)
        dump_yaml_roundtrip(doc, target)

        vres, _ = validate_today_file(target)
        if not vres.ok:
            raise WriteError(f"Validation failed: {'; '.join(vres.errors)}", status_code=422)
        return _edit_message("turn on" if enabled else "turn off", _SCOPE)

    return _run_transaction(state, _SCOPE, edit)
