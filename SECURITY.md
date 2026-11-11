# Security & Privacy Policy (`SECURITY.md`)

Workbench is designed to be updated automatically by AI coding agents (Claude Code, Gemini CLI) and background discovery jobs. Because automated tools can make mistakes, Workbench enforces strict architectural boundaries between source code, the metadata repository, and the network.

## Supported Versions

Workbench is pre-1.0. Security fixes go into the next release only; older versions are not patched. The latest release is on [GitHub Releases](https://github.com/user7217/workbench/releases/latest).

---

## 1. Two-Repository Isolation (`workbench` vs. `workbench-data`)

Workbench separates executable application code from project metadata:

- **`workbench` (Application Repository):** Contains the Python CLI, FastAPI server, and React frontend. Automated agents and discovery jobs never push to this repository.
- **`workbench-data` (Private Metadata Repository):** Contains only declarative data files (`workbench.yaml`, `today.yaml`, `projects/<id>/project.yaml`, `learning/<id>/path.yaml`, and `learning/<id>/topics/<id>.md`).
- **Path Whitelist Enforcement:** Every write path (`workbench push` and the other CLI commands, dashboard edits, the headless updater) and the server's staging-worktree validator, which checks every fetched commit before serving it, ([`src/workbench/validate.py`](src/workbench/validate.py)) reject any commit that touches a path outside those five patterns. Executable scripts, dotfiles, and Git hooks inside `workbench-data` are never executed.

---

## 2. Deterministic Privacy & Secret Firewall

Before any CLI command (`workbench push`, `workbench set`, `workbench add`, `workbench discover`), web UI edit or headless update commits to `workbench-data`, [`src/workbench/privacy.py`](src/workbench/privacy.py) scans every string field and topic note against the rules below. The server runs the same scan on every commit it fetches, so a commit pushed around the CLI is not served either.

1. **Deterministic Regex & `detect-secrets` Rules:**
   - Cloud & API credentials: AWS access keys (`AKIA...`), GitHub tokens (`ghp_...`, `github_pat_...`), OpenAI/Anthropic keys (`sk-...`), Slack tokens (`xox...`), JWTs, and PEM private keys.
   - Inline credentials: assignments to secret-like keys (`password = ...`, `api_key: ...`) and credentialed connection URIs (`postgres://user:pass@...`).
   - Local machine paths: macOS/Linux/Windows user paths (`/Users/...`, `/home/...`, `C:\Users\...`, `~/...`).
   - Private network identifiers: RFC 1918 IPv4 addresses (`10.x.x.x`, `192.168.x.x`, `172.16-31.x.x`), loopback IPs (`127.x.x.x`), and `.local` mDNS hostnames.
   - Personal email addresses (only `@example.com` and `@example.invalid` placeholder addresses are permitted).
2. **Zero Secret Echoing:**
   - When a violation is detected, Workbench reports **only** the YAML field path and pattern identifier (e.g., `Privacy violation in 'phases[0].notes': detected aws_access_key`). The matched value is never printed to terminal output, logs, or HTTP error responses.
3. **Optional Isolated LLM Privacy Review (opt-in, off by default):**
   - Nothing is sent to an LLM for review unless it is turned on: `workbench setup` asks (default no), or set `privacy_review = "auto"` or `"required"` in `~/.workbench/config.toml`. Then `workbench push` pipes the changed free-text fields to `reviewer_command` in an empty temporary directory. The default command runs Claude Haiku with all tools disabled (`--tools ""`) and no MCP servers, so the reviewer can only answer. A custom `reviewer_command` is run as given. Quick updates (`workbench set` / `add`) and dashboard edits skip this step; the deterministic scan above always runs.

---

## 3. Headless Updater Data Flow

With `update_mode = "headless"`, after each agent message that changed files or made a commit the hook starts a background job that sends `updater_command` (by default the same tool-less Claude Haiku run) the last user message, the assistant's reply, the previous reply, the project's `project.yaml`, `git diff --stat`, new file names and new commit subjects. That is the same conversation already shared with the coding agent's provider, but it does leave the machine once more.

With the default command the agent gets no tools and can only answer with JSON; a custom `updater_command` is run as given. Workbench applies only `set` operations on existing items, `add` operations under existing items and suggestions for the project's inbox (kept or dismissed by hand in the dashboard). It never renames or removes anything, clamps text lengths, and pushes the result through validation and the deterministic privacy scan, so every change is one ordinary commit that `workbench undo` can revert. Use `update_mode = "session"` (the default) or `"off"` to keep this from running.

---

## 4. Scoped Credentials

As detailed in **[docs/setup.md](docs/setup.md)**:

- **Server Push Access:** Use a repository-scoped GitHub **SSH Deploy Key** attached **only** to `<github-user>/workbench-data` (`./secrets/deploy_key`, `chmod 600`). Do not place a personal full-account SSH key on the server.
- **Server Discovery Token:** Use a fine-grained GitHub Personal Access Token with **Read-only** permissions (`Metadata: Read-only`, `Contents: Read-only`).
- **Desktop Mode:** The macOS app has no credentials of its own. Its server uses the laptop's existing Git login, the same one the CLI uses, to fetch and push `workbench-data`.

---

## 5. Network Binding & CSRF Protection

- **Bind Only to Loopback or Tailscale:** The Workbench server does not implement user authentication of its own; it relies on network-level access control. In Docker, set `WORKBENCH_BIND` to the server's `100.x.y.z` [Tailscale](https://tailscale.com) IPv4 address (or `127.0.0.1`); `workbench serve` listens on `127.0.0.1` unless `--host` says otherwise; the desktop app always listens on `127.0.0.1`. Never expose port `8420` on a public `0.0.0.0` internet interface, and never publish it with Tailscale Funnel or a reverse proxy open to the internet. Tailscale Serve (tailnet only) is fine; see [docs/setup.md](docs/setup.md).
- **CSRF Header Requirement:** Every mutating `POST /api/...` route on the server ([`src/workbench/server/app.py`](src/workbench/server/app.py)) requires `Content-Type: application/json` and the custom header `X-Workbench: 1`, preventing cross-site form submissions from untrusted browser tabs.
- **Host Check on Localhost:** When the server listens on `127.0.0.1` or `localhost` (`workbench serve` and the desktop app), it rejects requests whose `Host` header is not `localhost` or `127.0.0.1`. A malicious page that points its own domain at `127.0.0.1` (DNS rebinding) would otherwise become same-origin with the dashboard and could read it and send the `X-Workbench` header. The allowed names come from `WORKBENCH_ALLOWED_HOSTS`.

---

## 6. Release Downloads

Release files are built by the [release workflow](.github/workflows/release.yml) on GitHub-hosted runners from the tagged commit, and each release lists their SHA-256 checksums in `SHA256SUMS`. The macOS app is **not signed or notarized** yet, so macOS cannot vouch for it: download it only from this repository's Releases page and compare it with `SHA256SUMS` (`shasum -a 256 Workbench_*.dmg`) before opening it.

Once installed, the app updates itself over HTTPS from this repository's latest release, and installs an update only if its archive carries a valid signature from the updater key (an Ed25519 minisign key whose public half is built into the app). The private key exists only on the maintainer's machine and as an encrypted GitHub Actions secret; see [docs/releasing.md](docs/releasing.md#5-updater-signing-key).

---

## 7. Reporting a Vulnerability

Please report security problems privately through GitHub's **private vulnerability reporting** on this repository (**Security → Report a vulnerability**), not in a public issue. Include what was found, how to reproduce it, and what it exposes. Workbench is a personal project, so there is no formal response time, but reports are read and fixed as soon as possible.
