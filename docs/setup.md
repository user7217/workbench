# Workbench — Production & Server Deployment Guide

This guide walks through setting up Workbench in production: creating the private `workbench-data` repository on GitHub, deploying the dashboard server on an always-on server (via **Docker Compose + Tailscale** or standalone **`workbench serve`**), and connecting the laptop's CLI and coding agents.

> **How data flows in production:** Source code never leaves the laptop. The laptop CLI and coding agents push small, validated `.yaml` and `.md` metadata files to the private `workbench-data` repository on GitHub. The server polls `workbench-data` to serve the dashboard over Tailscale and pushes back any edits made in the web UI.

---

## Architecture & Credential Overview

Workbench uses three narrowly scoped credentials so no machine has more access than it needs:

| Machine | Credential | Scope & Purpose |
| :--- | :--- | :--- |
| **1. Laptop** | The laptop's normal Git SSH key (`~/.ssh/...`) or credential helper | Pulls and pushes metadata commits from the `workbench` CLI to `<github-user>/workbench-data`. |
| **2. Server (Git Push/Pull)** | Dedicated SSH Deploy Key (`./secrets/deploy_key`) | Registered **only** on `<github-user>/workbench-data` with **Allow write access** enabled. Allows the server to fetch updates and commit inline web UI edits. |
| **3. Server (Optional Discovery)** | Fine-grained GitHub Personal Access Token (`WORKBENCH_GITHUB_READ_TOKEN`) | **Read-only** access (`Metadata: Read-only` and `Contents: Read-only`) across the owner's repositories so the server's daily background job can discover new non-fork, non-archived GitHub repos. |

---

## Step 1: Create the Private `workbench-data` Repository

1. On GitHub, create a new **private** repository named **`workbench-data`** (leave *Add a README* and *.gitignore* unchecked).
2. On the laptop, initialize the repository with the root `workbench.yaml` marker file and push it to `main`:

```bash
mkdir -p /tmp/init-workbench-data && cd /tmp/init-workbench-data
git init -b main
cat << 'EOF' > workbench.yaml
schema_version: 1
EOF
mkdir -p projects learning
git add workbench.yaml
git commit -m "Initialize workbench-data (schema_version 1)"
git remote add origin git@github.com:<github-user>/workbench-data.git
git push -u origin main
cd ~ && rm -rf /tmp/init-workbench-data
```

---

## Step 2: Deploy the Server (Option A: Docker Compose + Tailscale — Recommended)

Running the server in Docker on a dedicated machine, NAS, Raspberry Pi, or VPS keeps the dashboard reachable across the owner's devices over [Tailscale](https://tailscale.com) without exposing any ports to the public internet.

### 2.1 Find the Server's Tailscale IP

SSH into the server, ensure Tailscale is connected, and note the server's `100.x.y.z` Tailscale IPv4 address:

```bash
tailscale ip -4
# Example output: 100.101.102.103
```

### 2.2 Clone the Workbench Application Repo on the Server

```bash
git clone https://github.com/<github-user>/workbench.git ~/workbench
cd ~/workbench
```

### 2.3 Generate and Register the Server's SSH Deploy Key

Generate a dedicated Ed25519 SSH keypair inside `./secrets/` (which is gitignored and mounted into the container by `docker-compose.yml`):

```bash
mkdir -p secrets
ssh-keygen -t ed25519 -C "workbench-server-deploy-key" -f secrets/deploy_key -N ""
chmod 600 secrets/deploy_key
cat secrets/deploy_key.pub
```

Register that public key on the **`workbench-data`** repository:
1. Open `https://github.com/<github-user>/workbench-data/settings/keys`.
2. Click **Add deploy key**.
3. Title it `workbench-server`, paste the output of `cat secrets/deploy_key.pub`, check **Allow write access**, and click **Add key**.

> **Important:** Always create `./secrets/deploy_key` *before* running `docker compose up`. If the file does not exist on the host, Docker will create an empty directory at `./secrets/deploy_key` instead of mounting a file.

### 2.4 (Optional) Create a Read-Only GitHub Token for Daily Repo Discovery

For the server to automatically discover new GitHub repositories that aren't cloned on the laptop:
1. Go to GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set **Resource owner** to `<github-user>` and **Repository access** to **All repositories**.
3. Under **Repository permissions**, grant:
   - **Contents:** `Read-only` (used to read the oldest commit SHA of each repo)
   - **Metadata:** `Read-only`
4. Generate the token and copy the `github_pat_...` string.

### 2.5 Create the `.env` Configuration File on the Server

Create `.env` in `~/workbench` on the server, setting `WORKBENCH_BIND` to the Tailscale IP from Step 2.1:

```bash
cat > .env << 'EOF'
WORKBENCH_DATA_REMOTE=git@github.com:<github-user>/workbench-data.git
WORKBENCH_BRANCH=main
WORKBENCH_POLL_SECONDS=600
WORKBENCH_BIND=100.101.102.103
WORKBENCH_GITHUB_OWNERS=<github-user>
WORKBENCH_GITHUB_READ_TOKEN=
EOF
chmod 600 .env
```

*(If a discovery token was created in Step 2.4, paste it after `WORKBENCH_GITHUB_READ_TOKEN=`.)*

### 2.6 Build, Start, and Verify the Container

```bash
docker compose up -d --build
docker compose logs -f workbench
```

What happens inside the container (`packaging/docker/Dockerfile` + `docker-compose.yml`):
- Stage 1 (`node:22-slim`) compiles the React/TypeScript frontend (`web/dist`).
- Stage 2 (`python:3.13-slim`) installs the Python package via `uv`, pre-populates `/etc/ssh/ssh_known_hosts` with GitHub's official SSH host keys (`packaging/docker/github_known_hosts`), mounts `./secrets/deploy_key` at `/root/.ssh/id_ed25519`, clones `workbench-data` into the persistent `workbench-data` Docker volume (`/data`), and starts `uvicorn` on port `8420`.

Verify from a browser on the laptop (connected to the same Tailscale tailnet):
- Open `http://100.101.102.103:8420/api/status` — confirm `"last_error": null` and `"last_good_commit"` is populated.
- Open `http://100.101.102.103:8420` — confirm the Workbench UI loads. With MagicDNS on, `http://<server-name>:8420` works too.
- Change one status in the dashboard and check that an `edit(<project-id>): ...` commit appears in `workbench-data` on GitHub (this proves the deploy key can push), then change it back.

To update the server after pulling new code changes in the future:

```bash
cd ~/workbench && git pull && docker compose up -d --build
```

**Released images instead of building:** each [release](https://github.com/user7217/workbench/releases) publishes the image `ghcr.io/user7217/workbench` for `linux/amd64` and `linux/arm64`, which `docker-compose.yml` names. Set `WORKBENCH_VERSION` in `.env` to pin one (`WORKBENCH_VERSION=0.1.0`; without it, `latest`), then update with:

```bash
cd ~/workbench && git pull && docker compose pull && docker compose up -d
```

`git pull` keeps `docker-compose.yml` in step with the image. The image is public (it takes the visibility of the public repository), so pulling needs no login; a fork's image may start private, in which case set it to public under the package's page → Package settings, or `docker login ghcr.io` on the server. `docker compose up -d --build` still builds from the checkout and tags the result with the same name.

The `workbench` command exists only inside the container, and the container has no laptop config, so run CLI commands (`workbench archive-empty`, `workbench set`, ...) on the laptop. The server picks up their commits on its next sync, or straight away when sync is pressed in the dashboard.

**After a reboot:** Docker can start before Tailscale has assigned the server its `100.x.y.z` address. Binding to that address then fails with `cannot assign requested address` and the container does not come up. Reboot once to check. If it happens, make Docker wait for Tailscale with `sudo systemctl edit docker` and add:

```ini
[Unit]
After=tailscaled.service
Wants=tailscaled.service
```

Or use Tailscale Serve (below), which binds the container to `127.0.0.1` and avoids the problem.

### 2.7 (Optional) HTTPS with Tailscale Serve

[Tailscale Serve](https://tailscale.com/kb/1312/serve) can give the dashboard an `https://<server-name>.<tailnet>.ts.net` address with a real certificate, still reachable only from the tailnet.

1. Enable **HTTPS Certificates** in the Tailscale admin console (DNS page).
2. **Check what Serve already publishes before adding anything:**
   ```bash
   tailscale serve status
   tailscale funnel status
   ```
   Serve offers HTTPS on ports `443`, `8443` and `10000`. Adding Workbench on a port that is already in use **replaces** that entry and takes down whatever was there, including a public Funnel entry. Pick a free port.
3. Bind the container to localhost: set `WORKBENCH_BIND=127.0.0.1` in `.env`, then run `docker compose up -d`.
4. Publish it on the free port (`443` here):
   ```bash
   sudo tailscale serve --bg --https=443 http://127.0.0.1:8420
   ```
5. Open `https://<server-name>.<tailnet>.ts.net` (add `:8443` or `:10000` if one of those was used). Remove it later with `sudo tailscale serve --https=443 off`.

Never put Workbench on **Funnel** (`tailscale funnel`): that publishes it on the internet, and the dashboard has no login of its own.

---

## Step 3: Deploy the Server (Option B: Without Docker via `workbench serve`)

To run the server directly on macOS or Linux without Docker:

1. **Build the frontend bundle and install the CLI tool:**
   ```bash
   cd /path/to/workbench
   (cd web && npm ci && npm run build)
   uv tool install --force .
   ```
2. **Start the server bound to `127.0.0.1` (local Mac) or the server's Tailscale IP:**
   ```bash
   export WORKBENCH_DATA_REMOTE="git@github.com:<github-user>/workbench-data.git"
   export WORKBENCH_GITHUB_OWNERS="<github-user>"
   export WORKBENCH_POLL_SECONDS="600"

   workbench serve --host 127.0.0.1 --port 8420
   ```
   `workbench serve` reads `data_repo_url` from `~/.workbench/config.toml` if `WORKBENCH_DATA_REMOTE` is not set, maintains its own clone in `~/.workbench/server`, and serves the built UI alongside the JSON API. Pass `--host <tailscale-ip>` to reach it from other devices; never bind it to a public address.
3. **Keep it running:** `workbench serve` stops when the terminal closes. Run it under `systemd` (Linux) or `launchd` (macOS) to keep it up across logouts and reboots.

If the dashboard page returns `404` and the server log says `Web UI not found`, the UI was not built before `uv tool install`: rebuild it (`cd web && npm run build`) and reinstall, or set `WORKBENCH_WEB_DIST` to a built `web/dist` folder.

---

## Step 4: Set Up the Laptop CLI & Agent Hooks

On the Mac or Linux machine where code is written:

1. **Install the `workbench` CLI globally:**
   ```bash
   cd /path/to/workbench
   uv tool install --force .
   ```
2. **Run the interactive setup wizard:**
   ```bash
   workbench setup
   ```
   The wizard prompts for:
   - **Workbench data repository URL:** `git@github.com:<github-user>/workbench-data.git` (verified immediately with `git ls-remote`).
   - **GitHub owner username(s):** `<github-user>`
   - **Git author email(s):** The commit email(s) (`git config user.email`) so local discovery only registers repositories the user authored.
   - **Local scan roots:** Directories where the code lives (e.g., `~/Documents, ~/code, ~/projects`).
   - **LLM privacy reviewer (default: no):** whether `workbench push` should also send changed text to Claude Haiku for a privacy check. The built-in secret, path and email scan runs either way. Change it later with `privacy_review` in `~/.workbench/config.toml`.

   It then clones `workbench-data` to `~/.workbench/data`, previews the exact hook and skill files it will write to `~/.claude`, `~/.gemini`, and `~/Library/LaunchAgents/com.workbench.discover.plist`, and asks for confirmation (`yes`).
3. **Run the first local discovery scan:**
   ```bash
   workbench discover --local
   ```
   All Git repositories under the scan roots that match the configured Git email or GitHub username are registered in a single commit and appear on the dashboard with a **New** badge. The scheduled scan that `workbench setup` installs is a macOS `launchd` job; on Linux, run `workbench discover --local` manually or from `cron`.
4. **(Optional) Enable Headless Background Agent Updates:**
   To have per-message updates run in a background Haiku call (spending zero tokens in the interactive coding session), add to `~/.workbench/config.toml`:
   ```toml
   update_mode = "headless"
   updater_command = 'claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'
   ```
   - The updater runs only after a message that changed files or made a commit; pure chat triggers nothing. It sends the last message, the reply, the project's entry and `git diff --stat` to `updater_command`.
   - The coding session is never interrupted, and automatic reviews after commits stop: run `/workbench-review` manually at milestones.
   - Ideas the assistant offered but the user did not agree to land in the project's **Inbox** on the dashboard (Keep / Dismiss) instead of the tree.
   - Results go to `~/.workbench/logs/updater.log` and `workbench history --kind update`. To preview what it would record, run `workbench auto-update --transcript <session.jsonl> --dry-run` inside the project's repo.
   - After an upgrade from an older version, reinstall the CLI (`uv tool install --force .`) so the hook runs the new code.

---

## Step 5: Troubleshooting & Maintenance

- **Inspecting recent metadata commits:**
  ```bash
  workbench history -n 15
  workbench history --project <project-id> --kind review,edit
  ```
- **Reverting an unwanted change:**
  ```bash
  workbench undo <commit-sha>
  ```
- **Archiving empty discovered repositories that should not appear on the Home grid:**
  ```bash
  workbench archive-empty --dry-run
  workbench archive-empty --keep <project-id-to-keep>
  ```
- **Checking why a server sync failed:**
  If an invalid file is ever pushed directly to `workbench-data`, the dashboard continues serving the last valid commit and displays an amber banner at the bottom of the screen. Click **Details** in the banner (or inspect `GET /api/status`) to see the exact commit SHA, file, and validation error.
