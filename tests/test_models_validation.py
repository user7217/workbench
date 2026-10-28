"""Unit tests for models, YAML round-trip, schema generation, validation, privacy, semantic diff, and identity (§14)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from workbench.gitops import commit_all, get_root_commit, init_repo, run_git
from workbench.identity import AmbiguousRepoIdentityError, find_project_for_repo, parse_github_slug
from workbench.models import LearningPath, Project, generate_json_schemas
from workbench.privacy import scan_data_structure, scan_text_value
from workbench.semantic import check_semantic_diff
from workbench.validate import (
    validate_any_file,
    validate_changed_paths,
    validate_learning_file,
    validate_project_file,
)
from workbench.yamlio import dump_yaml_roundtrip, load_yaml_roundtrip, load_yaml_str_roundtrip, to_plain_python

REPO_ROOT = Path(__file__).resolve().parent.parent
INVALID_FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "invalid"


def test_committed_schemas_and_ui_types_up_to_date() -> None:
    """Verify schema/*.json and generated UI TypeScript types match models.py (§5.3, §15)."""
    schema_dir = REPO_ROOT / "schema"
    expected = generate_json_schemas()
    for filename, schema_dict in expected.items():
        sfile = schema_dir / filename
        assert sfile.exists(), f"Missing schema file {sfile}"
        committed = json.loads(sfile.read_text(encoding="utf-8"))
        assert committed == schema_dict, f"Committed {filename} is out of date with models.py"

    # Also verify UI TypeScript types exist and contain generated interfaces
    proj_dts = REPO_ROOT / "web" / "src" / "types" / "project.d.ts"
    learn_dts = REPO_ROOT / "web" / "src" / "types" / "learning.d.ts"
    assert proj_dts.exists(), "Missing generated web/src/types/project.d.ts"
    assert learn_dts.exists(), "Missing generated web/src/types/learning.d.ts"
    assert "export interface Project" in proj_dts.read_text(encoding="utf-8")
    assert "export interface LearningPath" in learn_dts.read_text(encoding="utf-8")


def test_phased_and_non_phased_project_models() -> None:
    """Verify Project supports both uses_phases: true (phases) and uses_phases: false (direct topics)."""
    phased = Project.model_validate(
        {
            "schema_version": 1,
            "id": "example-phased",
            "name": "Example Phased Project",
            "description": "Project divided into phases.",
            "status": "active",
            "origin": "manual",
            "updated": "2026-10-20",
            "uses_phases": True,
            "repos": [],
            "links": [],
            "phases": [
                {
                    "id": "research",
                    "name": "Research",
                    "status": "in_progress",
                    "summary": "Initial architecture investigation.",
                    "topics": [
                        {
                            "id": "proto-node",
                            "name": "Prototype node",
                            "status": "done",
                            "summary": "Breadboard prototype.",
                        }
                    ],
                }
            ],
        }
    )
    assert phased.uses_phases is True
    assert len(phased.phases) == 1
    assert phased.phases[0].topics[0].id == "proto-node"

    non_phased = Project.model_validate(
        {
            "schema_version": 1,
            "id": "example-non-phased",
            "name": "Example Non-Phased Project",
            "description": "Project organized directly into topics without phases.",
            "status": "active",
            "origin": "manual",
            "updated": "2026-10-20",
            "uses_phases": False,
            "repos": [],
            "links": [],
            "phases": [],
            "topics": [
                {
                    "id": "architecture",
                    "name": "Architecture",
                    "description": "System design and component relationships.",
                    "subtopics": [
                        {
                            "id": "event-bus",
                            "name": "Event bus",
                            "status": "in_progress",
                            "summary": "Zero-copy message ring.",
                        }
                    ],
                }
            ],
        }
    )
    assert non_phased.uses_phases is False
    assert len(non_phased.phases) == 0
    assert len(non_phased.topics) == 1
    assert non_phased.topics[0].id == "architecture"
    assert non_phased.topics[0].subtopics[0].id == "event-bus"


def test_yaml_roundtrip_preserves_comments_and_key_order() -> None:
    """YAML round-trip with ruamel.yaml keeps key order and comments with no spurious diff (§14)."""
    original = """schema_version: 1
# Project comment header
id: demo-weather-station
name: Weather Station
description: Solar-powered sensor node.
status: active
origin: agent
updated: 2026-10-20
uses_phases: true
repos:
  - github: someuser/weather-station # inline repo comment
    root_commit: 3f2a9c1e3f2a9c1e3f2a9c1e3f2a9c1e3f2a9c1e
    name: weather-station
links: []
phases: []
"""
    loaded = load_yaml_str_roundtrip(original)
    dumped = dump_yaml_roundtrip(loaded)
    assert dumped == original


@pytest.mark.parametrize(
    "fixture_name,expected_substring",
    [
        ("unknown_field.yaml", "Extra inputs are not permitted"),
        ("bad_id.yaml", "String should match pattern"),
        ("duplicate_ids.yaml", "Duplicate component id: 'comp-dup'"),
        ("dangling_dependency.yaml", "depends on unknown component 'non-existent-comp'"),
        ("cycle.yaml", "Dependency cycle detected: comp-a -> comp-b -> comp-c -> comp-a"),
        ("over_length_note.yaml", "at most 1000 characters"),
        ("http_link.yaml", "https://"),
        ("planted_aws_key.yaml", "aws_access_key"),
        ("planted_user_path.yaml", "local_path_users"),
        ("planted_email.yaml", "email_address"),
    ],
)
def test_invalid_fixtures_fail_validation(fixture_name: str, expected_substring: str) -> None:
    """Every validation rule has a dedicated invalid fixture that fails cleanly (§11, §14)."""
    fpath = INVALID_FIXTURES_DIR / fixture_name
    assert fpath.exists()
    res = validate_any_file(fpath)
    assert not res.ok, f"Expected {fixture_name} to fail validation"
    joined = "\n".join(res.errors)
    assert expected_substring in joined, f"Expected '{expected_substring}' in:\n{joined}"


def test_semantic_and_disallowed_path_fixtures() -> None:
    """Test renamed ID, mass deletion (>30%), backward status warning, and disallowed path fixtures (§6.3, §11)."""
    sem_path = INVALID_FIXTURES_DIR / "semantic_and_paths.yaml"
    data = to_plain_python(load_yaml_roundtrip(sem_path))
    baseline = data["baseline"]
    renamed = data["renamed_id"]
    mass_del = data["mass_deletion"]
    bad_path = data["disallowed_path"]

    # 1. Renamed ID -> error even if allow_removals is False or True
    r1 = check_semantic_diff(baseline, renamed, allow_removals=False)
    assert not r1.ok
    assert any("IDs must not change" in e for e in r1.errors)

    # 2. Mass deletion (>30%) -> error unless allow_removals=True
    r2 = check_semantic_diff(baseline, mass_del, allow_removals=False)
    assert not r2.ok
    assert any("Mass deletion detected" in e for e in r2.errors)

    r2_allowed = check_semantic_diff(baseline, mass_del, allow_removals=True)
    assert r2_allowed.ok

    # 3. Backward status -> warning only
    backward = json.loads(json.dumps(baseline))
    backward["phases"][0]["status"] = "not_started"
    r3 = check_semantic_diff(baseline, backward, allow_removals=False)
    assert r3.ok
    assert any("Status moved backwards" in w for w in r3.warnings)

    # 4. Disallowed path
    path_res = validate_changed_paths([bad_path])
    assert not path_res.ok
    assert any("Disallowed path" in e for e in path_res.errors)


def test_privacy_scan_never_echoes_matched_secret_values() -> None:
    """Privacy scan catches secrets/PII/paths/IPs and NEVER echoes the matched value (§7.1, §14)."""
    secret_aws = "AKIAIOSFODNN7EXAMPLE"
    secret_gh = "ghp_1234567890abcdefghijklmnopqrstuv"
    secret_sk = "sk-proj-9876543210abcdefghijklmnop"
    secret_path = "/Users/secretuser/private_folder/key.pem"
    secret_home = "/home/secretlinux/token.txt"
    secret_win = r"C:\Users\secretwin\creds.json"
    secret_tilde = "~/my_private_notes/todo.md"
    secret_email = "real.person.name@privatecorp.org"
    secret_ip = "192.168.42.99"
    secret_host = "nas-box.local"
    secret_conn = "postgres://dbadmin:SuperSecretPass123@db.internal:5432/prod"
    secret_pwd = "password = UltraSecretVal99"

    payload = {
        "aws": f"key is {secret_aws}",
        "gh": f"token {secret_gh}",
        "sk": f"openai {secret_sk}",
        "path1": f"found at {secret_path}",
        "path2": f"linux {secret_home}",
        "path3": f"win {secret_win}",
        "path4": f"tilde {secret_tilde}",
        "email": f"mail {secret_email}",
        "allowed_email": "contact test@example.invalid and user@example.com",
        "ip": f"server at {secret_ip}",
        "host": f"connect to {secret_host}",
        "conn": f"url {secret_conn}",
        "pwd": secret_pwd,
    }

    issues = scan_data_structure(payload)
    field_paths = {iss.field_path for iss in issues}
    assert "aws" in field_paths
    assert "gh" in field_paths
    assert "sk" in field_paths
    assert "path1" in field_paths
    assert "path2" in field_paths
    assert "path3" in field_paths
    assert "path4" in field_paths
    assert "email" in field_paths
    assert "allowed_email" not in field_paths
    assert "ip" in field_paths
    assert "host" in field_paths
    assert "conn" in field_paths
    assert "pwd" in field_paths

    formatted = "\n".join(iss.format_error() for iss in issues)
    for sensitive_val in [
        secret_aws,
        secret_gh,
        secret_sk,
        secret_path,
        secret_home,
        secret_win,
        secret_tilde,
        secret_email,
        secret_ip,
        secret_host,
        "SuperSecretPass123",
        "UltraSecretVal99",
    ]:
        assert sensitive_val not in formatted, f"Leaked sensitive value {sensitive_val} in error output!"


def test_repo_identity_root_commit_multi_root_and_fork_collision(tmp_path: Path) -> None:
    """Test root commit extraction, lexicographically smallest multi-root selection, and fork collision (§5.4, §14)."""
    repo = tmp_path / "multi_root_repo"
    init_repo(repo, branch="main")

    # Empty repo -> None
    assert get_root_commit(repo) is None

    (repo / "a.txt").write_text("first root\n", encoding="utf-8")
    root1 = commit_all(repo, "First root commit")
    assert get_root_commit(repo) == root1

    # Create an orphan branch with a second root commit and merge it
    run_git(["checkout", "--orphan", "second-root"], cwd=repo)
    run_git(["rm", "-rf", "."], cwd=repo)
    (repo / "b.txt").write_text("second root\n", encoding="utf-8")
    root2 = commit_all(repo, "Second root commit")
    run_git(["checkout", "main"], cwd=repo)
    run_git(["merge", "--allow-unrelated-histories", "second-root", "-m", "Merge roots"], cwd=repo)

    expected_smallest = min(root1, root2)
    assert get_root_commit(repo) == expected_smallest

    # Test fork collision disambiguation
    p1 = Project.model_validate(
        {
            "schema_version": 1,
            "id": "proj-one",
            "name": "Proj One",
            "description": "",
            "status": "active",
            "origin": "manual",
            "updated": "2026-10-20",
            "repos": [{"github": "alice/widget", "root_commit": root1, "name": "widget"}],
            "links": [],
            "phases": [],
        }
    )
    p2 = Project.model_validate(
        {
            "schema_version": 1,
            "id": "proj-two",
            "name": "Proj Two",
            "description": "",
            "status": "active",
            "origin": "manual",
            "updated": "2026-10-20",
            "repos": [{"github": "bob/widget", "root_commit": root1, "name": "widget"}],
            "links": [],
            "phases": [],
        }
    )

    # Disambiguates by github slug
    matched = find_project_for_repo([p1, p2], root_commit=root1, github_slug="bob/widget")
    assert matched is not None and matched.id == "proj-two"

    # Fails clearly when still ambiguous
    with pytest.raises(AmbiguousRepoIdentityError, match="Ambiguous repo identity"):
        find_project_for_repo([p1, p2], root_commit=root1, github_slug="charlie/widget")


def test_chat_prompt_example_is_a_valid_project() -> None:
    """The format example in agent/chat/PROMPT.md must stay valid against the Project model."""
    import re

    from ruamel.yaml import YAML

    prompt = (Path(__file__).resolve().parent.parent / "agent" / "chat" / "PROMPT.md").read_text(
        encoding="utf-8"
    )
    example = re.search(r"```yaml\n(.*?)```", prompt, re.S).group(1)
    data = YAML(typ="safe").load(example)
    assert "updated" not in data and "origin" not in data  # the CLI sets these
    data["updated"] = "2026-10-28"
    Project.model_validate(data)


def _write_project(
    repo: Path, pid: str, parent: str | None = None, under: str | None = None
) -> None:
    """Project with phase `phase-<pid>` > component `part-<pid>` > nested component `bolt-<pid>`."""
    link = f"parent: {parent}\nunder: {under}\n" if parent else ""
    pdir = repo / "projects" / pid
    pdir.mkdir(parents=True)
    (pdir / "project.yaml").write_text(
        f"schema_version: 1\nid: {pid}\nname: {pid}\nstatus: active\nupdated: 2026-10-28\n{link}"
        f"phases:\n  - id: phase-{pid}\n    name: Phase\n    components:\n"
        f"      - id: part-{pid}\n        name: Part\n        components:\n"
        f"          - id: bolt-{pid}\n            name: Bolt\n",
        encoding="utf-8",
    )


def test_child_projects_name_an_existing_parent_and_item_with_no_cycles(tmp_path: Path) -> None:
    """A child project carries `parent:` + `under:`; repo validation checks them across files."""
    from workbench.validate import validate_data_repo

    def repo_errors(
        name: str, layout: dict[str, tuple[str, str] | None], related: list[str] | None = None
    ) -> list[str]:
        repo = tmp_path / name
        repo.mkdir()
        (repo / "workbench.yaml").write_text("schema_version: 1\n", encoding="utf-8")
        for pid, link in layout.items():
            _write_project(repo, pid, *(link or (None, None)))
        if related is not None:
            ldir = repo / "learning" / "control"
            ldir.mkdir(parents=True)
            (ldir / "path.yaml").write_text(
                "schema_version: 1\nid: control\nname: Control\nstatus: active\n"
                f"updated: 2026-10-28\nrelated_projects: [{', '.join(related)}]\n",
                encoding="utf-8",
            )
        return validate_data_repo(repo).errors

    # Valid: under a phase, under a nested component, a grandchild chain, a related project
    assert repo_errors(
        "ok",
        {"farm": None, "robot": ("farm", "phase-farm"), "sim": ("robot", "bolt-robot")},
        related=["farm", "sim"],
    ) == []

    missing = repo_errors("missing", {"robot": ("no-such-farm", "phase-x")})
    assert any("robot" in e and "no-such-farm" in e and "does not exist" in e for e in missing)

    bad_under = repo_errors("under", {"farm": None, "robot": ("farm", "nowhere")})
    assert any(
        "robot" in e and "farm" in e and "nowhere" in e and "not a phase or component" in e
        for e in bad_under
    )

    cycle = repo_errors("cycle", {"a": ("b", "phase-b"), "b": ("a", "part-a")})
    assert sum("cycle" in e for e in cycle) == 1
    assert any("a -> b -> a" in e or "b -> a -> b" in e for e in cycle)

    bad_related = repo_errors("related", {"farm": None}, related=["farm", "ghost"])
    assert any("control" in e and "ghost" in e and "does not exist" in e for e in bad_related)

    # parent/under must come together, and a project can't be its own parent (model-level)
    with pytest.raises(ValueError, match="together"):
        Project.model_validate(
            {"id": "x", "name": "x", "status": "active", "updated": "2026-10-28", "parent": "farm"}
        )


def test_semantic_diff_sees_nested_components() -> None:
    """Removing a nested component is caught without --allow-removals; moving one is not a removal."""
    def project(children: list[dict]) -> dict:
        return {
            "id": "farm",
            "phases": [{"id": "build", "name": "Build", "components": [
                {"id": "chassis", "name": "Chassis", "components": children},
            ]}],
        }

    wheel = {"id": "wheel", "name": "Wheel", "components": [{"id": "hub", "name": "Hub"}]}
    old = project([wheel])

    removed = project([{"id": "wheel", "name": "Wheel"}])
    res = check_semantic_diff(old, removed, allow_removals=False)
    assert any("'hub'" in e and "--allow-removals" in e for e in res.errors)
    assert check_semantic_diff(old, removed, allow_removals=True).ok

    renamed = project([{"id": "wheel", "name": "Wheel", "components": [{"id": "hub-2", "name": "Hub"}]}])
    assert any("renamed" in e for e in check_semantic_diff(old, renamed).errors)

    moved = project([{"id": "wheel", "name": "Wheel"}, {"id": "hub", "name": "Hub"}])
    assert check_semantic_diff(old, moved).ok

    # The >30% deletion check counts text inside nested components
    wordy = project([dict(wheel, notes="x" * 400)])
    assert any("Mass deletion" in e for e in check_semantic_diff(wordy, old).errors)


def test_privacy_scan_reaches_nested_components() -> None:
    """Deterministic scan and the LLM free-text diff both include components nested at any depth."""
    from workbench.privacy import build_free_text_diff

    secret_path = "/Users/someone/private/keys.txt"
    data = {
        "id": "farm",
        "phases": [{"id": "build", "name": "Build", "components": [
            {"id": "chassis", "name": "Chassis", "components": [
                {"id": "wheel", "name": "Wheel", "notes": f"calibration in {secret_path}"},
            ]},
        ]}],
    }
    issues = scan_data_structure(data)
    assert any(
        i.field_path == "phases[0].components[0].components[0].notes" and i.pattern_type.startswith("local_path")
        for i in issues
    ), [(i.field_path, i.pattern_type) for i in issues]

    diff = build_free_text_diff(None, data)
    assert "phases[build].components[chassis].components[wheel].notes" in diff
