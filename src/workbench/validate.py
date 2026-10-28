"""Structural and privacy validation for Workbench metadata files and repositories (§6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import ValidationError
from ruamel.yaml.error import YAMLError

from workbench.models import LearningPath, Project, SlugId, WorkbenchRepoRoot, SLUG_PATTERN
from workbench.privacy import scan_data_structure, scan_text_value
from workbench.yamlio import load_yaml_roundtrip, to_plain_python

MAX_TOPIC_MD_BYTES = 20 * 1024
SLUG_RE = re.compile(SLUG_PATTERN)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def merge(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def _format_pydantic_error(err: ValidationError, prefix: str = "") -> list[str]:
    messages: list[str] = []
    for item in err.errors():
        loc = ".".join(str(x) for x in item.get("loc", ()))
        msg = item.get("msg", "validation error")
        full_loc = f"{prefix}:{loc}" if (prefix and loc) else (prefix or loc or "root")
        messages.append(f"{full_loc}: {msg}")
    return messages


def is_allowed_repo_relative_path(rel_path: str) -> bool:
    """Return True if rel_path is an allowed path inside workbench-data (§4.2)."""
    parts = Path(rel_path).parts
    if not parts:
        return False
    if rel_path == "workbench.yaml":
        return True
    if len(parts) == 3 and parts[0] == "projects" and parts[2] == "project.yaml":
        return bool(SLUG_RE.fullmatch(parts[1])) and len(parts[1]) <= 48
    if len(parts) == 3 and parts[0] == "learning" and parts[2] == "path.yaml":
        return bool(SLUG_RE.fullmatch(parts[1])) and len(parts[1]) <= 48
    if (
        len(parts) == 4
        and parts[0] == "learning"
        and parts[2] == "topics"
        and parts[3].endswith(".md")
    ):
        topic_id = parts[3][:-3]
        return (
            bool(SLUG_RE.fullmatch(parts[1]))
            and len(parts[1]) <= 48
            and bool(SLUG_RE.fullmatch(topic_id))
            and len(topic_id) <= 48
        )
    return False


def validate_changed_paths(rel_paths: list[str]) -> ValidationResult:
    """Verify that every changed path is permitted by §4.2."""
    res = ValidationResult()
    for rel in rel_paths:
        norm = rel.strip("/")
        if not is_allowed_repo_relative_path(norm):
            res.errors.append(
                f"Disallowed path '{norm}': pushes may only touch workbench.yaml, projects/<id>/project.yaml, and learning/<id>/**"
            )
    return res


def validate_project_file(
    path: Path, expected_dir_id: str | None = None
) -> tuple[ValidationResult, Project | None, dict[str, Any] | None]:
    """Validate a project.yaml file structurally and for deterministic privacy."""
    res = ValidationResult()
    try:
        raw = load_yaml_roundtrip(path)
    except (YAMLError, OSError) as exc:
        res.errors.append(f"{path}: YAML load error: {exc}")
        return res, None, None

    plain = to_plain_python(raw)
    if not isinstance(plain, dict):
        res.errors.append(f"{path}: Root of project.yaml must be a mapping")
        return res, None, None

    # Always run deterministic privacy scan on raw strings so privacy errors are caught even if schema also fails
    for issue in scan_data_structure(plain):
        res.errors.append(issue.format_error())

    try:
        proj = Project.model_validate(plain)
    except ValidationError as exc:
        res.errors.extend(_format_pydantic_error(exc, str(path.name)))
        return res, None, plain

    dir_id = expected_dir_id
    if dir_id is None and path.name == "project.yaml" and path.parent.parent.name == "projects":
        dir_id = path.parent.name

    if dir_id is not None and proj.id != dir_id:
        res.errors.append(
            f"{path}: Directory name '{dir_id}' must equal project id '{proj.id}'"
        )

    return res, proj, plain


def validate_learning_file(
    path: Path, expected_dir_id: str | None = None, check_topics_dir: bool = True
) -> tuple[ValidationResult, LearningPath | None, dict[str, Any] | None]:
    """Validate a learning path.yaml file (and optional topics/*.md files)."""
    res = ValidationResult()
    try:
        raw = load_yaml_roundtrip(path)
    except (YAMLError, OSError) as exc:
        res.errors.append(f"{path}: YAML load error: {exc}")
        return res, None, None

    plain = to_plain_python(raw)
    if not isinstance(plain, dict):
        res.errors.append(f"{path}: Root of path.yaml must be a mapping")
        return res, None, None

    for issue in scan_data_structure(plain):
        res.errors.append(issue.format_error())

    try:
        lp = LearningPath.model_validate(plain)
    except ValidationError as exc:
        res.errors.extend(_format_pydantic_error(exc, str(path.name)))
        return res, None, plain

    dir_id = expected_dir_id
    if dir_id is None and path.name == "path.yaml" and path.parent.parent.name == "learning":
        dir_id = path.parent.name

    if dir_id is not None and lp.id != dir_id:
        res.errors.append(
            f"{path}: Directory name '{dir_id}' must equal learning path id '{lp.id}'"
        )

    if check_topics_dir and path.name == "path.yaml":
        topics_dir = path.parent / "topics"
        if topics_dir.exists():
            valid_item_ids: set[str] = set()
            for t in lp.topics:
                valid_item_ids.add(t.id)
                for s in t.subtopics:
                    valid_item_ids.add(s.id)

            for md_file in sorted(topics_dir.iterdir()):
                if md_file.name.startswith("."):
                    continue
                if md_file.suffix != ".md" or not md_file.is_file():
                    res.errors.append(f"Disallowed file in topics directory: '{md_file.name}'")
                    continue
                item_id = md_file.stem
                if item_id not in valid_item_ids:
                    res.errors.append(
                        f"{md_file}: Topic markdown '{md_file.name}' does not match any topic or subtopic id in '{lp.id}'"
                    )
                size = md_file.stat().st_size
                if size > MAX_TOPIC_MD_BYTES:
                    res.errors.append(
                        f"{md_file}: Topic markdown exceeds 20 KB limit ({size} bytes)"
                    )
                text = md_file.read_text(encoding="utf-8")
                for issue in scan_text_value(text, f"topics/{md_file.name}"):
                    res.errors.append(issue.format_error())

    return res, lp, plain


def validate_workbench_root_file(path: Path) -> ValidationResult:
    """Validate workbench.yaml at the root of workbench-data."""
    res = ValidationResult()
    if not path.exists():
        res.errors.append(f"Missing required root file: {path}")
        return res
    try:
        raw = load_yaml_roundtrip(path)
    except (YAMLError, OSError) as exc:
        res.errors.append(f"{path}: YAML load error: {exc}")
        return res
    plain = to_plain_python(raw)
    if not isinstance(plain, dict):
        res.errors.append(f"{path}: Root must be a mapping")
        return res
    try:
        WorkbenchRepoRoot.model_validate(plain)
    except ValidationError as exc:
        res.errors.extend(_format_pydantic_error(exc, "workbench.yaml"))
    return res


def validate_any_file(path: Path) -> ValidationResult:
    """Validate a single file based on its filename or content."""
    if path.name == "workbench.yaml":
        return validate_workbench_root_file(path)
    if path.name == "project.yaml":
        res, _, _ = validate_project_file(path)
        return res
    if path.name == "path.yaml":
        res, _, _ = validate_learning_file(path)
        return res
    if path.suffix == ".md":
        res = ValidationResult()
        if path.stat().st_size > MAX_TOPIC_MD_BYTES:
            res.errors.append(f"{path}: Topic markdown exceeds 20 KB limit")
        text = path.read_text(encoding="utf-8")
        for issue in scan_text_value(text, f"topics/{path.name}"):
            res.errors.append(issue.format_error())
        return res

    # Fallback for fixture YAML files with arbitrary names (e.g., tests/fixtures/invalid/*.yaml)
    res = ValidationResult()
    try:
        raw = load_yaml_roundtrip(path)
    except (YAMLError, OSError) as exc:
        res.errors.append(f"{path}: YAML load error: {exc}")
        return res
    plain = to_plain_python(raw)
    if not isinstance(plain, dict):
        res.errors.append(f"{path}: Root must be a mapping")
        return res

    for issue in scan_data_structure(plain):
        res.errors.append(issue.format_error())

    try:
        if (
            "topics" in plain
            and "uses_phases" not in plain
            and "phases" not in plain
            and "repos" not in plain
            and "origin" not in plain
        ):
            LearningPath.model_validate(plain)
        else:
            Project.model_validate(plain)
    except ValidationError as exc:
        res.errors.extend(_format_pydantic_error(exc, path.name))
    return res


def check_project_links(projects: list[Project], known_ids: Optional[set[str]] = None) -> list[str]:
    """Child projects name one parent and the phase/component they sit under; no parent cycles.

    known_ids lists every project id present in the repo (including ones that failed
    validation), so a child of an invalid parent is not misreported as orphaned.
    """
    errors: list[str] = []
    by_id = {p.id: p for p in projects}
    known = set(by_id) | (known_ids or set())

    for proj in projects:
        if proj.parent is None:
            continue
        if proj.parent not in known:
            errors.append(
                f"Project '{proj.id}': parent project '{proj.parent}' does not exist"
            )
            continue
        parent = by_id.get(proj.parent)
        if parent is None:
            continue  # parent exists but is invalid; its own errors are reported
        item_ids = {g.id for g in list(parent.phases) + list(parent.topics)}
        item_ids |= {c.id for c in parent.all_components()}
        if proj.under not in item_ids:
            errors.append(
                f"Project '{proj.id}': 'under: {proj.under}' is not a phase or component "
                f"of parent project '{parent.id}'"
            )

    reported: set[frozenset[str]] = set()
    for start in sorted(by_id):
        chain = [start]
        current = by_id[start].parent
        while current is not None and current in by_id:
            if current in chain:
                cycle = chain[chain.index(current):] + [current]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    errors.append(f"Project parent cycle: {' -> '.join(cycle)}")
                break
            chain.append(current)
            current = by_id[current].parent
    return errors


def check_related_projects(paths: list[LearningPath], project_ids: set[str]) -> list[str]:
    """Learning paths' related_projects must name existing projects."""
    errors: list[str] = []
    for lp in paths:
        for pid in lp.related_projects:
            if pid not in project_ids:
                errors.append(
                    f"Learning path '{lp.id}': related project '{pid}' does not exist"
                )
    return errors


def validate_data_repo(repo_root: Path) -> ValidationResult:
    """Validate an entire workbench-data repository checkout."""
    res = ValidationResult()
    wb_yaml = repo_root / "workbench.yaml"
    res.merge(validate_workbench_root_file(wb_yaml))

    # Check all files in repo_root (excluding .git) for disallowed paths
    for p in sorted(repo_root.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file():
            rel = p.relative_to(repo_root).as_posix()
            if not is_allowed_repo_relative_path(rel):
                res.errors.append(
                    f"Disallowed path '{rel}': only workbench.yaml, projects/<id>/project.yaml, and learning/<id>/** are permitted"
                )

    seen_ids: set[str] = set()
    valid_projects: list[Project] = []
    project_dir_ids: set[str] = set()
    valid_paths: list[LearningPath] = []

    projects_dir = repo_root / "projects"
    if projects_dir.exists():
        for proj_dir in sorted(projects_dir.iterdir()):
            if proj_dir.name.startswith(".") or not proj_dir.is_dir():
                continue
            proj_file = proj_dir / "project.yaml"
            if not proj_file.exists():
                res.errors.append(f"Missing project.yaml in {proj_dir}")
                continue
            project_dir_ids.add(proj_dir.name)
            p_res, proj, _ = validate_project_file(proj_file, expected_dir_id=proj_dir.name)
            res.merge(p_res)
            if proj:
                if proj.id in seen_ids:
                    res.errors.append(f"Duplicate item id across repository: '{proj.id}'")
                seen_ids.add(proj.id)
                valid_projects.append(proj)

    res.errors.extend(check_project_links(valid_projects, project_dir_ids))

    learning_dir = repo_root / "learning"
    if learning_dir.exists():
        for lp_dir in sorted(learning_dir.iterdir()):
            if lp_dir.name.startswith(".") or not lp_dir.is_dir():
                continue
            lp_file = lp_dir / "path.yaml"
            if not lp_file.exists():
                res.errors.append(f"Missing path.yaml in {lp_dir}")
                continue
            l_res, lp, _ = validate_learning_file(
                lp_file, expected_dir_id=lp_dir.name, check_topics_dir=True
            )
            res.merge(l_res)
            if lp:
                if lp.id in seen_ids:
                    res.errors.append(f"Duplicate item id across repository: '{lp.id}'")
                seen_ids.add(lp.id)
                valid_paths.append(lp)

    res.errors.extend(check_related_projects(valid_paths, project_dir_ids))
    return res
