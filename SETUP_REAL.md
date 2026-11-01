# Workbench — Production & Home Server Deployment Guide

This guide walks through setting up Workbench in production: creating your private `workbench-data` repository on GitHub, deploying the dashboard server on an always-on home server (via **Docker Compose + Tailscale** or standalone **`workbench serve`**), and connecting your laptop's CLI and coding agents.

> **How data flows in production:** Your source code never leaves your laptop. Your laptop CLI and coding agents push small, validated `.yaml` and `.md` metadata files to your private `workbench-data` repository on GitHub. Your home server polls `workbench-data` to serve the dashboard over Tailscale and pushes back any edits you make in the web UI.

---

## Architecture & Credential Overview

Workbench uses three narrowly scoped credentials so no machine has more access than it needs:

| Machine | Credential | Scope & Purpose |
| :--- | :--- | :--- |
| **1. Your Laptop** | Your normal Git SSH key (`~/.ssh/...`) or credential helper | Pulls and pushes metadata commits from the `workbench` CLI to `<you>/workbench-data`. |
| **2. Home Server (Git Push/Pull)** | Dedicated SSH Deploy Key (`./secrets/deploy_key`) | Registered **only** on `<you>/workbench-data` with **Allow write access** enabled. Allows the server to fetch updates and commit inline web UI edits. |
| **3. Home Server (Optional Discovery)** | Fine-grained GitHub Personal Access Token (`WORKBENCH_GITHUB_READ_TOKEN`) | **Read-only** access (`Metadata: Read-only` and `Contents: Read-only`) across your repositories so the server's daily background job can discover new non-fork, non-archived GitHub repos. |

---

## Step 1: Create Your Private `workbench-data` Repository

1. On GitHub, create a new **private** repository named **`workbench-data`** (leave *Add a README* and *.gitignore* unchecked).
2. On your laptop, initialize the repository with the root `workbench.yaml` marker file and push it to `main`:

```bash
mkdir -p /tmp/init-workbench-data && cd /tmp/init-workbench-data
git init -b main
cat << 'EOF' > workbench.yaml
schema_version: 1
EOF
mkdir -p projects learning
git add workbench.yaml
git commit -m "Initialize workbench-data (schema_version 1)"
git remote add origin git@github.com:<YOUR_GITHUB_USER>/workbench-data.git
git push -u origin main
cd ~ && rm -rf /tmp/init-workbench-data
```

---

## Step 2: Deploy the Server (Option A: Docker Compose + Tailscale — Recommended)

Running the server in Docker on a home server, NAS, Raspberry Pi, or VPS keeps the dashboard reachable across your devices over [Tailscale](https://tailscale.com) without exposing any ports to the public internet.

### 2.1 Find Your Server's Tailscale IP

SSH into your server, ensure Tailscale is connected, and note the server's `100.x.y.z` Tailscale IPv4 address:

```bash
tailscale ip -4
# Example output: 100.101.102.103
```

### 2.2 Clone the Workbench Application Repo on the Server

```bash
git clone https://github.com/<YOUR_GITHUB_USER>/workbench.git ~/workbench
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

Register that public key on your **`workbench-data`** repository:
1. Open `https://github.com/<YOUR_GITHUB_USER>/workbench-data/settings/keys`.
2. Click **Add deploy key**.
3. Title it `workbench-server`, paste the output of `cat secrets/deploy_key.pub`, check **Allow write access**, and click **Add key**.

> **Important:** Always create `./secrets/deploy_key` *before* running `docker compose up`. If the file does not exist on the host, Docker will create an empty directory at `./secrets/deploy_key` instead of mounting a file.

### 2.4 (Optional) Create a Read-Only GitHub Token for Daily Repo Discovery

If you want the server to automatically discover new GitHub repositories that aren't cloned on your laptop:
1. Go to GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set **Resource owner** to `<YOUR_GITHUB_USER>` and **Repository access** to **All repositories**.
3. Under **Repository permissions**, grant:
   - **Contents:** `Read-only` (used to read the oldest commit SHA of each repo)
   - **Metadata:** `Read-only`
4. Generate the token and copy the `github_pat_...` string.

### 2.5 Create the `.env` Configuration File on the Server

Create `.env` in `~/workbench` on the server, setting `WORKBENCH_BIND` to the Tailscale IP from Step 2.1:

```bash
cat > .env << 'EOF'
WORKBENCH_DATA_REMOTE=git@github.com:<YOUR_GITHUB_USER>/workbench-data.git
WORKBENCH_BRANCH=main
WORKBENCH_POLL_SECONDS=600
WORKBENCH_BIND=100.101.102.103
WORKBENCH_GITHUB_OWNERS=<YOUR_GITHUB_USER>
WORKBENCH_GITHUB_READ_TOKEN=
EOF
chmod 600 .env
```

*(If you created a discovery token in Step 2.4, paste it after `WORKBENCH_GITHUB_READ_TOKEN=`.)*

### 2.6 Build, Start, and Verify the Container

```bash
docker compose up -d --build
docker compose logs -f workbench
```

What happens inside the container (`Dockerfile` + `docker-compose.yml`):
- Stage 1 (`node:22-slim`) compiles the React/TypeScript frontend (`web/dist`).
- Stage 2 (`python:3.12-slim`) installs the Python package via `uv`, pre-populates `/etc/ssh/ssh_known_hosts` with GitHub's official SSH host keys (`docker/github_known_hosts`), mounts `./secrets/deploy_key` at `/root/.ssh/id_ed25519`, clones `workbench-data` into the persistent `workbench-data` Docker volume (`/data`), and starts `uvicorn` on port `8420`.

Verify from your laptop browser (connected to the same Tailscale tailnet):
- Open `http://100.101.102.103:8420/api/status` — confirm `"last_error": null` and `"last_good_commit"` is populated.
- Open `http://100.101.102.103:8420` — confirm the Workbench UI loads.

To update the server after pulling new code changes in the future:

```bash
cd ~/workbench && git pull && docker compose up -d --build
```

---

## Step 3: Deploy the Server (Option B: Without Docker via `workbench serve`)

If you prefer running the server directly on macOS or Linux without Docker:

1. **Build the frontend bundle and install the CLI tool:**
   ```bash
   cd /path/to/workbench
   (cd web && npm ci && npm run build)
   uv tool install --force .
   ```
2. **Start the server bound to `127.0.0.1` (local Mac) or your Tailscale IP:**
   ```bash
   export WORKBENCH_DATA_REMOTE="git@github.com:<YOUR_GITHUB_USER>/workbench-data.git"
   export WORKBENCH_GITHUB_OWNERS="<YOUR_GITHUB_USER>"
   export WORKBENCH_POLL_SECONDS="600"

   workbench serve --host 127.0.0.1 --port 8420
   ```
   `workbench serve` reads `data_repo_url` from `~/.workbench/config.toml` if `WORKBENCH_DATA_REMOTE` is not set, maintains its own clone in `~/.workbench/server`, and serves the built UI alongside the JSON API.

---

## Step 4: Set Up Your Laptop CLI & Agent Hooks

On the Mac or Linux machine where you write code:

1. **Install the `workbench` CLI globally:**
   ```bash
   cd /path/to/workbench
   uv tool install --force .
   ```
2. **Run the interactive setup wizard:**
   ```bash
   workbench setup
   ```
   The wizard prompts you for:
   - **Workbench data repository URL:** `git@github.com:<YOUR_GITHUB_USER>/workbench-data.git` (verified immediately with `git ls-remote`).
   - **GitHub owner username(s):** `<YOUR_GITHUB_USER>`
   - **Git author email(s):** Your commit email(s) (`git config user.email`) so local discovery only registers repositories you authored.
   - **Local scan roots:** Directories where your code lives (e.g., `~/Documents, ~/code, ~/projects`).

   It then clones `workbench-data` to `~/.workbench/data`, previews the exact hook and skill files it will write to `~/.claude`, `~/.gemini`, and `~/Library/LaunchAgents/com.workbench.discover.plist`, and asks for confirmation (`yes`).
3. **Run your first local discovery scan:**
   ```bash
   workbench discover --local
   ```
   All Git repositories under your scan roots that match your Git email or GitHub username are registered in a single commit and appear on your dashboard with a **New** badge.
4. **(Optional) Enable Headless Background Agent Updates:**
   To have per-message updates run asynchronously in a background Haiku call (spending zero tokens in your interactive coding session), add to `~/.workbench/config.toml`:
   ```toml
   update_mode = "headless"
   updater_command = 'claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'
   ```

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
- **Archiving empty discovered repositories you don't want on the Home grid:**
  ```bash
  workbench archive-empty --dry-run
  workbench archive-empty --keep <project-id-to-keep>
  ```
- **Checking why a server sync failed:**
  If an invalid file is ever pushed directly to `workbench-data`, the dashboard continues serving the last valid commit and displays an amber banner at the bottom of the screen. Click **Details** in the banner (or inspect `GET /api/status`) to see the exact commit SHA, file, and validation error.
