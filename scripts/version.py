"""Keep every version number and the changelog in step.

    uv run python scripts/version.py show              # each file's version
    uv run python scripts/version.py check             # all agree, CHANGELOG has an Unreleased section
    uv run python scripts/version.py check --tag v1.2.3  # also: matches the tag, CHANGELOG has 1.2.3
    uv run python scripts/version.py set 1.2.3         # write 1.2.3 everywhere, date the changelog
    uv run python scripts/version.py notes 1.2.3       # print 1.2.3's changelog section (release notes)

The version is written once in src/workbench/__init__.py (pyproject.toml reads it, and
desktop/src-tauri/tauri.conf.json reads desktop/package.json); `set` updates the copies that
cannot point there and the lock files that record them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
CHANGELOG = ROOT / "CHANGELOG.md"
REPO_URL = "https://github.com/user7217/workbench"

INIT = ROOT / "src" / "workbench" / "__init__.py"
INIT_PATTERN = re.compile(r'^__version__ = "([^"]+)"$', re.M)
CARGO_TOML = ROOT / "desktop" / "src-tauri" / "Cargo.toml"
CARGO_TOML_PATTERN = re.compile(r'^(\[package\][^\[]*?^version = ")([^"]+)(")', re.M | re.S)
CARGO_LOCK = ROOT / "desktop" / "src-tauri" / "Cargo.lock"
CARGO_LOCK_PATTERN = re.compile(r'(name = "workbench-desktop"\nversion = ")([^"]+)(")')
NPM_PACKAGES = [ROOT / "web", ROOT / "desktop"]


def current_versions() -> dict[str, str]:
    """Version recorded in each file that holds one."""
    found: dict[str, str] = {}
    found["src/workbench/__init__.py"] = INIT_PATTERN.search(INIT.read_text()).group(1)
    for pkg in NPM_PACKAGES:
        rel = pkg.relative_to(ROOT)
        found[f"{rel}/package.json"] = json.loads((pkg / "package.json").read_text())["version"]
        lock = json.loads((pkg / "package-lock.json").read_text())
        found[f"{rel}/package-lock.json"] = lock["version"]
    found["desktop/src-tauri/Cargo.toml"] = CARGO_TOML_PATTERN.search(CARGO_TOML.read_text()).group(
        2
    )
    found["desktop/src-tauri/Cargo.lock"] = CARGO_LOCK_PATTERN.search(CARGO_LOCK.read_text()).group(
        2
    )
    return found


def changelog_section(version: str) -> str | None:
    """Body of the `## [version]` section, or None if there is no such heading."""
    text = CHANGELOG.read_text()
    match = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.M | re.S
    )
    return None if match is None else match.group(1).strip()


def cmd_show(_: argparse.Namespace) -> int:
    for path, version in current_versions().items():
        print(f"{version:<12} {path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems: list[str] = []
    versions = current_versions()
    distinct = set(versions.values())
    if len(distinct) != 1:
        problems.append("versions differ: " + ", ".join(f"{p}={v}" for p, v in versions.items()))
    version = versions["src/workbench/__init__.py"]
    if changelog_section("Unreleased") is None:
        problems.append("CHANGELOG.md has no '## [Unreleased]' section")
    if args.tag:
        tag_version = args.tag.removeprefix("refs/tags/").removeprefix("v")
        if tag_version != version:
            problems.append(f"tag {args.tag} does not match version {version}")
        notes = changelog_section(tag_version)
        if notes is None:
            problems.append(f"CHANGELOG.md has no '## [{tag_version}]' section")
        elif not notes:
            problems.append(f"CHANGELOG.md section for {tag_version} is empty")
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print(f"ok: {version}" + (f", matches {args.tag}" if args.tag else ""))
    return 1 if problems else 0


def _sub(path: Path, pattern: re.Pattern[str], replacement: str) -> None:
    text, count = pattern.subn(replacement, path.read_text())
    if count != 1:
        raise SystemExit(f"error: expected one version in {path.relative_to(ROOT)}, found {count}")
    path.write_text(text)


def _date_changelog(version: str) -> None:
    """Turn the Unreleased section into `## [version] - today` and start a new empty one."""
    text = CHANGELOG.read_text()
    if changelog_section(version) is not None:
        print(f"CHANGELOG.md already has a {version} section; left as is")
        return
    if not changelog_section("Unreleased"):
        raise SystemExit(
            "error: the Unreleased section of CHANGELOG.md is empty; describe the release first"
        )
    today = datetime.date.today().isoformat()
    text = text.replace("## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - {today}\n", 1)
    # Compare links at the bottom: Unreleased compares against the new tag
    previous = re.search(
        r"^\[Unreleased\]: .*/compare/(v[^.]+\.[^.]+\.[^.]+)\.\.\.HEAD$", text, re.M
    )
    new_link = (
        f"[{version}]: {REPO_URL}/compare/{previous.group(1)}...v{version}"
        if previous
        else f"[{version}]: {REPO_URL}/releases/tag/v{version}"
    )
    text = re.sub(
        r"^\[Unreleased\]: .*$",
        f"[Unreleased]: {REPO_URL}/compare/v{version}...HEAD\n{new_link}",
        text,
        count=1,
        flags=re.M,
    )
    CHANGELOG.write_text(text)


def cmd_set(args: argparse.Namespace) -> int:
    version = args.version.removeprefix("v")
    if not SEMVER.match(version):
        raise SystemExit(f"error: {version!r} is not a version like 1.2.3")
    # Refuse before touching any file, so a failed set leaves nothing half-changed
    if changelog_section(version) is None and not changelog_section("Unreleased"):
        raise SystemExit(
            "error: the Unreleased section of CHANGELOG.md is empty; describe the release first"
        )
    _sub(INIT, INIT_PATTERN, f'__version__ = "{version}"')
    for pkg in NPM_PACKAGES:
        manifest = pkg / "package.json"
        data = json.loads(manifest.read_text())
        data["version"] = version
        manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        lock_path = pkg / "package-lock.json"
        lock = json.loads(lock_path.read_text())
        lock["version"] = version
        lock["packages"][""]["version"] = version
        lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
    _sub(CARGO_TOML, CARGO_TOML_PATTERN, rf"\g<1>{version}\g<3>")
    _sub(CARGO_LOCK, CARGO_LOCK_PATTERN, rf"\g<1>{version}\g<3>")
    _date_changelog(version)
    subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT, check=True)
    print(
        f"set {version}; next: review the diff, commit, then `git tag v{version}` and push the tag"
    )
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    notes = changelog_section(args.version.removeprefix("v"))
    if notes is None:
        print(f"error: CHANGELOG.md has no section for {args.version}", file=sys.stderr)
        return 1
    print(notes)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show").set_defaults(func=cmd_show)
    check = sub.add_parser("check")
    check.add_argument("--tag", help="release tag such as v1.2.3 (or refs/tags/v1.2.3)")
    check.set_defaults(func=cmd_check)
    set_cmd = sub.add_parser("set")
    set_cmd.add_argument("version")
    set_cmd.set_defaults(func=cmd_set)
    notes = sub.add_parser("notes")
    notes.add_argument("version")
    notes.set_defaults(func=cmd_notes)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
