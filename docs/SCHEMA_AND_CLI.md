# Workbench Schema, CLI & API Reference (`docs/SCHEMA_AND_CLI.md`)

This reference documents the exact YAML schemas for `workbench-data`, configuration files (`~/.workbench/config.toml`), server environment variables, and HTTP API endpoints.

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
  - github: youruser/weather-station
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
  - github: youruser/portfolio-site
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

Created by `workbench setup` (supports `.toml` or `.yaml`):

```toml
data_repo_url = "git@github.com:<YOUR_GITHUB_USER>/workbench-data.git"
branch = "main"
github_owners = ["<YOUR_GITHUB_USER>"]
user_emails = ["you@users.noreply.github.com"]
scan_roots = ["~/Documents", "~/code", "~/projects"]

# "inline" (default: prompt coding session) or "headless" (spawn background agent)
update_mode = "inline"
updater_command = 'claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'

# "auto" | "required" | "off"
privacy_review = "auto"
```

---

## 4. Server Environment Variables

Read by `create_app()` in [`src/workbench/server/app.py`](../src/workbench/server/app.py) and [`docker-compose.yml`](../docker-compose.yml):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `WORKBENCH_DATA_REMOTE` | `config.toml` `data_repo_url` | Git URL or path of the `workbench-data` repository. |
| `WORKBENCH_BRANCH` | `main` | Branch tracked by the server in `workbench-data`. |
| `WORKBENCH_STATE_DIR` | `/tmp/workbench-server-state` (`/data` in Docker) | Directory where the server stores its Git clone (`repo/`) and cached `snapshot.json`. |
| `WORKBENCH_POLL_SECONDS` | `600` | Interval (in seconds) between background `git fetch` + validation polls. |
| `WORKBENCH_GITHUB_OWNERS` | `""` | Comma-separated GitHub usernames for daily server-side repository discovery. |
| `WORKBENCH_GITHUB_READ_TOKEN` | `""` | Optional fine-grained read-only GitHub PAT for server-side discovery. |
| `WORKBENCH_BIND` | `127.0.0.1` | Host IP to bind in `docker-compose.yml` (set to your server's `100.x.y.z` Tailscale IP in production). |
| `WORKBENCH_PORT` | `8420` | HTTP port for the FastAPI + React dashboard server. |
| `WORKBENCH_WEB_DIST` | `<repo>/web/dist` | Optional override path to the compiled frontend static bundle. |

---

## 5. Server HTTP API Reference

All `POST` endpoints require the custom request header `X-Workbench: 1` (CSRF protection) and commit changes atomically to `workbench-data`.

| Method & Route | Request Body | Description |
| :--- | :--- | :--- |
| `GET /api/snapshot` | — | Return the current validated snapshot (`projects`, `learning`, `topic_markdowns`, `commit`, `synced_at`). |
| `GET /api/status` | — | Return sync health (`last_good_commit`, `last_checked_at`, `last_error`). |
| `POST /api/sync` | `{}` | Trigger an immediate `git fetch` + validation sync against `origin/main`. |
| `POST /api/projects/{id}/status` | `{"status": "..."}` | Update a project's top-level status (`planned`, `active`, `paused`, `done`, `archived`). |
| `POST /api/projects/{id}/fields` | `{"name"?, "description"?}` | Update a project's name and/or description. |
| `POST /api/projects/{id}/parent` | `{"parent": "<id>\|null", "under": "<id>\|null"}` | Attach or detach a child project under a parent project's item. |
| `POST /api/projects/{id}/items` | `{"name": "...", "under"?: "...", "status"?, "summary"?, "notes"?}` | Add a new phase/topic (`under: null`) or nested component (`under: "<id>"`). |
| `POST /api/projects/{id}/items/{item_id}/status` | `{"status": "..."}` | Update a phase, topic, or component status (`not_started`, `in_progress`, `done`, `parked`). |
| `POST /api/projects/{id}/items/{item_id}/fields` | `{"name"?, "summary"?, "description"?, "notes"?, "items"?}` | Edit an item's text fields or bullet list in place. |
| `POST /api/projects/{id}/items/{item_id}/move` | `{"under"?: "<id>\|null", "position"?: "before\|after", "target"?: "<id>"}` | Reorder an item among siblings or move it under a different parent. |
| `POST /api/projects/{id}/items/{item_id}/delete` | `{}` | Delete a phase, topic, or component from a project. |
| `POST /api/projects/{id}/suggestions/{sug_id}/keep` | `{}` | Promote a suggestion from the inbox into the project tree. |
| `POST /api/projects/{id}/suggestions/{sug_id}/dismiss` | `{}` | Dismiss and remove a suggestion from the inbox. |
| `POST /api/learning/{id}/status` | `{"status": "..."}` | Update a learning path's top-level status. |
| `POST /api/learning/{id}/fields` | `{"name"?, "description"?}` | Update a learning path's name and/or description. |
| `POST /api/learning/{id}/items/{item_id}/status` | `{"status": "..."}` | Update a learning topic or subtopic status (`not_started`, `in_progress`, `done`). |
| `POST /api/learning/{id}/items/{item_id}/fields` | `{"name"?, "summary"?, "description"?, "notes"?}` | Update a learning topic or subtopic's text fields. |
