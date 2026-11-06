# Contributing to Workbench

Contributions to Workbench are welcome. This guide covers setting up a development environment, running the test suite, keeping schemas and TypeScript types synchronized, and submitting changes.

---

## 1. Development Environment Setup

### Prerequisites

- **Python 3.12+** managed with [uv](https://docs.astral.sh/uv/)
- **Node.js 20+** and `npm` (22+ for the desktop app)
- **Git**
- **Rust** (`brew install rust` or [rustup](https://rustup.rs)), only for the macOS desktop app

### Install Dependencies

From the repository root:

```bash
uv sync
(cd web && npm install)
```

---

## 2. Running the Demo & Dev Servers

### Full Self-Contained Demo (`make demo`)

Builds the frontend bundle and starts the FastAPI server against a temporary, disposable `workbench-data` repository seeded with `demo-*` fixtures:

```bash
make demo
# Open http://127.0.0.1:8420
```

### Frontend Hot-Reload Development

To work on the React UI (`web/src/ui/` or a custom look) with Vite hot module replacement:

```bash
# Terminal 1: Start the demo backend API on port 8441
WORKBENCH_PORT=8441 uv run python scripts/run_demo.py

# Terminal 2: Start Vite dev server proxying /api to port 8441
cd web
WORKBENCH_API=http://127.0.0.1:8441 npm run dev
```

### Desktop App (macOS)

The menu-bar app lives in `desktop/` (Tauri 2, Rust) and runs a PyInstaller-frozen copy of the CLI and server. [docs/desktop.md](docs/desktop.md) §8 lists the parts.

```bash
make desktop-binary                            # frozen CLI + server only, in desktop/build/workbench/
make desktop                                   # the whole app: .app and .dmg under desktop/src-tauri/target/release/bundle/
make desktop-release                           # plus the signed update archive (needs the updater key; docs/releasing.md)
(cd desktop/src-tauri && cargo clippy)         # Rust lint; keep it at zero warnings
```

A change to the Python side reaches the app only after `make desktop-binary` (or `make desktop`) runs again. Quit any running Workbench app before testing a new build, since a second copy only opens the dashboard of the first.

---

## 3. Keeping Schemas and TypeScript Types in Sync

[`src/workbench/models.py`](src/workbench/models.py) is the single source of truth for both JSON Schemas (`schema/*.json`) and frontend TypeScript interfaces (`web/src/types/*.d.ts`).

After any change to a Pydantic model in `src/workbench/models.py`, regenerate the schemas and TypeScript types before running tests or committing:

```bash
make build
```

Or run the generation steps individually:

```bash
uv run python -c "from pathlib import Path; from workbench.models import write_json_schemas; write_json_schemas(Path('schema'))"
(cd web && npm run generate-types)
```

> **Note:** the maintainer's test suite checks that `schema/project.schema.json`, `schema/learning.schema.json`, and `web/src/types/*.d.ts` match `models.py` byte-for-byte, so commit the regenerated files together with the model change.

---

## 4. Testing & Quality Checks

> **The test suites are not published.** `tests/` (Python and Playwright) and `web/src/core/__tests__/` (Vitest) are kept out of the repository (see `.gitignore`), so in a fresh clone `make test` finds no tests and `npm test` reports no test files. Check a change with:
>
> ```bash
> uv run python -c "import workbench.cli, workbench.server.app"   # the backend imports cleanly
> (cd web && npx tsc --noEmit -p . && npm run typecheck:look && npm run build)
> make demo                                                     # then try the change in the browser
> ```
>
> and describe what was tested in the pull request. The rest of this section describes how the suite runs where it exists.

Run the full build and test suite with:

```bash
make test
```

### Lint & Format

Python uses [Ruff](https://docs.astral.sh/ruff/) (configured in `pyproject.toml`) and the web UI uses [Biome](https://biomejs.dev/) (configured in `web/biome.json`):

```bash
make lint     # ruff check, ruff format --check, biome check
make format   # apply safe lint fixes and format everything
```

Biome reports the accessibility rules as warnings for now (clickable elements without keyboard support, icons without titles); new code should not add to them.

### Continuous Integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request:

- **Lint and format:** `make lint` must pass, and `scripts/version.py check` (every copy of the version agrees, `CHANGELOG.md` has an Unreleased section).
- **Web UI and generated files:** regenerates `schema/` and `web/src/types/` and fails if they differ from what is committed, then type-checks both looks and builds the UI.
- **Package (Linux, macOS, Windows):** builds the wheel, checks that it contains the UI and the agent files, installs it in a clean environment and smoke-tests `workbench --help` and `workbench install`; on Linux it also starts `workbench serve` against a demo and loads the dashboard.
- **Docker image:** builds the image without pushing it.

CI does not build the desktop app; the release workflow does, for every version tag.

The test suites are not published, so CI does not run them; run them locally before opening a pull request.

### Changelog and Releases

A change that users would notice gets a line under `## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md). Releases are cut by tagging a version; [docs/releasing.md](docs/releasing.md) has the steps and what the release workflow publishes.

### Safety & Isolation Guarantees in Tests

- **Zero user-directory side effects:** Tests must never read or modify real `~/.workbench`, `~/.claude`, `~/.gemini`, or `~/Library/LaunchAgents` paths. Always pass isolated `tmp_path` directories and override `HOME` via `monkeypatch.setenv("HOME", ...)`.
- **Zero network dependency:** All unit, integration, and Playwright E2E tests run completely offline against local bare Git repositories and mocked HTTP transports.

### Running Specific Test Suites

```bash
# Python backend unit & integration tests
uv run pytest tests/test_models_validation.py tests/test_cli_integration.py tests/test_discovery.py tests/test_server.py -v

# Frontend headless store & derivation unit tests (Vitest)
(cd web && npm test)

# End-to-end Playwright Chromium UI tests
uv run pytest tests/test_ui_e2e.py -v
```

---

## 5. Architectural Guardrails

When adding features or modifying the UI, preserve these core design constraints:

1. **No task-manager clutter:** Do not add completion percentages (`%`), item counts (`(12)`), progress fractions (`3/5`), due dates, countdowns, streaks, or priority scores.
2. **Comment-preserving YAML edits:** Always use `ruamel.yaml` helpers in [`src/workbench/yamlio.py`](src/workbench/yamlio.py) and [`src/workbench/edits.py`](src/workbench/edits.py) so comments and field ordering in `workbench-data` are preserved.
3. **Never echo matched secrets:** Any new privacy rule in [`src/workbench/privacy.py`](src/workbench/privacy.py) must report only the offending YAML field path and rule name, never the matched secret value.
4. **Keep `core/` and `ui/` separated:** Business logic, data derivation, and API calls belong in `web/src/core/`; layout, styling, and React components belong in `web/src/ui/` (see [web/README.md](web/README.md)).

---

## 6. License

Workbench is released under the [MIT License](LICENSE). By contributing, contributors agree that their contributions are licensed under it too.
