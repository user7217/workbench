# Workbench Backend Architecture & Internals (`src/workbench`)

This document is the technical reference for the `src/workbench` Python package. It explains how every backend module fits together, how repository identity and discovery work, how CLI and web edits mutate YAML safely, and how the validation, privacy, and agent hook pipelines operate.

---

## 1. Module Map

```text
src/workbench/
├── __init__.py
├── models.py          # Pydantic v2 models (Project, Phase, Component, Suggestion, LearningPath) & JSON Schema export
├── config.py          # Laptop configuration (~/.workbench/config.toml) and the default agent commands
├── gitops.py          # Git plumbing (bare repos, clones, temporary worktrees, rebases, root commit extraction)
├── yamlio.py          # Comment- and key-order-preserving YAML loader/dumper built on ruamel.yaml
├── edits.py           # Pure in-place YAML AST mutations shared by CLI commands and server write endpoints
├── commitmsg.py       # Structured commit message formatter & parser (`<kind>(<scope>): <summary>`)
├── learning_md.py     # Markdown outline parser, merger, and serializer for Learning Paths
├── identity.py        # Canonical root-commit SHA matching & GitHub owner/repo fork disambiguation
├── validate.py        # Path whitelist, Pydantic validation, directory-ID matching, and 20 KB Markdown checks
├── semantic.py        # Semantic diff checks against origin/main (ID immutability, removal guards, >30% deletion cap)
├── privacy.py         # Deterministic secret/PII/path regex scanner + isolated LLM privacy review subprocess
├── cli/               # Typer CLI: one module per command area, merged into `app` by __init__.py
│   ├── __init__.py    # Creates `app` (the `workbench` entry point); module order sets the --help order
│   ├── _common.py     # Data repo clone, worktree helpers, the shared push pipeline (`_push_worktree`) and quick edits
│   ├── projects.py    # show, begin, push, archive-empty, link, validate
│   ├── hierarchy.py   # new, attach, detach
│   ├── items.py       # set, add, focus
│   ├── discover.py    # discover
│   ├── history.py     # history, undo
│   ├── agents.py      # hook-check, auto-update (the headless updater job)
│   ├── server.py      # serve
│   ├── setup.py       # install, setup
│   ├── learning.py    # `learning` sub-app: import, export
│   └── demo.py        # `demo` sub-app: init
├── agents/            # Coding-agent integration
│   ├── hooks.py       # Working-tree fingerprint, Stop/AfterAgent hook decision, per-repo state (~/.workbench/state.json)
│   ├── updater.py     # Headless per-message updater: transcript reading, prompt, applying the agent's JSON plan
│   ├── install.py     # Installer for ~/.claude skills/hooks, ~/.gemini rules/hooks, and the macOS launchd plist
│   └── assets/        # Shipped in the wheel: claude/ skills, gemini/ rules and commands, chat/ import prompts
├── __main__.py        # `python -m workbench` (how the hook starts the background updater)
├── demo/              # Self-contained temporary demo environment generator for `make demo` and E2E tests
│   ├── environment.py # init_demo_environment: fake code repos, seeded bare workbench-data remote, fake HOME
│   ├── fake_github.py # FakeGitHubServer: fixture-backed GitHub REST stub for discovery
│   └── data/          # Demo content shipped in the wheel: repos.yaml, github_fixtures.yaml, seed/ (the data repo's files)
├── discovery/
│   ├── local.py       # Bounded local filesystem Git scanner & author/remote ownership verifier
│   ├── github.py      # Paginated GitHub REST API scanner & Link-header root-commit extractor
│   └── register.py    # Idempotent batch registration of newly discovered repositories
└── server/
    ├── app.py         # FastAPI application, background polling/discovery loop, and REST routes
    ├── sync.py        # Staging-worktree validation and atomic last-good snapshot swap
    └── writes/        # Thread-safe server worktree transactions for inline UI edits
        ├── __init__.py     # Public write functions and WriteError, imported by app.py
        ├── _transaction.py # `_run_transaction`: lock, worktree, commit, push retries; `_commit_edit`: one project/learning file with validation and semantic check
        ├── fields.py       # Status and text-field edits
        ├── structure.py    # Add, move, delete, parent link
        ├── suggestions.py  # Keep or dismiss a suggestion
        └── today.py        # Replace the Today's Work list (today.yaml)
```

The rest of the repository:

```text
web/                   # React + TypeScript UI: src/core (fixed state layer), src/ui (replaceable look), docs/images
desktop/               # macOS menu-bar app (Tauri): runs the frozen `workbench serve` at login (docs/desktop.md)
schema/                # JSON Schemas generated from models.py (the contract between Python and the UI)
packaging/
├── hatch_build.py     # Wheel build hook: ships the built web/dist inside the package as workbench/web_dist
├── pyinstaller/       # Spec that freezes the CLI + server + UI into desktop/build/workbench/ for the app
└── docker/            # Dockerfile, GitHub's SSH host keys, and update.sh + systemd timer that move a server to each new release
docker-compose.yml     # Server deployment (stays at the root: it reads .env and secrets/ next to it)
scripts/               # run_demo.py (the `make demo` server), screenshots.py (README screenshots), version.py (versions + changelog)
docs/                  # setup.md, desktop.md, releasing.md, reference.md, architecture.md (this file)
.github/workflows/     # ci.yml: lint, generated files, UI build, wheel smoke test on three OSes, Docker build;
                       # release.yml: on a v* tag, the .dmg, wheel + sdist, GHCR image and the GitHub Release
CHANGELOG.md           # Release notes, one section per version (docs/releasing.md)
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

Workbench links local Git clones and GitHub repositories to `projects/<id>/project.yaml` without placing tracking files inside the code repositories:

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
2. **GitHub API Scanner (`discovery/github.py`, run by the server once a day):**
   - With `WORKBENCH_GITHUB_READ_TOKEN` set, lists `GET /user/repos?affiliation=owner,organization_member` (which includes private repos) and keeps repos owned by one of `WORKBENCH_GITHUB_OWNERS`; without a token, lists `GET /users/<owner>/repos` (public repos only). Follows `Link: rel="next"` pages and skips forks and archived repos.
   - Extracts each repository's initial commit SHA by requesting `GET /repos/<owner>/<repo>/commits?per_page=1` and following the `rel="last"` URL in the HTTP `Link` response header.
   - Handles `HTTP 409` (empty repo) and `HTTP 403`/`429` rate limits cleanly.
3. **Batch Registration (`discovery/register.py`):**
   - Checks out a temporary worktree of `workbench-data`, filters out any candidate whose `root_commit` or `github` slug is already tracked across any project, privacy-scans the description (blanking it if a pattern matches), and commits all newly discovered projects in a single `discover: ...` commit.

---

## 5. In-Place YAML Mutations & Worktree Transactions (`edits.py` & `cli/`)

Workbench supports two ways to modify metadata from the CLI or web UI:

1. **Single-Step Mutations (`edits.py`, `cli/items.py`, `server/writes/`):**
   - Used by `workbench set`, `workbench add`, and all dashboard UI write endpoints.
   - Functions in `edits.py` (`set_project_item_status`, `set_project_item_fields`, `set_project_item_dependencies`, `add_project_item`, `add_suggestion`, `keep_suggestion`, `dismiss_suggestion`, and the learning-path equivalents) mutate the `ruamel.yaml` `CommentedMap` AST in place, preserving comments and key ordering. Moving and deleting items and setting a project's parent need checks across files, so they live in `server/writes/structure.py` (`move_project_item`, `delete_project_item`, `set_project_parent`).
2. **Multi-Item Worktree Sessions (`workbench begin` → `workbench push`):**
   - `workbench begin` creates an isolated Git worktree under `~/.workbench/work/<id>-<rand>` off `origin/main` and records `.git_wb_baseline.json`.
   - `workbench push` validates the modified worktree, sets `updated` to today's date, upgrades `origin: discovered` to `origin: agent` once structure is added, commits with a standardized message (`commitmsg.py`), rebases onto `origin/main`, and pushes with up to 3 automatic retries on concurrent push races.

---

## 6. Three-Stage Validation & Privacy Firewall (`validate.py`, `semantic.py`, `privacy.py`)

Every write to `workbench-data` must pass three checks before a commit is pushed or accepted by the server:

1. **Structural Validation (`validate.py`):**
   - Enforces the repository path whitelist (`workbench.yaml`, `today.yaml`, `projects/<id>/project.yaml`, `learning/<id>/path.yaml`, `learning/<id>/topics/<id>.md`), Pydantic model constraints, directory-to-ID equality, and the 20 KB Markdown cap.
2. **Semantic Diff Guard (`semantic.py`):**
   - Compares the updated file against `origin/main` across all nested levels:
     - Blocks changing the top-level `id`.
     - Blocks renaming an existing item's `id` while keeping the same `name`.
     - Blocks removing any phase, topic, subtopic, or nested component ID unless `--allow-removals` is explicitly passed (or an explicit UI delete action is invoked).
     - Blocks deleting more than `30%` of total string content without `--allow-removals`.
   - Applies to project and learning files only. `today.yaml` (the Today's Work list) is validated structurally and scanned for privacy, but entries leaving it, or the file being deleted, is never blocked.
3. **Privacy & Secret Scanner (`privacy.py`):**
   - Scans every string value using deterministic regexes and `detect-secrets` for AWS keys (`AKIA...`), GitHub tokens (`ghp_...`, `github_pat_...`), OpenAI/Anthropic keys (`sk-...`), Slack tokens, private keys, JWTs, credentialed URLs, password assignments, local filesystem paths (`/Users/...`, `/home/...`, `C:\...`, `~/...`), private IPv4 addresses, `.local` hostnames, and personal emails.
   - **Never echoes secrets:** Error messages report only the YAML field path and rule name (`Privacy violation in 'phases[0].summary': detected local_path_users`), never the matched secret text.

---

## 7. Agent Lifecycle Hooks & Headless Mode (`agents/hooks.py`, `agents/updater.py`)

`workbench hook-check` runs at the end of every coding-agent message (`Stop` in Claude Code, `AfterAgent` in Gemini CLI). It compares the repo with what `~/.workbench/state.json` last saw for it: `HEAD`, and a fingerprint (`compute_fingerprint`: SHA-256 over `git status --porcelain=v1 -z --untracked-files=all` with `--no-optional-locks`, `git diff HEAD`, and untracked file sizes/mtimes).

It lets the agent finish without doing anything when `stop_hook_active` is true, the directory is not a git repo with commits, it runs inside the headless updater's own agent (`WORKBENCH_UPDATER` is set), `update_mode = "off"`, or anything fails (fail open: an error never traps a session). Otherwise `update_mode` in `config.toml` decides:

- **`session` (default):** a new `HEAD` blocks the agent with "run the **workbench-review** skill"; a changed fingerprint blocks it with "run the **workbench-update** skill". A session is never blocked twice for the same state. `workbench set`/`add`/`push` and `workbench hook-check --mark-seen` record the state as seen.
- **`headless`:** when `HEAD` or the fingerprint changed, the hook records both as seen, writes a job file to `~/.workbench/jobs/`, starts `python -m workbench auto-update --job <file>` detached (`updater.launch_update_job`), and returns at once. The session is never blocked, a new commit is recorded like any other change, and full reviews run only when `/workbench-review` is called. If `updater_command`'s program is not installed, the hook behaves as in `session` mode.

The background job (`cli.agents._run_auto_update`, one at a time under `~/.workbench/updater.lock`):

1. Reads the message the hook fired for (`updater.exchange_from_hook`): Gemini sends `prompt`/`prompt_response`; for Claude it reads the last user turn and its reply from the transcript, using the transcript size at hook time so a queued job never picks up a later message. Long texts are cut, keeping both ends.
2. Finds the project (the repo's focus, else the project the repo is registered under) and stops with a log line if there is none.
3. Runs `updater_command` with a prompt holding the rules, the full `project.yaml`, `git diff --stat`, new files, new commits and the exchange. The agent has no tools and answers with JSON: `set` and `add` operations plus suggestions.
4. Applies them itself (`updater.apply_plan`): only `set` (status, notes, summary) and `add` (a new item under an existing one) on existing ids, no renames or removals, texts clamped to the model limits, duplicates by name skipped. An operation that does not fit is skipped and logged. Suggestions go to the project's `suggestions` inbox.
5. Pushes one `update(<project>[/<item>]): <summary>` commit through the quick path (validation, deterministic privacy scan, semantic check) and appends a line to `~/.workbench/logs/updater.log`.

---

## 8. Dashboard Server (`server/`)

- **Sync (`sync.py`):** on startup the server loads its cached `snapshot.json`, then every `WORKBENCH_POLL_SECONDS` fetches `origin/main` into its own clone, validates the fetched commit in a staging worktree and, only if it is valid, swaps in a new snapshot (projects, learning paths, topic Markdown, the Today's Work list from `today.yaml`, and each entry's last change time from `git log`). An invalid commit keeps the last good snapshot and is reported in `/api/status`. A sync never raises, and one lock serializes all git work on the clone.
- **Writes (`writes/`):** each dashboard edit opens a worktree, applies the change with `edits.py`, runs the same validation and semantic checks as the CLI, commits an `edit(...)` message, rebases and pushes with retries, then resyncs and returns the new snapshot. `POST /api/today` replaces the list in `today.yaml` through the same transaction without the semantic check; it stores only the chosen roots, which the UI expands.
- **Discovery:** with `WORKBENCH_GITHUB_OWNERS` set, the server runs GitHub discovery once a day.
- **UI (`app.py`):** the built UI is served from `WORKBENCH_WEB_DIST` if set, else the copy inside the installed package (`workbench/web_dist`, added to the wheel by `packaging/hatch_build.py` when `web/dist` is built), else the checkout's `web/dist`. The server runs in the Docker image, with `workbench serve`, or inside the desktop app.
- **Host check (`app.py`):** with `WORKBENCH_ALLOWED_HOSTS` set, requests addressed to any other host name are rejected (`400`). `workbench serve` sets it to `127.0.0.1,localhost` when listening on loopback, which blocks DNS rebinding; the Docker server leaves it unset.
