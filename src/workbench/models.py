"""Pydantic v2 models for Workbench (single source of truth for schema_version 1)."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
SHA_PATTERN = r"^[0-9a-f]{7,64}$"
DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"


def _coerce_date(v: Any) -> Any:
    if isinstance(v, datetime.date) and not isinstance(v, datetime.datetime):
        return v.isoformat()
    return v


def _check_https_url(v: str) -> str:
    if not v.startswith("https://") or len(v) <= len("https://"):
        raise ValueError("URL must start with https:// and include a host")
    return v


SlugId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=48, pattern=SLUG_PATTERN),
]
CommitSha = Annotated[
    str,
    StringConstraints(min_length=7, max_length=64, pattern=SHA_PATTERN),
]
FullCommitSha = Annotated[
    str,
    StringConstraints(min_length=7, max_length=64, pattern=SHA_PATTERN),
]
DateStr = Annotated[
    str,
    BeforeValidator(_coerce_date),
    StringConstraints(pattern=DATE_PATTERN),
]
HttpsUrl = Annotated[
    str,
    StringConstraints(min_length=9, max_length=500),
    AfterValidator(_check_https_url),
]

ShortName = Annotated[str, StringConstraints(min_length=1, max_length=80)]
LabelStr = Annotated[str, StringConstraints(min_length=1, max_length=40)]
DescriptionStr = Annotated[str, StringConstraints(max_length=500)]
SummaryStr = Annotated[str, StringConstraints(max_length=300)]
NotesStr = Annotated[str, StringConstraints(max_length=1000)]

# planned = not started yet; a child project only shows in the Projects tab while active
ProjectStatus = Literal["planned", "active", "paused", "done", "archived"]
ProjectOrigin = Literal["manual", "agent", "discovered"]
PhaseStatus = Literal["not_started", "in_progress", "done", "parked"]
LearningItemStatus = Literal["not_started", "in_progress", "done"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkbenchRepoRoot(StrictModel):
    schema_version: Literal[1] = 1


class Link(StrictModel):
    label: LabelStr
    url: HttpsUrl


class ProjectRepo(StrictModel):
    github: Optional[Annotated[str, StringConstraints(min_length=3, max_length=120)]] = None
    root_commit: FullCommitSha
    name: ShortName


class Component(StrictModel):
    id: SlugId
    name: ShortName
    status: PhaseStatus = "not_started"
    summary: SummaryStr = ""
    description: DescriptionStr = ""
    notes: NotesStr = ""
    depends_on: list[SlugId] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    refs: list[CommitSha] = Field(default_factory=list)
    items: list[SummaryStr] = Field(default_factory=list)
    # Components nest to any depth; ids stay unique across the whole project
    components: list["Component"] = Field(default_factory=list)


def iter_components(items: list[Component]):
    """Yield every component in items and all of its nested components, depth first."""
    for comp in items:
        yield comp
        yield from iter_components(comp.components)


class Phase(StrictModel):
    id: SlugId
    name: ShortName
    status: PhaseStatus = "not_started"
    summary: SummaryStr = ""
    description: DescriptionStr = ""
    notes: NotesStr = ""
    components: list[Component] = Field(default_factory=list)
    topics: list[Component] = Field(default_factory=list)
    subtopics: list[Component] = Field(default_factory=list)


class Suggestion(StrictModel):
    """A suggested next step waiting in the project's inbox: Keep adds it to the tree, Dismiss drops it."""

    id: SlugId
    name: ShortName
    summary: SummaryStr = ""
    # The phase/topic/component it would go under; None means a new phase/topic
    under: Optional[SlugId] = None
    added: DateStr


class Project(StrictModel):
    schema_version: Literal[1] = 1
    id: SlugId
    name: ShortName
    description: DescriptionStr = ""
    status: ProjectStatus
    origin: ProjectOrigin = "manual"
    updated: DateStr
    uses_phases: bool = True
    repos: list[ProjectRepo] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    phases: list[Phase] = Field(default_factory=list)
    topics: list[Phase] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    # A child project names its single parent and the phase or component it sits under
    parent: Optional[SlugId] = None
    under: Optional[SlugId] = None

    def all_components(self) -> list[Component]:
        """Every component in the project, including nested ones."""
        out: list[Component] = []
        for group in list(self.phases) + list(self.topics):
            out.extend(iter_components(list(group.components) + list(group.topics) + list(group.subtopics)))
        return out

    @model_validator(mode="after")
    def validate_internal_graph(self) -> "Project":
        if (self.parent is None) != (self.under is None):
            raise ValueError("'parent' and 'under' must be set together")
        if self.parent == self.id:
            raise ValueError("A project cannot be its own parent")

        phase_ids: set[str] = set()
        component_ids: set[str] = set()
        all_groups = list(self.phases) + list(self.topics)

        for group in all_groups:
            if group.id in phase_ids:
                raise ValueError(f"Duplicate phase id: '{group.id}'")
            phase_ids.add(group.id)
        all_comps = self.all_components()
        for comp in all_comps:
            if comp.id in component_ids or comp.id in phase_ids:
                raise ValueError(f"Duplicate component id: '{comp.id}'")
            component_ids.add(comp.id)

        adj: dict[str, list[str]] = {}
        for comp in all_comps:
            adj[comp.id] = list(comp.depends_on)
            for dep in comp.depends_on:
                if dep == comp.id:
                    raise ValueError(
                        f"Component '{comp.id}' cannot depend on itself (self-reference)"
                    )
                if dep not in component_ids:
                    raise ValueError(
                        f"Component '{comp.id}' depends on unknown component '{dep}'"
                    )

        suggestion_ids: set[str] = set()
        for sug in self.suggestions:
            if sug.id in suggestion_ids:
                raise ValueError(f"Duplicate suggestion id: '{sug.id}'")
            suggestion_ids.add(sug.id)

        # Cycle detection using DFS with path reporting
        visited: set[str] = set()
        in_stack: dict[str, int] = {}
        stack: list[str] = []

        def dfs(node: str) -> None:
            visited.add(node)
            in_stack[node] = len(stack)
            stack.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    dfs(neighbor)
                elif neighbor in in_stack:
                    idx = in_stack[neighbor]
                    cycle_path = " -> ".join(stack[idx:] + [neighbor])
                    raise ValueError(f"Dependency cycle detected: {cycle_path}")
            stack.pop()
            del in_stack[node]

        for cid in adj:
            if cid not in visited:
                dfs(cid)

        return self


class Subtopic(StrictModel):
    id: SlugId
    name: ShortName
    status: LearningItemStatus
    summary: SummaryStr = ""
    description: DescriptionStr = ""
    notes: NotesStr = ""
    references: list[Link] = Field(default_factory=list)


class Topic(StrictModel):
    id: SlugId
    name: ShortName
    status: LearningItemStatus
    summary: SummaryStr = ""
    description: DescriptionStr = ""
    notes: NotesStr = ""
    references: list[Link] = Field(default_factory=list)
    subtopics: list[Subtopic] = Field(default_factory=list)


class LearningPath(StrictModel):
    schema_version: Literal[1] = 1
    id: SlugId
    name: ShortName
    description: DescriptionStr = ""
    status: ProjectStatus
    updated: DateStr
    references: list[Link] = Field(default_factory=list)
    # Plain references to projects this learning supports; no status flows either way
    related_projects: list[SlugId] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "LearningPath":
        seen: set[str] = set()
        for topic in self.topics:
            if topic.id in seen:
                raise ValueError(f"Duplicate topic/subtopic id: '{topic.id}'")
            seen.add(topic.id)
            for sub in topic.subtopics:
                if sub.id in seen:
                    raise ValueError(f"Duplicate topic/subtopic id: '{sub.id}'")
                seen.add(sub.id)
        return self


def generate_json_schemas() -> dict[str, dict[str, Any]]:
    """Generate JSON Schemas from the Pydantic models."""
    project_schema = Project.model_json_schema()
    project_schema["title"] = "Project"
    learning_schema = LearningPath.model_json_schema()
    learning_schema["title"] = "LearningPath"
    return {
        "project.schema.json": project_schema,
        "learning.schema.json": learning_schema,
    }


def write_json_schemas(schema_dir: Path) -> None:
    """Write generated JSON Schemas to disk."""
    schema_dir.mkdir(parents=True, exist_ok=True)
    for filename, schema in generate_json_schemas().items():
        target = schema_dir / filename
        target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
