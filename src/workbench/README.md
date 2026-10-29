# Workbench Backend Package (`src/workbench`)

This directory contains the Python backend, CLI, discovery engine, validation/privacy pipeline, agent hook handlers, and FastAPI server for **Workbench**.

---

## 1. Package Layout & Module Responsibilities

```text
src/workbench/
├── __init__.py
├── models.py          # Single source of truth Pydantic v2 models & JSON Schema generator
├── config.py          # Laptop configuration (~/.workbench/config.yaml) loader/writer
├── gitops.py          # Git plumbing wrappers (bare repos, clones, worktrees, rebases, root commits)
├── yamlio.py          # Comment- and key-order-preserving YAML I/O via ruamel.yaml
├── identity.py        # Root-commit SHA extraction & GitHub owner/repo fork disambiguation
├── validate.py        # Structural, path-whitelist, and file-size validation
├── semantic.py        # Semantic diff checks against origin/main (ID renames, removals, mass deletion)
├── privacy.py         # Deterministic secret/PII/path regex scanner + isolated LLM privacy reviewer
├── hooks.py           # Claude Code Stop hook & Gemini CLI AfterAgent hook logic
├── updater.py         # Headless per-message updater: transcript reading, prompt, applying the agent's plan
├── install.py         # Installer for ~/.claude, ~/.gemini, and macOS launchd discovery job
├── cli.py             # Typer CLI (setup, show, begin, push, validate, new, learning, discover, history, undo)
├── demo_env.py        # Self-contained demo environment generator for `make demo` and E2E tests
├── discovery/
│   ├── __init__.py
│   ├── local.py       # Local filesystem Git repository scanner & ownership verifier
│   ├── github.py      # GitHub REST API repository & root-commit pagination scanner
│   └── register.py    # Idempotent registration of newly discovered repos into workbench-data
└── server/
    ├── __init__.py
    ├── app.py         # FastAPI application, background polling loop, and static SPA server
    ├── sync.py        # Staging-worktree validation & atomic last-good snapshot swap
    └── writes.py      # Server-side worktree commit & push for learning item status updates
```

---

## 2. Data Repository Structure (`workbench-data`)

Workbench uses a dedicated Git repository (`workbench-data`) as its database. Only the following paths are permitted inside `workbench-data` (enforced by `is_allowed_repo_relative_path()` in `validate.py`):

- `workbench.yaml` — Root marker file containing `schema_version: 1`.
- `projects/<project-id>/project.yaml` — Project metadata, linked repositories, and hierarchy (`phases` when `uses_phases: true`, or direct `topics` when `uses_phases: false`).
- `learning/<path-id>/path.yaml` — Learning path metadata, topics, and subtopics.
- `learning/<path-id>/topics/<item-id>.md` — Optional Markdown study notes (capped at 20 KB per file) matching a topic or subtopic `id` in `path.yaml`.

### Phased vs. Non-Phased Projects (`models.py`)

Every `Project` in `models.py` explicitly supports two structural styles via `uses_phases: bool`:

1. **Phased Projects (`uses_phases: true`)**:
   - Work is grouped into top-level `phases:` (e.g., `Hardware & Power`, `Embedded Firmware`), each containing nested `components:` (or `topics:` / `subtopics:`).
2. **Non-Phased Projects (`uses_phases: false`)**:
   - Work is organized directly under `topics:` (with `phases: []`), each containing optional nested `subtopics:` / `components:` and individual `items:`.
3. **Internal Dependency Graph Validation**:
   - `Project.validate_internal_graph()` verifies that all phase/topic and component/subtopic IDs are unique across the project, that every `depends_on` reference targets a valid component ID within the same project, and that the dependency graph is a DAG (reporting the exact cycle path `a -> b -> c -> a` if a cycle exists).

---

## 3. Repository Identity Resolution (`identity.py`)

Workbench links local Git directories and GitHub remotes to a `Project` in `workbench-data` without relying on fragile local filesystem paths:

1. **Root Commit SHA (`get_root_commit` in `gitops.py`)**:
   - Runs `git rev-list --max-parents=0 HEAD` in the target repository.
   - If a repository has multiple root commits (for example, after `git merge --allow-unrelated-histories`), Workbench deterministically selects the **lexicographically smallest** root commit SHA.
   - Repositories with zero commits are ignored until their first commit is created.
2. **Matching & Fork Disambiguation (`find_project_for_repo` in `identity.py`)**:
   - Searches all `projects/*/project.yaml` files for a `repos[]` entry whose `root_commit` matches.
   - If only one project claims that `root_commit`, it is returned immediately—even if the folder was renamed or moved on disk.
   - If multiple projects claim the same `root_commit` (for example, two forks of the same base repository), `find_project_for_repo()` disambiguates using the repository's `owner/repo` GitHub remote slug (`parse_github_slug`). If still ambiguous, it raises `AmbiguousRepoIdentityError`.

---

## 4. How Repository Discovery Works (`discovery/`)

Discovery automatically finds repositories you own and registers any unknown ones into `workbench-data` as minimal `origin: discovered` entries (which appear with a **New** badge in the UI until their phases or topics are populated).

### 4.1 Local Filesystem Discovery (`discovery/local.py`)

Triggered via `workbench discover --local` or daily at 12:00 PM by the macOS `launchd` job (`com.workbench.discover.plist`):

1. **Scan Root Expansion (`expand_scan_root`)**:
   - Reads `scan_roots` from `~/.workbench/config.yaml` (e.g., `~/Documents`, `~/code`, `~/projects`).
2. **Bounded Directory Walk (`find_git_repos_under`)**:
   - Walks each scan root up to `max_depth = 4`.
   - Stops descending into a directory as soon as a `.git` directory/file is found.
   - Skips hidden directories (`.git`, `.cache`, etc.), symlinks, and heavy build/dependency folders (`node_modules`, `.venv`, `vendor`, `__pycache__`, `dist`, `build`).
3. **Ownership Verification (`is_repo_owned_by_user`)**:
   - A local Git repository is considered **owned by you** (and not a third-party upstream clone) if **either**:
     - Its `origin` remote URL belongs to one of your configured `github_owners` (case-insensitive), **or**
     - At least one commit in `git log --all --format=%ae` matches one of your configured `user_emails` (case-insensitive).
4. **Candidate Deduplication (`scan_local_candidates`)**:
   - Extracts `(root_commit, github_slug, repo_name)` for each owned repo with at least one commit and deduplicates candidates by `root_commit`.

### 4.2 GitHub API Discovery (`discovery/github.py`)

Triggered by the server background loop once every 24 hours when `WORKBENCH_GITHUB_OWNERS` is configured:

1. **Paginated Repo Listing (`fetch_github_candidates`)**:
   - With `WORKBENCH_GITHUB_READ_TOKEN` set, queries `GET /user/repos?affiliation=owner,organization_member&per_page=100` (the authenticated listing, which includes private repos) and keeps only repos owned by one of `WORKBENCH_GITHUB_OWNERS`. Without a token it queries `GET /users/<owner>/repos?per_page=100`, which returns public repos only. Either way it follows HTTP `Link: <...>; rel="next"` headers across all pages.
   - Automatically skips repositories where `"fork": true` or `"archived": true`.
2. **Root Commit Lookup via HTTP `Link` Header**:
   - For each non-fork, non-archived repo, requests `GET /repos/<owner>/<repo>/commits?per_page=1`.
   - If a `rel="last"` link is present in the `Link` header, fetches that last page and reads the SHA of the oldest commit (`commits_data[-1]["sha"]`).
   - Empty repositories (`HTTP 409`) are skipped cleanly.
   - Rate-limit responses (`HTTP 403` with `X-RateLimit-Remaining: 0` or `HTTP 429`) halt the scan gracefully and record `Retry-After` without raising an unhandled exception.

### 4.3 Idempotent Registration (`discovery/register.py`)

Both local and GitHub discovery pass their `DiscoveredRepoCandidate` lists to `register_discovered_repos()`:

1. Fetches `origin/main` and checks out an isolated temporary worktree (`~/.workbench/work/discover-<rand>`).
2. Loads all existing `projects/*/project.yaml` files and builds sets of known `root_commit` SHAs and `github` (`owner/repo`) slugs.
3. Skips any candidate whose `root_commit` or `github` slug is already registered.
4. For each genuinely new repository:
   - Generates a unique slug ID (`slugify_name(cand.name)`, appending `-2`, `-3`, etc. if another project has the same name).
   - Privacy-scans the repository description (`sanitize_discovered_description`); if the description triggers any privacy pattern, it is blanked to `""` rather than failing the batch.
   - Writes `projects/<id>/project.yaml` with `origin: discovered`, `uses_phases: false`, `phases: []`, and `topics: []`.
5. Commits all newly discovered projects in a **single batch commit** (`Register N discovered repo(s) via ...`), rebases onto `origin/main`, and pushes with up to 3 retry attempts on concurrent push races.

---

## 5. CLI Worktree Transactions (`cli.py`)

Agents and humans never edit `~/.workbench/data` directly on `main`. Instead, edits use isolated Git worktrees:

1. **`workbench show`**:
   - Resolves the current directory's Git identity (or `--id <project-id>`) and prints its current `project.yaml`, or prints `"unregistered"` with instructions.
2. **`workbench begin` (`--id <project-id>` or `--learning <path-id>`)**:
   - Fetches `origin/main` and creates a temporary worktree at `~/.workbench/work/<id>-<rand>`.
   - If the project or learning path does not exist yet, generates a pre-filled skeleton `project.yaml` or `path.yaml` using `ruamel.yaml` (`yamlio.py`) so comments and key ordering are preserved.
   - Records `.git_wb_baseline.json` in the worktree root so `workbench push` can detect if an agent manually tampered with CLI-managed fields (`updated` or `origin`), and prints the absolute path to the YAML file to edit.
3. **`workbench push <worktree> -m "<summary>"`**:
   - Removes `.git_wb_baseline.json` and checks `git diff --name-only origin/main`. If nothing changed, removes the worktree and marks the current repo seen.
   - Overwrites `updated` with today's ISO date and manages `origin` automatically (upgrading `origin: discovered` to `origin: agent` as soon as `phases` or `topics` are populated).
   - Runs the 3-stage validation pipeline (Section 6 below).
   - Commits the change in the worktree, fetches `origin/main`, runs `git rebase origin/main`, re-validates the post-rebase tree, pushes to `origin/main` (retrying up to 3 times on non-fast-forward push races), removes the worktree, and updates `~/.workbench/state.json`.
   - If a rebase conflict occurs against another concurrent edit to the same file, aborts the rebase and **keeps the worktree path on disk** so no work is lost.

---

## 6. Three-Stage Validation & Privacy Pipeline (`validate.py`, `semantic.py`, `privacy.py`)

Every `workbench push` must pass three gatekeepers before a commit is created:

1. **Structural & Path Validation (`validate.py` + `models.py`)**:
   - Verifies that only allowed file paths were modified (`workbench.yaml`, `projects/<id>/project.yaml`, `learning/<id>/path.yaml`, `learning/<id>/topics/<topic-id>.md`).
   - Validates strict Pydantic models (`extra="forbid"`, `strict=True`), `https://` URLs, slug formats, string length caps, directory-name-to-ID matching, and internal dependency DAGs.
2. **Semantic Diff Protection (`semantic.py`)**:
   - Compares the new YAML against the previous version at `origin/main`:
     - **Top-level ID immutability**: Rejects any change to the top-level `id`.
     - **Rename detection**: If an existing phase, topic, component, or subtopic ID disappears while a new ID with the **exact same `name`** appears, blocks with an error (`IDs must not change: ... was renamed`).
     - **Removal guard**: Blocks removing any existing phase, topic, component, or subtopic ID unless `--allow-removals` is explicitly passed.
     - **Mass deletion guard**: Blocks deleting more than `30%` of total free-text characters across the file and its topic Markdown files unless `--allow-removals` is passed.
     - **Backward status warning**: Emits a non-blocking warning if an item's status moves backwards (e.g., `done` -> `not_started`).
3. **Deterministic & LLM Privacy Scanning (`privacy.py`)**:
   - **Deterministic Regex & `detect-secrets` Scan (`scan_data_structure`)**: Scans all string values for private keys, AWS keys (`AKIA...`), GitHub tokens (`ghp_...`, `github_pat_...`), API keys (`sk-...`), Slack tokens (`xox...`), JWTs, password assignments, credentialed connection URLs, local filesystem paths (`/Users/...`, `/home/...`, `C:\...`, `~/...`), private IPv4 addresses (`10.x`, `192.168.x`, `172.16-31.x`, `127.x`), `.local` hostnames, and personal email addresses (allowing only `@example.com` and `@example.invalid`).
   - **Zero Secret Echoing**: `PrivacyIssue.format_error()` reports **only** the offending YAML field path and pattern type (e.g., `Privacy violation in 'phases[0].summary': detected aws_access_key`) and **never** echoes the matched secret value to stdout/stderr or logs.
   - **Isolated LLM Privacy Reviewer (`run_llm_privacy_reviewer`)**: When `privacy_review` is `"auto"` or `"required"`, extracts a diff of changed free-text fields (`description`, `summary`, `notes`, and `topics/*.md`) and pipes it to the configured reviewer command inside an empty temporary directory (`cwd=tempfile.TemporaryDirectory`).

---

## 7. Agent Lifecycle Hooks (`hooks.py`, `updater.py` & `install.py`)

`workbench install` makes coding agents run `workbench hook-check` (`hooks.py`) at the end of every message (`Stop` in Claude Code, `AfterAgent` in Gemini CLI). The hook reads the JSON payload from stdin (`session_id`, `cwd`, `transcript_path`, `stop_hook_active`) and compares the repo with what `~/.workbench/state.json` last saw: `HEAD` and a fingerprint of the working tree (status, diff against `HEAD`, untracked file stats).

It always allows the agent to finish (prints nothing) when `stop_hook_active` is true, `cwd` is not a git repo with commits, the hook runs inside the headless updater's own agent (`WORKBENCH_UPDATER` is set), `update_mode = "off"`, or anything fails (fail open). Otherwise `update_mode` in `~/.workbench/config.toml` decides:

**`session` (default).** A new `HEAD` blocks with "run the workbench-review skill"; a changed fingerprint blocks with "run the workbench-update skill". A session is never blocked twice for the same state. The agent finishes with `workbench set`/`add`/`push` or `workbench hook-check --mark-seen`, which record the state as seen.

**`headless`.** When `HEAD` or the fingerprint changed, the hook records them as seen, writes a job to `~/.workbench/jobs/`, starts `workbench auto-update --job <file>` detached, and returns at once; the session is never blocked and full reviews run only on `/workbench-review`. The job (`updater.py`, one at a time under `~/.workbench/updater.lock`):

1. Reads the message the hook fired for: Gemini sends `prompt`/`prompt_response`; for Claude it reads the last user turn and its reply from the transcript (the turn that started before the hook fired, so a queued job never records a later message), plus the previous reply for context. Long texts are cut, keeping both ends.
2. Finds the project (the repo's focus, else the project the repo is registered under) and skips with a log line if there is none.
3. Sends `updater_command` (default `claude -p --model haiku --tools "" ...`, prompt on stdin, run in an empty temp dir) the rules, the full `project.yaml`, `git diff --stat`, new files and new commits, and the exchange. The agent has no tools and answers with JSON: `set` ops (status/notes/summary of an existing item), `add` ops (a new item under an existing one) and suggestions.
4. Applies the ops itself: only `set` and `add`, only on existing ids, no renames or removals, texts clamped to the model limits, duplicates by name skipped; an op that does not fit is skipped and logged, not fatal. Suggestions go to the project's `suggestions` inbox, where the dashboard offers Keep / Dismiss.
5. Pushes one `update(<project>[/<item>]): <summary>` commit through the quick path (validation, deterministic privacy scan, semantic check) and appends a line to `~/.workbench/logs/updater.log`.

If the `updater_command` executable is not installed, headless mode falls back to `session` behaviour. `workbench auto-update --transcript <file.jsonl> --dry-run` (inside the project repo) prints the prompt and the plan without changing anything.

---

## 8. FastAPI Server & Atomic Sync (`server/`)

The dashboard server (`server/app.py`, `server/sync.py`, `server/writes.py`) is stateless with respect to your laptop and syncs directly against `workbench-data`:

- **Startup & Background Polling (`app.py`, `sync.py`)**:
  - On startup, `ServerState.load_snapshot_from_disk()` restores the cached `snapshot.json` from `WORKBENCH_STATE_DIR` (if present) and immediately runs `sync_once()` in a worker thread.
  - Every `WORKBENCH_POLL_SECONDS` (default `600`s) — or on `POST /api/sync` ("Check for updates") — `sync_once()` fetches `origin/main`, checks out the fetched commit into a temporary staging worktree, and runs `validate_data_repo()`.
  - **Last-Good Snapshot Preservation**: If validation passes, `sync_once()` atomically writes `snapshot.json` and swaps the in-memory snapshot (`last_error = None`). If validation fails, it keeps serving the previous valid snapshot and populates `last_error` (`commit`, `file`, `message`) so the UI can display the amber warning banner.
- **Direct Learning Status Writes (`writes.py`)**:
  - Clicking `not started` / `in progress` / `done` on a learning topic or subtopic in the UI sends `POST /api/learning/{path_id}/items/{item_id}/status` with header `X-Workbench: 1`.
  - `update_learning_item_status()` acquires `state._repo_lock`, creates a temporary worktree on the server, updates the item's `status` and `updated` date via `ruamel.yaml`, validates the file, commits `Mark <item_id> <status> via dashboard`, pushes to `origin/main` (with 3 rebase retries), and refreshes the live snapshot.
