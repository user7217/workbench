# Workbench Backend Architecture & Internals (`src/workbench`)

This document is the technical reference for the `src/workbench` Python package. It explains how every backend module fits together, how repository identity and discovery work, how CLI and web edits mutate YAML safely, and how the validation, privacy, and agent hook pipelines operate.

---

## 1. Module Map

```text
src/workbench/
├── __init__.py
├── models.py          # Pydantic v2 models (Project, Phase, Component, Suggestion, LearningPath) & JSON Schema export
├── config.py          # Laptop configuration (~/.workbench/config.toml / config.yaml) & state (~/.workbench/state.json)
├── gitops.py          # Git plumbing (bare repos, clones, temporary worktrees, rebases, root commit extraction)
├── yamlio.py          # Comment- and key-order-preserving YAML loader/dumper built on ruamel.yaml
├── edits.py           # Pure in-place YAML AST mutations shared by CLI commands and server write endpoints
├── commitmsg.py       # Structured commit message formatter & parser (`<kind>(<scope>): <summary>`)
├── learning_md.py     # Markdown outline parser, merger, and serializer for Learning Paths
├── identity.py        # Canonical root-commit SHA matching & GitHub owner/repo fork disambiguation
├── validate.py        # Path whitelist, Pydantic validation, directory-ID matching, and 20 KB Markdown checks
├── semantic.py        # Semantic diff checks against origin/main (ID immutability, removal guards, >30% deletion cap)
├── privacy.py         # Deterministic secret/PII/path regex scanner + isolated LLM privacy review subprocess
├── hooks.py           # Working-tree fingerprinter, Stop/AfterAgent hook router, and headless background updater
├── install.py         # Installer for ~/.claude skills/hooks, ~/.gemini rules/hooks, and macOS launchd plist
├── cli.py             # Typer CLI entrypoints (setup, show, set, add, focus, begin, push, new, attach, link, etc.)
├── demo_env.py        # Self-contained temporary demo environment generator for `make demo` and E2E tests
├── discovery/
│   ├── local.py       # Bounded local filesystem Git scanner & author/remote ownership verifier
│   ├── github.py      # Paginated GitHub REST API scanner & Link-header root-commit extractor
│   └── register.py    # Idempotent batch registration of newly discovered repositories
└── server/
    ├── app.py         # FastAPI application, background polling/discovery loop, and REST routes
    ├── sync.py        # Staging-worktree validation and atomic last-good snapshot swap
    └── writes.py      # Thread-safe server worktree transactions for inline UI edits
```

---

## 2. Data Model & Hierarchy (`models.py`)

`models.py` is the single source of truth for `schema_version: 1`. Both `schema/project.schema.json` and `schema/learning.schema.json` (and the TypeScript definitions in `web/src/types/`) are generated directly from these Pydantic models (`StrictModel` with `extra="forbid"` and `strict=True`).

### 2.1 Project Hierarchy (`Project`, `Phase`, `Component`, `Suggestion`)

- **Phased vs. Non-Phased Projects (`uses_phases: bool`):**
  - When `uses_phases: true` (default), top-level divisions live in `phases: list[Phase]`.
  - When `uses_phases: false`, top-level divisions live directly in `topics: list[Phase]` (with `phases: []`), avoiding artificial phase wrappers for simpler projects.
- **Recursive Component Nesting (`Component.components`):**
  - Each `Phase` (or top-level project topic) holds child items (`components`, `topics`, or `subtopics`).
  - Each `Component` can nest further child components in `components: list[Component]` to any depth (`Component.iter_components()`).
- **Child Projects (`parent` & `under`):**
  - A `Project` can set `parent: SlugId` and `under: SlugId` (validated together) to attach itself underneath a specific phase, topic, or component of another project.
- **Suggestions Inbox (`suggestions: list[Suggestion]`):**
  - Holds candidate items (`id`, `name`, `summary`, `under`, `added`) proposed by agents or the headless updater. Accepting a suggestion moves it into the project tree as a `not_started` item; dismissing removes it.
- **Graph Integrity (`Project.validate_internal_graph`):**
  - Enforces that every phase, topic, and nested component ID is unique across the entire project, every `depends_on` slug references an existing component in the same project (no self-dependencies), and the dependency graph is acyclic (reporting the exact cycle path `a -> b -> c -> a` via DFS).

---

## 3. Repository Identity Resolution (`identity.py` & `gitops.py`)

Workbench links local Git clones and GitHub repositories to `projects/<id>/project.yaml` without placing tracking files inside your code repositories:

1. **Canonical Root Commit (`get_root_commit`):**
   - Runs `git rev-list --max-parents=0 HEAD`.
   - If a repository has multiple root commits (e.g., after `git merge --allow-unrelated-histories`), it selects the **lexicographically smallest** SHA.
2. **Lookup & Fork Disambiguation (`find_project_for_repo`):**
   - Matches `root_commit` against `project.repos[].root_commit`.
   - If two projects share the same `root_commit` (such as forks of a template repo), it disambiguates using the normalized `owner/repo` slug parsed from `remote.origin.url` (`parse_github_slug`).

---

## 4. Automatic Repository Discovery (`discovery/`)

Discovery registers unknown repositories into `workbench-data` as `origin: discovered` stubs so they appear on the dashboard with a **New** badge.

1. **Local Scanner (`discovery/local.py`):**
   - Walks configured `scan_roots` up to `max_depth = 4`, stopping recursion as soon as a `.git` directory is encountered and skipping build/dependency directories (`node_modules`, `.venv`, `dist`, `build`, `vendor`, symlinks, hidden folders).
   - Verifies ownership (`is_repo_owned_by_user`): a repo is included only if its `origin` remote belongs to `github_owners` **or** `git log --all --format=%ae` contains an email in `user_emails`.
2. **GitHub API Scanner (`discovery/github.py`):**
   - Queries `GET /users/<owner>/repos?per_page=100`, skipping forks and archived repos.
   - Extracts each repository's initial commit SHA by requesting `GET /repos/<owner>/<repo>/commits?per_page=1` and following the `rel="last"` URL in the HTTP `Link` response header.
   - Handles `HTTP 409` (empty repo) and `HTTP 403`/`429` rate limits cleanly.
3. **Batch Registration (`discovery/register.py`):**
   - Checks out a temporary worktree of `workbench-data`, filters out any candidate whose `root_commit` or `github` slug is already tracked across any project, privacy-scans the description (blanking it if a pattern matches), and commits all newly discovered projects in a single `discover: ...` commit.

---

## 5. In-Place YAML Mutations & Worktree Transactions (`edits.py` & `cli.py`)

Workbench supports two ways to modify metadata from the CLI or web UI:

1. **Single-Step Mutations (`edits.py`, `cli.py`, `server/writes.py`):**
   - Used by `workbench set`, `workbench add`, and all dashboard UI write endpoints.
   - Functions in `edits.py` (`set_project_item_status`, `set_project_item_fields`, `set_project_item_dependencies`, `add_project_item`, `move_project_item`, `delete_project_item`, `add_suggestion`, `keep_suggestion`, `dismiss_suggestion`, `set_project_parent`) mutate the `ruamel.yaml` `CommentedMap` AST in place, preserving comments and key ordering.
2. **Multi-Item Worktree Sessions (`workbench begin` → `workbench push`):**
   - `workbench begin` creates an isolated Git worktree under `~/.workbench/work/<id>-<rand>` off `origin/main` and records `.git_wb_baseline.json`.
   - `workbench push` validates the modified worktree, sets `updated` to today's date, upgrades `origin: discovered` to `origin: agent` once structure is added, commits with a standardized message (`commitmsg.py`), rebases onto `origin/main`, and pushes with up to 3 automatic retries on concurrent push races.

---

## 6. Three-Stage Validation & Privacy Firewall (`validate.py`, `semantic.py`, `privacy.py`)

Every write to `workbench-data` must pass three checks before a commit is pushed or accepted by the server:

1. **Structural Validation (`validate.py`):**
   - Enforces the repository path whitelist (`workbench.yaml`, `projects/<id>/project.yaml`, `learning/<id>/path.yaml`, `learning/<id>/topics/<id>.md`), Pydantic model constraints, directory-to-ID equality, and the 20 KB Markdown cap.
2. **Semantic Diff Guard (`semantic.py`):**
   - Compares the updated file against `origin/main` across all nested levels:
     - Blocks changing the top-level `id`.
     - Blocks renaming an existing item's `id` while keeping the same `name`.
     - Blocks removing any phase, topic, subtopic, or nested component ID unless `--allow-removals` is explicitly passed (or an explicit UI delete action is invoked).
     - Blocks deleting more than `30%` of total string content without `--allow-removals`.
3. **Privacy & Secret Scanner (`privacy.py`):**
   - Scans every string value using deterministic regexes and `detect-secrets` for AWS keys (`AKIA...`), GitHub tokens (`ghp_...`, `github_pat_...`), OpenAI/Anthropic keys (`sk-...`), Slack tokens, private keys, JWTs, credentialed URLs, password assignments, local filesystem paths (`/Users/...`, `/home/...`, `C:\...`, `~/...`), private IPv4 addresses, `.local` hostnames, and personal emails.
   - **Never echoes secrets:** Error messages report only the YAML field path and rule name (`Privacy violation in 'phases[0].summary': detected local_path_users`), never the matched secret text.

---

## 7. Agent Lifecycle Hooks & Headless Mode (`hooks.py`)

`workbench hook-check` is invoked at the end of each coding agent turn (`Stop` in Claude Code, `AfterAgent` in Gemini CLI):

- **Working-Tree Fingerprinting (`compute_fingerprint`):**
  - Computes a SHA-256 digest over `git status --porcelain=v1 -z --untracked-files=all` (with `--no-optional-locks`), `git diff HEAD`, and untracked file sizes/mtimes.
- **Two-Skill Routing (`evaluate_hook_input`):**
  - If `HEAD != last_seen_commit` (a new Git commit landed in the code repo), the hook prompts the agent to run **`workbench-review`** (unless `update_mode = "headless"`, where reviews run only on explicit `/workbench-review`).
  - If `HEAD == last_seen_commit` but `fingerprint != last_seen_fingerprint` (files were edited during the turn), the hook either prompts the agent to run **`workbench-update`** (`update_mode = "inline"`) or spawns a non-blocking background subprocess (`spawn_headless_updater` when `update_mode = "headless"`) that inspects the session transcript (`run_auto_update`) and applies the update automatically.
- **Fail-Open Safety:** Any exception or timeout inside `hooks.py` returns `None` so an agent session is never trapped by a hook error.
