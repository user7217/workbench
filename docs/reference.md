# Workbench Schema, Configuration, CLI & API Reference

This reference documents the exact YAML schemas for `workbench-data`, the laptop configuration file (`~/.workbench/config.toml`), server environment variables, HTTP API endpoints, and every CLI command with its options.

---

## 1. Project Metadata Schema (`projects/<id>/project.yaml`)

Defined by `Project` in [`src/workbench/models.py`](../src/workbench/models.py) and exported to [`schema/project.schema.json`](../schema/project.schema.json).

### 1.1 Phased Project Example (`uses_phases: true`)

Use `uses_phases: true` when a project divides into sequential milestones or major architectural subsystems (`phases:`):

```yaml
schema_version: 1
id: weather-station
name: Weather Station
description: Solar-powered environmental telemetry node logging temperature, pressure, and wind speed over LoRa.
status: active          # planned | active | paused | done | archived
origin: agent           # manual | agent | discovered
updated: 2026-10-29
uses_phases: true
repos:
  - github: github-user/weather-station
    root_commit: 3f2a9c1e3f2a9c1e3f2a9c1e3f2a9c1e3f2a9c1e
    name: weather-station
links:
  - label: Schematics
    url: https://example.com/weather-schematics
phases:
  - id: hardware
    name: Hardware & Power
    status: done        # not_started | in_progress | done | parked
    summary: Custom 2-layer carrier PCB and LiFePO4 solar charge controller.
    components:
      - id: sensor-board
        name: Sensor carrier board
        status: done
        summary: BME280 + anemometer pulse counter interface.
        notes: Verified 18 uA deep-sleep draw on rev B board.
        depends_on: []
        links: []
        refs:
          - 3f2a9c1e3f2a
        components: []  # Optional nested sub-components (arbitrary depth)
  - id: firmware
    name: Embedded Firmware
    status: in_progress
    summary: Low-power STM32L4 firmware and LoRa packet framer.
    components:
      - id: radio-tx
        name: LoRa radio transmitter
        status: in_progress
        summary: Framed CRC-16 packet transmission with adaptive spreading factor.
        depends_on:
          - sensor-board
suggestions:
  - id: ota-bootloader
    name: Dual-bank OTA bootloader
    summary: Allow firmware updates over LoRa maintenance window.
    under: firmware
    added: 2026-10-29
```

### 1.2 Non-Phased Project Example (`uses_phases: false`)

Use `uses_phases: false` when a project is organized directly around topics (`topics:`) without phase groupings:

```yaml
schema_version: 1
id: portfolio-site
name: Personal Portfolio Site
description: Minimal static engineering notebook for hardware teardowns and architecture writeups.
status: active
origin: manual
updated: 2026-10-29
uses_phases: false
repos:
  - github: github-user/portfolio-site
    root_commit: 8b1d4e2a8b1d4e2a8b1d4e2a8b1d4e2a8b1d4e2a
    name: portfolio-site
links:
  - label: Live Site
    url: https://example.com/portfolio
phases: []
topics:
  - id: typography
    name: Typography & Layout
    status: done
    summary: Dark stylesheet, sidenote annotations, and interactive SVG schematic viewer.
    components:
      - id: schematic-pan-zoom
        name: SVG schematic viewer
        status: done
        summary: Zero-dependency pan and zoom controller for embedded KiCad plots.
```

### 1.3 Child Project Linking (`parent` & `under`)

To attach a project underneath a specific phase, topic, or component of a parent project, set both `parent` and `under`:

```yaml
parent: robot-arm
under: wrist-differential
```

### 1.4 Field Constraints & Length Limits

| Field | Type / Pattern | Constraint |
| :--- | :--- | :--- |
| `id`, `parent`, `under`, `depends_on[]` | `SlugId` | `^[a-z0-9]+(-[a-z0-9]+)*$`, 1–48 chars |
| `name` | `ShortName` | 1–80 chars |
| `description` | `DescriptionStr` | Max 500 chars |
| `summary`, `items[]` | `SummaryStr` | Max 300 chars |
| `notes` | `NotesStr` | Max 1,000 chars |
| `links[].label` | `LabelStr` | 1–40 chars |
| `links[].url` | `HttpsUrl` | Must start with `https://`, max 500 chars |
| `repos[].root_commit`, `refs[]` | `CommitSha` | `^[0-9a-f]{7,64}$` |
| `updated`, `suggestions[].added` | `DateStr` | `YYYY-MM-DD` |

---

## 2. Learning Path Schema (`learning/<id>/path.yaml`)

Defined by `LearningPath` in [`src/workbench/models.py`](../src/workbench/models.py) and exported to [`schema/learning.schema.json`](../schema/learning.schema.json).

```yaml
schema_version: 1
id: signal-processing
name: Signal Processing Basics
description: Continuous-time sampling theory, discrete Fourier analysis, and digital filter design.
status: active                  # planned | active | paused | done | archived
updated: 2026-10-29
related_projects:
  - esp32-audio-dsp
references: []
topics:
  - id: sampling
    name: Sampling & Reconstruction
    status: done                # not_started | in_progress | done
    summary: Nyquist-Shannon theorem, anti-aliasing filters, and DAC sinc roll-off.
    subtopics:
      - id: aliasing
        name: Aliasing & Folding
        status: done
        summary: Spectral folding above Fs/2 and oversampling trade-offs.
```

Optional Markdown notes for any topic or subtopic live at `learning/<path-id>/topics/<topic-or-subtopic-id>.md` (maximum 20 KB per file).

---

## 3. Laptop Configuration (`~/.workbench/config.toml`)

Created by `workbench setup` (TOML only). Every key is optional; missing keys take the defaults shown:

```toml
data_repo_url = "git@github.com:<github-user>/workbench-data.git"
branch = "main"
github_owners = ["<github-user>"]
user_emails = ["<github-user>@users.noreply.github.com"]
scan_roots = ["~/Documents", "~/code", "~/projects"]
github_api_base = "https://api.github.com"

# How the per-message update runs after each agent message:
#   "session"  (default) the hook asks the coding agent to run the workbench-update skill,
#              and asks for workbench-review after a new commit
#   "headless" a background agent records the message; the session is never blocked and
#              reviews run only when /workbench-review is called
#   "off"      the hook does nothing
# An unknown value falls back to "session". If updater_command's program is not installed,
# headless mode also falls back to "session".
update_mode = "session"
updater_command = 'claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'

# LLM privacy review of changed free text on `workbench push` (opt-in; the deterministic
# secret/path scan always runs):
#   "off"      (default) no LLM review; an unknown value also means "off"
#   "auto"     run reviewer_command when its program is installed, else skip with a warning
#   "required" refuse to push when the review fails or cannot run
privacy_review = "off"
reviewer_command = 'claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'
```

Keys that Workbench does not know are ignored and dropped the next time it writes the file. This includes `today` from earlier development builds: the Today's Work switch is now `enabled` in `today.yaml` ([section 7](#7-todays-work-list-todayyaml)).

Both commands get their prompt on stdin, run in an empty temporary directory, and must print JSON. Any agent CLI that does that can be used; only Claude Code has been tested.

---

## 4. Server Environment Variables

Read by `create_app()` in [`src/workbench/server/app.py`](../src/workbench/server/app.py), except `WORKBENCH_BIND`, `WORKBENCH_PORT` and `WORKBENCH_VERSION`, which only the Docker files ([`packaging/docker/Dockerfile`](../packaging/docker/Dockerfile), [`docker-compose.yml`](../docker-compose.yml)) and the demo use; `workbench serve` takes `--host` and `--port` instead.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `WORKBENCH_DATA_REMOTE` | none (required in Docker); `workbench serve` uses `data_repo_url` from `config.toml` | Git URL or path of the `workbench-data` repository. |
| `WORKBENCH_BRANCH` | `main` (`workbench serve`: `branch` from `config.toml`) | Branch tracked by the server in `workbench-data`. |
| `WORKBENCH_STATE_DIR` | `/tmp/workbench-server-state`; `/data` in Docker; `~/.workbench/server` with `workbench serve` | Where the server keeps its own Git clone (`repo/`) and cached `snapshot.json`. |
| `WORKBENCH_POLL_SECONDS` | `600` | Seconds between background `git fetch` + validation polls. |
| `WORKBENCH_GITHUB_OWNERS` | `""` | Comma-separated GitHub owners for daily server-side discovery (off when empty). |
| `WORKBENCH_GITHUB_READ_TOKEN` | `""` | Optional fine-grained read-only token. With it, discovery lists the token owner's private repos too; without it, public repos only. |
| `WORKBENCH_GITHUB_API_BASE` | `https://api.github.com` | GitHub API base URL (for GitHub Enterprise or tests). |
| `WORKBENCH_WEB_DIST` | see below | Folder holding the built UI (`index.html` + `assets/`). |
| `WORKBENCH_ALLOWED_HOSTS` | `""` (any); `127.0.0.1,localhost` when `workbench serve` listens on `127.0.0.1` or `localhost` | Comma-separated host names the server answers to; requests with any other `Host` header get `400`. Blocks DNS rebinding against a localhost server. |
| `WORKBENCH_BIND` | `127.0.0.1` | Host address `docker-compose.yml` publishes the port on (set it to the server's `100.x.y.z` Tailscale IP). Inside the container the server listens on `0.0.0.0`. |
| `WORKBENCH_PORT` | `8420` | Port inside the container and for `make demo`. |
| `WORKBENCH_VERSION` | `latest` | Image tag `docker-compose.yml` runs. Set in `.env`, it pins that release and stops [`packaging/docker/update.sh`](../packaging/docker/update.sh) from updating ([setup.md 2.8](setup.md#28-recommended-automatic-updates-from-releases)). |

The built UI is looked up in this order: `WORKBENCH_WEB_DIST` if set; the copy shipped inside the installed package (`workbench/web_dist`, added to the wheel when `web/dist` was built before installing); the checkout's `web/dist` (editable installs and the demo). With none of them the server logs "Web UI not found" and serves only the API.

---

## 5. Server HTTP API Reference

Every `POST` endpoint requires `Content-Type: application/json` and the custom header `X-Workbench: 1` (CSRF protection); requests without them get `415` or `403`. Bodies are JSON objects, and unknown keys are rejected with `400`. Every write except `/api/sync` validates the change, commits it to `workbench-data` (an `edit(...)` commit), pushes it, and answers `{"ok": true, "snapshot": ..., "status": ...}` with the fresh snapshot. Errors use `400` (bad input), `404` (unknown project or item), `409` (push conflict after retries) or `422` (the change would fail validation).

| Method & Route | Request Body | Description |
| :--- | :--- | :--- |
| `GET /api/snapshot` | — | Return the current validated snapshot (`projects`, `learning`, `topic_markdowns`, `today` with the Today's Work list, `updated_at` with each project's and path's last change time from git, `commit`, `synced_at`). `today` is always present: `{"enabled": true\|false, "day": "YYYY-MM-DD"\|null, "entries": [{"kind", "id", "item"?, "added"}]}`, and `{"enabled": true, "day": null, "entries": []}` when the repository has no `today.yaml` ([section 7](#7-todays-work-list-todayyaml)). The entries are sent even when `enabled` is `false`. |
| `GET /api/status` | — | Return sync health (`last_good_commit`, `last_checked_at`, `last_error`). |
| `POST /api/sync` | `{}` | Trigger an immediate `git fetch` + validation sync against `origin/main` (commits nothing). |
| `POST /api/projects/{id}/status` | `{"status": "..."}` | Update a project's top-level status (`planned`, `active`, `paused`, `done`, `archived`). |
| `POST /api/projects/{id}/fields` | `{"name"?, "description"?}` | Update a project's name and/or description. |
| `POST /api/projects/{id}/parent` | `{"parent": "<id>\|null", "under": "<id>\|null"}` | Attach or detach a child project under a parent project's item. |
| `POST /api/projects/{id}/items` | `{"name": "...", "parent_id"?: "<id>"\|null, "id"?, "status"?, "summary"?, "position"?: <int>}` | Add a phase/topic (`parent_id` null or left out) or a component under a phase/component. The id defaults to a unique slug of the name; `position` is the index among siblings (default: last). Returns `item_id`. |
| `POST /api/projects/{id}/items/{item_id}/status` | `{"status": "..."}` | Update a phase, topic, or component status (`not_started`, `in_progress`, `done`, `parked`). |
| `POST /api/projects/{id}/items/{item_id}/fields` | `{"name"?, "summary"?, "description"?, "notes"?, "items"?}` | Edit an item's text fields or bullet list in place. |
| `POST /api/projects/{id}/items/{item_id}/move` | `{"position": <int>, "parent_id"?: "<id>"}` | Move an item to index `position` among its siblings. With `parent_id`, a component moves under another phase or component (not under itself or its own descendants); phases can only be reordered. |
| `POST /api/projects/{id}/items/{item_id}/delete` | `{"confirm": true}` | Delete a phase, topic, or component and everything under it. Refused while another component still lists a removed id in `depends_on`, or a child project sits under a removed item. |
| `POST /api/projects/{id}/suggestions/{sug_id}/keep` | `{}` | Move a suggestion from the inbox into the tree as a `not_started` item under its `under` (a new phase/topic when it has none). `422` if that item no longer exists. Returns `item_id`. |
| `POST /api/projects/{id}/suggestions/{sug_id}/dismiss` | `{}` | Dismiss and remove a suggestion from the inbox. |
| `POST /api/today` | `{"day": "YYYY-MM-DD", "entries": [{"kind": "project"\|"learning", "id": "<id>", "item"?: "<id>"\|null}]}` | Replace the Today's Work list in `today.yaml` (created when missing) with `entries`, in the order given. Both keys are required; `added` is not accepted. An entry already in the list keeps its `added` date and a new one gets `day`. An empty list keeps the file with `entries: []`. Entries are not looked up, so one may name a project or item that does not exist. `400` for a bad date, kind or id, a duplicate entry, or more than 60 entries. Removing entries is never refused by the semantic diff guard. While Today's Work is turned off (`enabled: false` in `today.yaml` as it is on the remote) the answer is `403` with `{"detail": "Today's Work is turned off"}` and nothing is changed. |
| `POST /api/today/enabled` | `{"enabled": true\|false}` | Turn Today's Work on or off: sets `enabled` in `today.yaml` (created when missing) and keeps `day` and `entries`. Always allowed. Commits `edit(today): turn on` or `edit(today): turn off`; setting the value already stored commits nothing. `400` when `enabled` is missing or not a boolean. |
| `POST /api/learning/{id}/status` | `{"status": "..."}` | Update a learning path's top-level status. |
| `POST /api/learning/{id}/fields` | `{"name"?, "description"?}` | Update a learning path's name and/or description. |
| `POST /api/learning/{id}/items/{item_id}/status` | `{"status": "..."}` | Update a learning topic or subtopic status (`not_started`, `in_progress`, `done`). |
| `POST /api/learning/{id}/items/{item_id}/fields` | `{"name"?, "summary"?, "description"?, "notes"?}` | Update a learning topic or subtopic's text fields. |

---

## 6. CLI Commands

Every command runs on the laptop (where `~/.workbench/config.toml` and the `~/.workbench/data` clone live), except `workbench serve`, which runs the dashboard server. Run `workbench <command> --help` for the full help text.

| Command | Arguments & options |
| :--- | :--- |
| `workbench --version` | Print the version (the same for the CLI, server, web UI and desktop app). |
| `workbench setup` | `--home <dir>`. Interactive wizard: asks for the data repository, GitHub owners, author emails, scan roots and the optional LLM privacy review; writes `config.toml`, clones `workbench-data`, installs the agent skills, hooks and the macOS discovery job after the user types `yes`. |
| `workbench install` | `--home <dir>` (required), `--claude`, `--gemini`, `--launchd`. Install or refresh the skills and hooks for each agent, and the macOS `launchd` job. |
| `workbench show` | `--id <project>`, `--item <item-id>`, `--brief`. Without `--id`, the project registered for the current repo. |
| `workbench focus` | `--set <project>[/<item>]`, `--clear`. Without options, print the current repo's focus. |
| `workbench set <project> <item-id>` | `--status`, `--name`, `--summary`, `--notes`, `--depends-on <ids>`. Text options replace the field; an empty value clears it. |
| `workbench add <project>` | `--name` (required), `--under <item-id>`, `--id`, `--status`, `--summary`, `--notes`, `--depends-on <ids>`. Without `--under`, adds a phase (or topic). |
| `workbench begin` | `--id <project>` or `--learning <path-id>`. Prints the `project.yaml` / `path.yaml` path in a fresh worktree. |
| `workbench push <worktree>` | `-m/--message` (required), `--allow-removals`. Validates, privacy-checks, commits as `review(...)` and pushes. |
| `workbench validate [<path>]` | A metadata file or a `workbench-data` directory (default: the local clone). |
| `workbench new "<Name>"` | `--learning`, `--parent <project>`, `--under <item-id>`, `--status <status>`. |
| `workbench attach` | `--id <child>`, `--parent <project>`, `--under <item-id>` (all required). |
| `workbench detach` | `--id <child>`. |
| `workbench link` | `--id <project>`. Run inside the repo to attach it to that project. |
| `workbench archive-empty` | `--dry-run`, `--keep <id>` (repeatable). Only `origin: discovered` projects with no phases or topics. |
| `workbench discover` | `--local`. Scan `scan_roots` for repositories the user owns. |
| `workbench learning import <file>` | A `.md` outline or `.yaml` path; merges into an existing path without moving statuses back. |
| `workbench learning export <path-id>` | Print the path as a Markdown outline. |
| `workbench history [<id>]` | `-n/--limit` (default 20), `--kind <kind>[,<kind>]`, `--project <id>`. |
| `workbench undo <commit>` | Revert one `workbench-data` commit and push the revert. |
| `workbench hook-check` | `--mark-seen`. Read by the agent hooks from stdin; `--mark-seen` records the current changes as needing no update. |
| `workbench auto-update` | `--job <file>` (written by the hook) or `--transcript <file.jsonl>`, plus `--dry-run`. Run by hand inside the project repo. |
| `workbench serve` | `--host` (default `127.0.0.1`), `--port` (default `8420`). Run the dashboard server without Docker. On `127.0.0.1` or `localhost` it answers only to those host names (`WORKBENCH_ALLOWED_HOSTS`). The macOS desktop app runs this command from login ([desktop.md](desktop.md)). |
| `workbench demo init <dir>` | Create a throwaway demo environment (bare remote, fake HOME and repos) in `<dir>`; used by `make demo` and the tests. |

---

## 7. Today's Work List (`today.yaml`)

Defined by `Today` in [`src/workbench/models.py`](../src/workbench/models.py) and exported to [`schema/today.schema.json`](../schema/today.schema.json). The file sits at the root of `workbench-data`, next to `workbench.yaml`, and is optional: a repository without it is valid and its snapshot reports an empty list.

It stores only the roots chosen for the day. The dashboard expands each root into its children and prerequisites and works out the order; none of that is stored.

```yaml
schema_version: 1
enabled: true                 # the Today's Work switch; optional, true when absent
day: 2026-11-10               # the local calendar day the list was last confirmed for; null or absent if never set
entries:
  - kind: project             # project | learning
    id: robot-arm             # project id, or learning path id when kind is learning
    item: kinematics          # optional: a phase, topic or component (or a learning topic or subtopic)
    added: 2026-11-09         # the day the entry was first added
  - kind: learning
    id: signal-processing     # no item: the whole learning path
    added: 2026-11-10
```

| Field | Type / Pattern | Constraint |
| :--- | :--- | :--- |
| `schema_version` | integer | Must be `1` |
| `enabled` | boolean | Whether Today's Work is turned on; optional, `true` by default |
| `day` | `DateStr` or null | `YYYY-MM-DD`; optional |
| `entries` | list | At most 60 entries; optional, empty by default |
| `entries[].kind` | `TodayKind` | `project` or `learning` |
| `entries[].id`, `entries[].item` | `SlugId` | `^[a-z0-9]+(-[a-z0-9]+)*$`, 1–48 chars; `item` is optional |
| `entries[].added` | `DateStr` | `YYYY-MM-DD` |

- No two entries may share the same `kind`, `id` and `item`. The same `id` may appear as a whole project and again with an `item`.
- Unknown keys are rejected, as in the other files.
- Entries are not checked against the projects and learning paths in the repository. An entry that names a project, path or item that no longer exists is valid and the dashboard ignores it, so removing an item from a project never makes the repository invalid.
- The semantic diff guard and the removed-file check of `workbench push` do not apply to this file: entries leaving the list, or the file being deleted, is never refused.
- The dashboard writes the file through `POST /api/today` ([section 5](#5-server-http-api-reference)); its commits are `edit(today): add <entry>`, `edit(today): remove <entry>`, `edit(today): set <n> entries for <day>` or `edit(today): clear`, where `<entry>` is `<project>[/<item>]` or `learning/<path>[/<item>]`. The switch is written through `POST /api/today/enabled` (`edit(today): turn on` / `edit(today): turn off`). There is no CLI command for either.
- A Workbench version from before this file existed rejects it as a disallowed path, so every install (server, desktop app, CLI) must be updated before the list is used.
- `enabled: false` turns Today's Work off for every device and server reading the repository: the dashboard hides the Today tab and the sun and **Today** buttons, and `POST /api/today` is refused. The file is still validated and the day and entries are kept ([today.md](today.md#5-turning-it-off)).
