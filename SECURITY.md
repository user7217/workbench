# Security & Privacy Policy (`SECURITY.md`)

Workbench is designed to be updated automatically by AI coding agents (Claude Code, Gemini CLI) and background discovery jobs. Because automated tools can make mistakes, Workbench enforces strict architectural boundaries between your source code, your metadata repository, and your network.

---

## 1. Two-Repository Isolation (`workbench` vs. `workbench-data`)

Workbench separates executable application code from project metadata:

- **`workbench` (Application Repository):** Contains the Python CLI, FastAPI server, and React frontend. Automated agents and discovery jobs never push to this repository.
- **`workbench-data` (Private Metadata Repository):** Contains only declarative data files (`workbench.yaml`, `projects/<id>/project.yaml`, `learning/<id>/path.yaml`, and `learning/<id>/topics/<id>.md`).
- **Path Whitelist Enforcement:** Both `workbench push` and the server's staging-worktree validator ([`src/workbench/validate.py`](src/workbench/validate.py)) reject any commit that touches a path outside those four patterns. Executable scripts, dotfiles, and Git hooks inside `workbench-data` are never executed.

---

## 2. Deterministic Privacy & Secret Firewall

Before any CLI command (`workbench push`, `workbench set`, `workbench add`, `workbench discover`) or web UI edit commits to `workbench-data`, [`src/workbench/privacy.py`](src/workbench/privacy.py) scans every string field against two layers:

1. **Deterministic Regex & `detect-secrets` Rules:**
   - Cloud & API credentials: AWS access keys (`AKIA...`), GitHub tokens (`ghp_...`, `github_pat_...`), OpenAI/Anthropic keys (`sk-...`), Slack tokens (`xox...`), JWTs, and PEM private keys.
   - Inline credentials: Password assignments (`password = ...`, `api_key: ...`) and credentialed connection URIs (`postgres://user:pass@...`).
   - Local machine paths: macOS/Linux/Windows user paths (`/Users/...`, `/home/...`, `C:\Users\...`, `~/...`).
   - Private network identifiers: RFC 1918 IPv4 addresses (`10.x.x.x`, `192.168.x.x`, `172.16-31.x.x`), loopback IPs (`127.x.x.x`), and `.local` mDNS hostnames.
   - Personal email addresses (only `@example.com` and `@example.invalid` placeholder addresses are permitted).
2. **Zero Secret Echoing:**
   - When a violation is detected, Workbench reports **only** the YAML field path and pattern identifier (e.g., `Privacy violation in 'phases[0].notes': detected aws_access_key`). The matched value is never printed to terminal output, logs, or HTTP error responses.
3. **Optional Isolated LLM Privacy Review (opt-in, off by default):**
   - Nothing is sent to an LLM for review unless you turn it on: `workbench setup` asks (default no), or set `privacy_review = "auto"` or `"required"` in `~/.workbench/config.toml`. Then `workbench push` pipes the changed free-text fields to `reviewer_command` in an empty temporary directory. The default command runs Claude Haiku with all tools disabled (`--tools ""`) and no MCP servers, so the reviewer can only answer. A `reviewer_command` you set yourself is run as given. Quick updates (`workbench set` / `add`) and dashboard edits skip this step; the deterministic scan above always runs.

---

## 3. Headless Updater Data Flow

With `update_mode = "headless"`, after each agent message that changed files the hook starts a background job that sends `updater_command` (by default the same tool-less Claude Haiku run) the last user message, the assistant's reply, the previous reply, the project's `project.yaml`, `git diff --stat`, new file names and new commit subjects. That is the same conversation already shared with the coding agent's provider, but it does leave your machine once more.

The agent gets no tools and can only answer with JSON. Workbench applies only `set` and `add` operations on existing items, never renames or removes anything, clamps text lengths, and pushes the result through validation and the deterministic privacy scan, so every change is one ordinary commit you can `workbench undo`. Use `update_mode = "session"` (the default) or `"off"` to keep this from running.

---

## 4. Scoped Credentials

As detailed in **[SETUP_REAL.md](SETUP_REAL.md)**:

- **Server Push Access:** Use a repository-scoped GitHub **SSH Deploy Key** attached **only** to `<you>/workbench-data` (`./secrets/deploy_key`, `chmod 600`). Do not place your personal full-account SSH key on the server.
- **Server Discovery Token:** Use a fine-grained GitHub Personal Access Token with **Read-only** permissions (`Metadata: Read-only`, `Contents: Read-only`).

---

## 5. Network Binding & CSRF Protection

- **Bind Only to Loopback or Tailscale:** The Workbench server does not implement user authentication of its own; it relies on network-level access control. Always bind `WORKBENCH_BIND` to `127.0.0.1` (local Mac) or your server's `100.x.y.z` [Tailscale](https://tailscale.com) IPv4 address—never expose port `8420` on a public `0.0.0.0` internet interface, and never publish it with Tailscale Funnel or a reverse proxy open to the internet. Tailscale Serve (tailnet only) is fine; see [SETUP_REAL.md](SETUP_REAL.md).
- **CSRF Header Requirement:** Every mutating `POST /api/...` route on the server ([`src/workbench/server/app.py`](src/workbench/server/app.py)) requires `Content-Type: application/json` and the custom header `X-Workbench: 1`, preventing cross-site form submissions from untrusted browser tabs.

---

## 6. Reporting a Vulnerability

Please report security problems privately through GitHub's **private vulnerability reporting** on this repository (**Security → Report a vulnerability**), not in a public issue. Include what you found, how to reproduce it, and what it exposes. Workbench is a personal project, so there is no formal response time, but reports are read and fixed as soon as possible.
