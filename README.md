# Workbench

Workbench is a personal dashboard showing everything you are **building** (software projects) and **learning** (learning paths) so nothing gets forgotten — what exists, what is half-done, and what is left to do.

- **Completeness over task management:** Workbench is an inventory and architectural map of your projects and learning paths, not a task tracker. There are no deadlines, countdowns, streaks, percentages, priority scores, or "overdue" indicators.
- **Zero-friction updates:** AI coding agents (Claude Code, Gemini CLI) keep project structure and status current through two skills driven by a Stop/AfterAgent hook (`workbench hook-check`): **workbench-update** records what a single message changed for one item (a status, a note, a newly found problem) whenever files changed since the last update, and **workbench-review** reconciles the whole entry after a new commit lands, at session end, or on request. When nothing changed, the hook asks for nothing. With `update_mode = "headless"` the per-message update runs in a background agent instead (Haiku by default), so the coding session spends no tokens on it, suggestions land in the project's **Inbox** on the dashboard, and full reviews run only when you call `/workbench-review`. Local and GitHub discovery jobs register new repositories automatically.
- **Git-backed audit trail:** All metadata lives in a separate private Git repository (`workbench-data`). Every change is validated structurally, scanned for secrets/PII, and committed to `main`. Any bad change can be reverted with `workbench undo <commit>`.

## Features

- **Projects** with phases, components, and **components nested to any depth**; statuses `not_started`, `in_progress`, `done`, `parked`; `depends_on` links drawn on an interactive map.
- **Child projects:** a large piece of work can be its own project that sits under a phase or component of a parent (`parent:` / `under:`). The parent shows it inline (expand it in place or open it); a child only appears in the main project lists while its status is `active`.
- **Learning paths** with topics and subtopics, Markdown notes per item, and plain `related_projects` references.
- **Web editing:** change any item's status, edit names, summaries, notes and descriptions inline, add / reorder / move / delete phases and components, attach a project to a parent, and archive/unarchive projects and learning paths, directly from the UI. Every edit is validated and committed like a CLI push.
- **Build your own look:** the UI is a fixed core plus a swappable look; see [web/README.md](web/README.md) ("Build your own look").
- **Chat-friendly import:** `agent/chat/PROMPT.md` and `agent/chat/LEARNING_PROMPT.md` turn a chat's history into the exact project YAML or learning outline format; `workbench learning import` merges outlines without ever losing progress.

## Quick Start (Isolated Demo)

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node.js 20+, and git.

```bash
uv sync                       # Python dependencies
(cd web && npm install)       # UI dependencies
```

Run the full build and test suite (offline, using isolated temporary directories and fake credentials):

```bash
make test
```

Start the local demo server seeded with artificial `demo-*` projects and learning paths:

```bash
make demo
```

Then open **http://127.0.0.1:8420** (set `WORKBENCH_PORT` to use another port). The demo data lives in a temporary folder that is rebuilt on every start.

- Press `/` on the Home screen to filter items by name.
- Open a project (e.g. **6-DOF Robot Arm**) and use the visual map; click a node to inspect its notes, links, dependencies and reverse dependencies.
- Click any status icon to change it, or use the status control in a project header to archive it.
- Open a learning path, select a topic to read its Markdown notes, and mark items **Not started / In progress / Done**.

## CLI Cheat Sheet

| Command | What it does |
|---|---|
| `workbench setup` / `workbench install --home ~ --claude --gemini` | Configure the data repo; install the agent skills and hooks |
| `workbench show [--id <project>]` | Print a project's entry (from inside its repo, or by id) |
| `workbench show [--id <project>] --brief` / `--item <item-id>` | One line per item (id, status, name) / just one item and its parts |
| `workbench focus` | Print the current focus (`project` or `project/item`) for this repo |
| `workbench set <project> <item-id> [--status S] [--name T] [--summary T] [--notes T]` | Quick update of one item, validated and pushed in one step |
| `workbench add <project> [--under <item-id>] --name T [--id I] [--status S] [--summary T] [--notes T]` | Add one item (a new phase or topic when `--under` is left out) |
| `workbench begin [--id <project>]` → edit → `workbench push <worktree> -m "…"` | Edit an entry in an isolated worktree, then validate and push |
| `workbench new "<Name>" [--parent <id> --under <item>] [--status planned]` | Create a project (optionally as a child project) |
| `workbench attach --id <child> --parent <id> --under <item>` / `detach --id <child>` | Move a project under a parent, or detach it |
| `workbench link --id <project>` | Attach the current repo to a project (removes an empty discovered stub) |
| `workbench archive-empty [--dry-run] [--keep <id>]` | Archive empty, auto-discovered projects |
| `workbench learning import <file.md\|.yaml>` / `learning export <id>` | Import (merge) or print a learning-path outline |
| `workbench history [--kind K[,K]] [--project P] [-n N]` / `workbench undo <commit>` | Show recent changes, filtered by commit kind or project / revert one |
| `workbench serve [--host H] [--port P]` | Run the dashboard server (API and web UI) without Docker |
| `workbench hook-check --mark-seen` | Tell the agent hook nothing needs recording for the current changes |
| `workbench auto-update --transcript <file.jsonl> --dry-run` | Show what the headless updater would record for a session's last message (run inside the project repo) |

### Commit format in workbench-data

Every commit Workbench makes to `workbench-data` reads `<kind>(<scope>): <summary>` (or `<kind>: <summary>` when it touches several entries), so the history can be filtered by kind and project:

| Kind | Made by | Example |
|---|---|---|
| `create` | `workbench new` | `create(robot-arm): new project Robot Arm` |
| `update` | `workbench set` / `add` (workbench-update) | `update(robot-arm/gripper): status in_progress, notes` |
| `review` | `workbench push` (workbench-review) | `review(robot-arm): gripper working, wrist calibration left to do` |
| `edit` | the dashboard | `edit(robot-arm/wrist): name, summary` |
| `import` | `workbench learning import` | `import(learning/control-theory): merge into existing learning path` |
| `attach` | `workbench attach` | `attach(gripper-firmware): under robot-arm/gripper` |
| `detach` | `workbench detach` | `detach(gripper-firmware): from robot-arm` |
| `link` | `workbench link` | `link(robot-arm): add repo robot-arm-firmware` |
| `archive` | `workbench archive-empty` | `archive: 3 empty discovered project(s)` |
| `discover` | local or GitHub discovery | `discover: register 2 repo(s) via local discovery: new-tool, scratch` |
| `undo` | `workbench undo` | `undo(robot-arm/gripper): revert 1a2b3c4d (update(robot-arm/gripper): status done)` |

`--project` matches a project and everything under it (`robot-arm`, `robot-arm/gripper`); learning paths use `learning/<id>`. Commits made before this format don't match any filter.

```bash
workbench history --kind review,edit --project robot-arm -n 10   # milestone reviews and dashboard edits
```

## Architecture on One Page

```
 Laptop                                   GitHub                    Home server
┌──────────────────────────┐        ┌──────────────────┐      ┌───────────────────────────┐
│ project repos (any)      │        │ workbench-data   │      │ workbench server (Python) │
│  agent turn ends         │        │ (private)        │◀─────│  poll every 10 min        │
│   → Stop hook            │ push   │  projects/       │ fetch│  validate → snapshot swap │
│   → skills               │───────▶│  learning/       │      │  GitHub discovery (daily) │
│   → `workbench push`     │        └──────────────────┘◀─────│  web status edits (push)  │
│ launchd: local discovery │──push──────────▲                 │  serves UI + JSON API     │
└──────────────────────────┘                                  └─────────────▲─────────────┘
                                                                            │ Tailscale only
                                                                        browser (user)
```

### Core Components

1. **Two-Repository Split (`workbench` vs. `workbench-data`):**
   - `workbench` (this repository) contains the Python CLI, validation/privacy engine, FastAPI server, and React/TypeScript UI.
   - `workbench-data` is a private repository containing only `workbench.yaml`, `projects/<id>/project.yaml`, and `learning/<id>/path.yaml` (+ optional `topics/<topic-id>.md`). Automated pushes never touch executable code.
2. **Deterministic Repository Identity (`src/workbench/identity.py`):**
   - Repositories are identified by their canonical root commit SHA (`git rev-list --max-parents=0 HEAD`, choosing the lexicographically smallest SHA if multiple roots exist) plus optional `github` (`owner/repo`) fork disambiguation — no marker files inside your project repos.
3. **Concurrency-Safe Agent Workflow (`src/workbench/cli.py`, `src/workbench/gitops.py`):**
   - `workbench begin` creates an isolated git worktree under `~/.workbench/work/<id>-<rand>/` off `origin/main`, so multiple agents can run at once without colliding.
   - `workbench push` sets `updated`/`origin`, runs structural validation (`validate.py`), deterministic secret/PII scanning (`privacy.py`), semantic diff checks (`semantic.py`, which blocks silent removals), and an optional isolated LLM privacy review, then commits, rebases onto `origin/main` (with 3× race retry), re-validates the whole repository, pushes, and removes the worktree.
4. **Validation-Gated Server (`src/workbench/server/`):**
   - Polls `origin/main` every 10 minutes, validates the entire repository in a staging worktree, then atomically writes `snapshot.json` and swaps the in-memory snapshot. If validation fails, the server keeps serving the last known-good snapshot and reports the error at `/api/status`.
   - Web edits (`server/writes.py`) go through the same validation and semantic checks before they are committed and pushed; all git work on the server is serialized by one lock.

### Repository Layout

```
src/workbench/      CLI, models (single source of truth for the schema), validation, privacy, git ops
src/workbench/server/   FastAPI app, background sync, web write path
web/                React + TypeScript UI (React Flow map)
schema/             JSON Schemas generated from the models (`make schema`)
agent/              Claude skills, Gemini instructions and commands, chat prompts
tests/              pytest suite, including Playwright UI tests
demo/               Demo environment runner
```

## Security Notes

- The server has no login of its own and its write endpoints are protected only by a custom header against cross-site requests. Run it reachable **only over Tailscale** (or another private network), and add authentication before exposing it any further.
- Keep credentials out of this repository: `.env`, deploy keys and local tool settings are listed in `.gitignore`.

See [`DECISIONS.md`](DECISIONS.md) for engineering decisions and [`SETUP_REAL.md`](SETUP_REAL.md) for go-live instructions.
