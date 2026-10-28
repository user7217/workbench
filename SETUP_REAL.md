# Workbench — Real Environment Setup Guide (`SETUP_REAL.md`)

During development and testing, Workbench uses only isolated temporary directories, local bare git repositories, and `demo-*` fixtures. Follow the copy-paste steps below when you are ready to connect Workbench to your real private `workbench-data` repository, laptop environment, and home server.

> **Note on fresh repositories:** A real `workbench-data` repository starts **empty** (with only `workbench.yaml`). None of the `demo-*` projects or learning paths exist in your real repository. If you ever import demo items for testing, they all share the `demo-` prefix and can be removed by deleting `projects/demo-*` and `learning/demo-*`.

---

## 1. Create the Private `workbench-data` Repository

Create a new **private** repository named `workbench-data` on GitHub (do not initialize it with a README or `.gitignore`), then seed it with `workbench.yaml` only:

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

## 2. Create Credentials

Workbench uses three minimal, scoped credentials:

### 2.1 Laptop (existing Git auth)
Your laptop uses your existing SSH key or Git credential helper to pull and push to `git@github.com:<YOUR_GITHUB_USER>/workbench-data.git`.

### 2.2 Home Server Push Credential (SSH Deploy Key)
Generate a dedicated SSH keypair for the home server and register it as a **Deploy Key** on `<YOUR_GITHUB_USER>/workbench-data` ONLY (with **Allow write access** enabled):

```bash
ssh-keygen -t ed25519 -C "workbench-server-deploy-key" -f ~/.ssh/workbench_data_deploy_key -N ""
cat ~/.ssh/workbench_data_deploy_key.pub
```

1. Open `https://github.com/<YOUR_GITHUB_USER>/workbench-data/settings/keys`.
2. Click **Add deploy key**, paste the `.pub` output, check **Allow write access**, and save.

### 2.3 Server GitHub Discovery Token (Fine-Grained Read-Only PAT)
1. Open GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
2. Click **Generate new token**:
   - **Resource owner:** `<YOUR_GITHUB_USER>`
   - **Repository access:** All repositories
   - **Permissions → Repository permissions → Metadata:** `Read-only` (and **Contents:** `Read-only` so root commits can be inspected). No write permissions.
3. Copy the generated `github_pat_...` token for the server's `WORKBENCH_GITHUB_READ_TOKEN`.

---

## 3. Deploy the Home Server (Tailscale + Docker Compose)

1. Install and authenticate [Tailscale](https://tailscale.com) on your home server and find its Tailscale IPv4 address:

```bash
tailscale ip -4
# Example output: 100.101.102.103
```

2. Clone `workbench` onto your home server, copy your deploy key into `./secrets/id_ed25519`, and configure environment variables so the server binds **only** to the Tailscale IP:

```bash
export WORKBENCH_DATA_REMOTE="git@github.com:<YOUR_GITHUB_USER>/workbench-data.git"
export WORKBENCH_BRANCH="main"
export WORKBENCH_POLL_SECONDS="600"
export WORKBENCH_GITHUB_OWNERS="<YOUR_GITHUB_USER>"
export WORKBENCH_GITHUB_READ_TOKEN="github_pat_..."
export WORKBENCH_BIND="100.101.102.103"
export WORKBENCH_PORT="8420"

docker compose up -d --build
```

Verify the server is reachable over Tailscale at `http://100.101.102.103:8420/api/status`.

---

## 4. Laptop Setup & Initial Discovery

1. Install the `workbench` CLI tool globally on your laptop via `uv`:

```bash
uv tool install .
```

2. Run the interactive go-live wizard (`workbench setup`), which verifies access to `workbench-data` with `git ls-remote`, writes `~/.workbench/config.toml`, clones the data repo to `~/.workbench/data`, shows you the exact files it will touch in `~/.claude`, `~/.gemini`, and `~/Library/LaunchAgents`, and waits for you to type `yes`:

```bash
workbench setup
```

3. Run local discovery once to register your existing laptop repositories (only repositories owned by your configured GitHub user or containing commits authored by your configured email will be registered; third-party clones are skipped):

```bash
workbench discover --local
```

Server-side GitHub discovery runs automatically once a day (and on server startup if configured) to register any GitHub repositories not cloned on your laptop.

---

## 5. Set the Dashboard as Your Browser Start Page

Set your browser's homepage / new-tab URL to your Tailscale server address:

```text
http://100.101.102.103:8420
```

Because every project and learning path appears on the Home screen grouped by status (and ordered alphabetically within each group), opening a browser window gives you an immediate, calm inventory of everything you are building and learning.

---

## 6. Undoing a Bad Change (`workbench undo`)

Every metadata update is a plain commit on `main` in `workbench-data`. To inspect recent changes and revert a bad commit:

```bash
# View recent commits across the data repo (or pass a project/path ID to filter)
workbench history
workbench history <project-id>

# Revert a specific commit SHA and push the revert to origin/main
workbench undo <commit-sha>
```
