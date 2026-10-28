"""YAML round-trip load and dump utilities using ruamel.yaml."""

from __future__ import annotations

import datetime
from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def _make_yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def load_yaml_roundtrip(path: Path | str) -> Any:
    """Load YAML preserving comments and key order."""
    yaml = _make_yaml()
    if isinstance(path, Path):
        text = path.read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return yaml.load(StringIO(text))


def load_yaml_str_roundtrip(text: str) -> Any:
    """Load YAML from a string in round-trip mode."""
    yaml = _make_yaml()
    return yaml.load(StringIO(text))


def dump_yaml_roundtrip(data: Any, path: Path | None = None) -> str:
    """Dump YAML preserving comments and key order."""
    yaml = _make_yaml()
    buf = StringIO()
    yaml.dump(data, buf)
    out = buf.getvalue()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out, encoding="utf-8")
    return out


def to_plain_python(obj: Any) -> Any:
    """Recursively convert ruamel CommentedMap/CommentedSeq to plain dict/list for Pydantic."""
    if isinstance(obj, (CommentedMap, dict)):
        return {str(k): to_plain_python(v) for k, v in obj.items()}
    if isinstance(obj, (CommentedSeq, list)):
        return [to_plain_python(v) for v in obj]
    if isinstance(obj, datetime.date) and not isinstance(obj, datetime.datetime):
        return obj.isoformat()
    return obj


def parse_iso_date_for_yaml(date_str: str) -> datetime.date:
    """Convert YYYY-MM-DD string to datetime.date so ruamel dumps it unquoted like 2026-10-20."""
    return datetime.date.fromisoformat(date_str)
