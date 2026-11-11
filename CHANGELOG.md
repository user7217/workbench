# Changelog

Notable changes to Workbench. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version (0.x.0) may change the data format or the CLI; such changes are listed under **Changed** with what to do about them.

Each release on [GitHub Releases](https://github.com/user7217/workbench/releases) uses its section below as release notes. Changes waiting for the next release go under **Unreleased**; [docs/releasing.md](docs/releasing.md) describes how a release is cut.

## [Unreleased]

### Added

- **Today's Work:** a new **Today** tab holds what was chosen for the day. A sun button beside every phase, topic, component and subtopic (and a **Today** button on every project and learning path) adds the item with everything nested under it and the unfinished items it depends on. The tab has one card per project or learning path, like Home, with no project ranked above another. Each card shows its own step to do **Now** and the one **Next** in two colours, with **Done**, **Start** and **Do first**; finishing a step moves that card's next one into place. On a new day it asks which unfinished topics stay. See docs/today.md.
- **Today's Work can be turned on or off:** in `workbench setup`, with `workbench today on|off`, from the desktop app's menu (**Today's Work**), and with `WORKBENCH_TODAY` on a Docker server. Off removes the tab and its buttons; the stored list is kept.
- **Phone layout:** on screens up to 720px wide the sidebar is a tab bar at the bottom, pages use the full width and controls are large enough to tap. The learning page now has the Navigation / Contents / Visual Map tabs the project page had.

### Changed

- The data repository may now contain `today.yaml`, the Today's Work list. Every Workbench install (server, desktop app, CLI) has to be on this version or newer before Today's Work is used: older versions reject the file as a disallowed path and stop syncing until they are updated.
- **Dependency map layout:** the project is a header across the top, and the phases are dealt into as many columns as the panel's width holds (1, 2, 3 across, then 4 under 1), each with its parts listed under it. Each column stacks its own phases, so a folded phase leaves no gap. The header reaches each column's phases through a rail down the left of that column, apart from the lines that join a phase to its parts, so a phase in a second row can no longer look like the child of the one above it. A phone shows one column.
- **Look:** calmer borders and surfaces, the Inter typeface bundled with the UI (the same text on every system), thinner scrollbars, and roomier Home cards.

### Fixed

- After an update, browsers kept showing the previous UI until a hard reload; the page is now always checked with the server.
- The dependency map logged errors, and was not fitted to its panel, when that panel started hidden behind a tab on narrow screens.

## [0.1.3] - 2026-11-10

### Added

- **Homebrew:** `brew install --cask user7217/workbench/workbench-dashboard` installs the macOS app and links the `workbench` command. Each release updates the cask.
- **PyPI:** the CLI and server are published as `workbench-dashboard`: `uv tool install workbench-dashboard` or `pipx install workbench-dashboard`. The command is still `workbench`.

### Changed

- The Python package is now named `workbench-dashboard` (wheels are `workbench_dashboard-<version>-…`); the import package and the `workbench` command are unchanged. A CLI installed earlier with `uv tool install .` is registered as the tool `workbench`: run `uv tool uninstall workbench` once, then install `workbench-dashboard`. In a development checkout, run `uv sync --reinstall-package workbench-dashboard` once if `uv run workbench` stops finding the command.

## [0.1.2] - 2026-11-07

### Added

- **The macOS app updates itself:** it checks GitHub Releases at launch and every 6 hours, installs a signed new version in place, and offers **Restart to Update** in the menu. **Check for Updates…** checks on demand. Apps before this version need one manual update to get it.

### Fixed

- `packaging/docker/update.sh` no longer fails, without a message, when the server's clone holds an outdated copy of a release tag; it takes GitHub's copy and logs any fetch error.

## [0.1.1] - 2026-11-07

### Added

- **Automatic server updates:** `packaging/docker/update.sh` and an hourly systemd timer move a Docker server to each new release (releases only, never unreleased commits). To switch an existing server from building on the server to released images, see "Automatic Updates from Releases" in docs/setup.md.
- **Browser tab icon:** the dashboard shows the app's icon in the browser tab and as the iOS home-screen icon.
- **Desktop docs:** a first-run guide for setting up a new laptop from the app alone.

### Fixed

- Release pages no longer list a stray `.dockerbuild` file among the downloads.
- The error for a file the data repository does not allow now names the allowed paths correctly.
- SECURITY.md matches the code: network binding for each run mode, what the headless updater can change, release checksums, and supported versions.

## [0.1.0] - 2026-11-07

The first release. Everything below is new.

### Added

- **Data model and validation:** projects with phases or topics, components nested to any depth with dependencies, child projects, learning paths with topics and subtopics, all in a private `workbench-data` Git repository. Every change is checked against JSON Schemas, a semantic diff (no lost ids, no mass deletions, no status regressions) and a privacy scanner for secrets, local paths and private addresses, with an optional LLM privacy review (off by default).
- **CLI:** `workbench show`, `set`, `add`, `focus`, `new`, `attach`, `detach`, `archive-empty`, `push`, `history`, `revert`, learning-path import and update, local and GitHub repository discovery, `setup`, `install`, `serve`, and `workbench --version`. Repositories are identified by their root commit, so renames and moves keep their history.
- **Dashboard server:** FastAPI server that syncs `workbench-data`, validates each fetched commit in a staging worktree and serves the last good snapshot. Edits made in the dashboard are validated and committed back. Daily GitHub discovery with an optional read-only token (private repositories included).
- **Web UI:** home view of projects and learning paths, three-panel project and learning workspaces, an interactive dependency map with a divider that moves across the whole column, inline editing of text and structure, and a suggestions inbox (Keep or Dismiss). The UI is split into a fixed state layer and a replaceable look, with a minimal example look.
- **Coding-agent integration:** Claude Code and Gemini CLI hooks with `workbench-update` and `workbench-review` skills, and a headless updater that records each agent turn in the background through a tool-less model call.
- **Server mode:** Docker image and Compose file for an always-on machine reached over Tailscale, with a deploy key for the data repository. Also published as `ghcr.io/user7217/workbench`.
- **Desktop mode (macOS):** a menu-bar app that runs the server on the laptop from login, restarts it if it crashes, opens the dashboard in the browser and can put the `workbench` command on the PATH. Distributed as an unsigned `.dmg` for Apple silicon.
- **Security:** write endpoints require `X-Workbench: 1` and JSON; a server listening on localhost rejects other `Host` names, blocking DNS rebinding.
- **Releases:** one version number for the Python package, web UI and desktop app (`scripts/version.py`), this changelog, and a release workflow that builds the `.dmg`, the wheel and sdist, and the Docker image for each `v*` tag.

[Unreleased]: https://github.com/user7217/workbench/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/user7217/workbench/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/user7217/workbench/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/user7217/workbench/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/user7217/workbench/releases/tag/v0.1.0
