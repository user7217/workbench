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
3. **Optional Isolated LLM Privacy Review:**
   - When `privacy_review = "auto"` or `"required"` in `~/.workbench/config.toml`, changed free-text fields are piped to a subprocess running in an empty temporary directory with no repository tools enabled.

---

## 3. Scoped Credentials

As detailed in **[SETUP_REAL.md](SETUP_REAL.md)**:

- **Server Push Access:** Use a repository-scoped GitHub **SSH Deploy Key** attached **only** to `<you>/workbench-data` (`./secrets/deploy_key`, `chmod 600`). Do not place your personal full-account SSH key on the server.
- **Server Discovery Token:** Use a fine-grained GitHub Personal Access Token with **Read-only** permissions (`Metadata: Read-only`, `Contents: Read-only`).

---

## 4. Network Binding & CSRF Protection

- **Bind Only to Loopback or Tailscale:** The Workbench server does not implement user authentication of its own; it relies on network-level access control. Always bind `WORKBENCH_BIND` to `127.0.0.1` (local Mac) or your server's `100.x.y.z` [Tailscale](https://tailscale.com) IPv4 address—never expose port `8420` on a public `0.0.0.0` internet interface.
- **CSRF Header Requirement:** Every mutating `POST /api/...` route on the server ([`src/workbench/server/app.py`](src/workbench/server/app.py)) requires `Content-Type: application/json` and the custom header `X-Workbench: 1`, preventing cross-site form submissions from untrusted browser tabs.
