# Changelog

Notable changes to Workbench. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version (0.x.0) may change the data format or the CLI; such changes are listed under **Changed** with what to do about them.

Each release on [GitHub Releases](https://github.com/user7217/workbench/releases) uses its section below as release notes. Changes waiting for the next release go under **Unreleased**; [docs/releasing.md](docs/releasing.md) describes how a release is cut.

## [Unreleased]

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

[Unreleased]: https://github.com/user7217/workbench/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/user7217/workbench/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/user7217/workbench/releases/tag/v0.1.0
