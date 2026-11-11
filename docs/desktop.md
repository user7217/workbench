# Workbench Desktop App (macOS)

Workbench runs its dashboard in one of two ways:

| | Server mode | Desktop mode |
| :--- | :--- | :--- |
| **Where** | Docker on an always-on machine ([setup.md](setup.md)) | The laptop itself |
| **Reached from** | Every device on the tailnet | This computer only (`http://localhost:8420`) |
| **Runs** | `docker compose up -d` | The Workbench menu-bar app, started at login |

Both run the same `workbench serve`. The desktop app is a menu-bar icon that starts the bundled server in the background, keeps it running, and opens the dashboard in the default browser. It has no window of its own and no Dock icon.

The app is macOS-only for now, for Apple silicon. Each [release](https://github.com/user7217/workbench/releases) (from 0.1.0) includes it as `Workbench_<version>_aarch64.dmg`. It is not signed with an Apple Developer ID yet, so macOS asks for confirmation on first open (§2).

---

## 1. Requirements

- macOS 12 or later on Apple silicon.
- Git (the Xcode command-line tools: `xcode-select --install`), and a Git login that can push to `workbench-data`, the same one the CLI uses.
- A private `workbench-data` repository ([setup.md Step 1](setup.md#step-1-create-the-private-workbench-data-repository)) and a `~/.workbench/config.toml` that names it. The app's own CLI writes the config on first run (§3); no separate install is needed. The server clones the data repo into `~/.workbench/server`, apart from the CLI's clone in `~/.workbench/data`.

To build it from source: [uv](https://docs.astral.sh/uv/), Node.js 22+, and Rust (`brew install rust` or [rustup](https://rustup.rs)).

## 2. Install

### With Homebrew

```bash
brew install --cask user7217/workbench/workbench-dashboard
```

This installs `Workbench.app` into `Applications`, clears the download's quarantine flag (so the first open needs no extra step) and links the `workbench` command to the copy inside the app, so **Install Command Line Tool…** is not needed. The app then updates itself (§4.1); `brew upgrade` leaves it alone unless run with `--greedy`. `brew uninstall --cask workbench-dashboard` also removes the login item; `--zap` additionally removes the app's settings, but never `~/.workbench`.

### From a Release

1. Download `Workbench_<version>_aarch64.dmg` from the [latest release](https://github.com/user7217/workbench/releases/latest) (`SHA256SUMS` beside it has its checksum).
2. Open the `.dmg` and drag `Workbench.app` onto `Applications`.
3. Clear the download's quarantine flag, then open Workbench from `Applications`:
   ```bash
   xattr -dr com.apple.quarantine /Applications/Workbench.app
   ```

The app is not signed by Apple yet, so with the flag still set macOS refuses to open it, usually with "Workbench.app is damaged and can't be opened. You should move it to the Trash." The file is not damaged: choose **Cancel** (not Move to Trash) and run the command above. On some macOS versions the message instead offers **System Settings → Privacy & Security → Open Anyway**, which works too. This is needed once; updates the app installs itself (§4.1) are not quarantined.

From 0.1.2 on, the app updates itself (§4.1). Versions before 0.1.2 are updated by hand once: quit Workbench from its menu, replace the app in `Applications` with the new one and open it. The login item keeps working because the path stays the same.

### From Source

```bash
make desktop
```

This builds the web UI, freezes the CLI and server into one folder with PyInstaller (`desktop/build/workbench/`), and bundles the app around it. The results are:

- `desktop/src-tauri/target/release/bundle/macos/Workbench.app`
- `desktop/src-tauri/target/release/bundle/dmg/Workbench_<version>_aarch64.dmg`

Copy `Workbench.app` into `/Applications` and open it from there (the first launch points the login item at wherever the app runs from). A copy built on the same Mac opens without the Gatekeeper prompt.

## 3. First Run

On first launch the app turns on **Start at Login**, starts the server and opens the dashboard. On a laptop that already ran `workbench setup`, that is all.

On a new laptop the server has no data repository yet, and the menu shows `Stopped: no data repo: …`. The app carries everything needed to fix that:

1. Choose **Install Command Line Tool…** in the menu (§5). It links `~/.local/bin/workbench`.
2. If `~/.local/bin` is not on the `PATH` yet, add it in `~/.zprofile` and open a new terminal:
   ```bash
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile
   ```
3. Run the setup wizard. It writes `~/.workbench/config.toml`, clones `workbench-data` and installs the coding-agent hooks ([setup.md Step 4](setup.md#step-4-set-up-the-laptop-cli--agent-hooks) describes its questions):
   ```bash
   workbench setup
   workbench discover --local
   ```
4. Choose **Restart Server**. The status line changes to `Running on localhost:8420`, and **Open Dashboard** shows the projects.

## 4. Using the App

At later logins the app starts quietly in the menu bar. Opening the app again while it runs opens the dashboard.

| Menu item | What it does |
| :--- | :--- |
| *Status line* | `Starting…`, `Running on localhost:8420`, or `Stopped: <reason>` |
| **Open Dashboard** | Opens `http://localhost:8420` in the default browser |
| **Restart Server** | Stops and starts the bundled server (also retries after a failure) |
| **Start at Login** | Adds or removes the login item (`~/Library/LaunchAgents/Workbench.plist`) |
| **Install Command Line Tool…** | Links `~/.local/bin/workbench` to the binary inside the app (see §5) |
| **Show Server Log** | Opens `~/.workbench/logs/desktop-server.log` |
| **Check for Updates…** | Checks for a new release now and installs it; becomes **Restart to Update to x.y.z** once one is installed (§4.1) |
| **Quit Workbench** | Stops the server and quits |

A server that crashes is restarted after 2 seconds. One that fails three times within 30 seconds of starting stays stopped, with its last error in the status line, until **Restart Server** is chosen. If the app itself is force-quit or crashes, the server notices and stops within a few seconds, so it never holds the port on its own.

If another Workbench server already answers on port 8420 (for example `workbench serve` in a terminal), the app shows it as running and opens it, without starting a second one. Any other program on that port shows `Port 8420 is used by another program`.

### 4.1 Updates

The app checks for a new release a minute after it starts and every 6 hours after that. It reads `latest.json` from the latest release on GitHub (pre-releases are never offered), downloads the new `Workbench.app`, checks its signature against the public key built into the app, and installs it in place of the old one. A download that is not signed with the release key is rejected.

The running app and its server stay on the old version until a restart: **Restart to Update to x.y.z** in the menu stops the server and relaunches the app (the dashboard is down for a few seconds), and otherwise the next login starts the new version. The command line tool links into the app, so it switches to the new version as soon as the update is installed.

Each check is logged to `~/.workbench/logs/desktop-updater.log`. The app has to be able to write to its own folder; in `/Applications` that is the case for an administrator account. Apps built from source (`make desktop`) check the same releases and update to them too; `tauri dev` builds never update.

## 5. The Command Line Tool

The coding-agent hooks run `workbench hook-check` after every turn, so the laptop needs `workbench` on the PATH. The app ships the same CLI as its server. **Install Command Line Tool…** links `~/.local/bin/workbench` to it, so the hooks, the terminal and the dashboard run one version.

If `~/.local/bin/workbench` already exists from another install (such as `uv tool install`), the app asks before replacing it. Keeping the other install also works; the two copies are then updated separately. After replacing a `uv` install, `uv tool uninstall workbench-dashboard` (or `uv tool uninstall workbench` for an install from before 0.1.3) removes the leftover.

## 6. Network Access

The server listens on `127.0.0.1` only, so no other machine can connect to it. It also rejects any request whose `Host` header is not `localhost` or `127.0.0.1`, so a web page that points its own domain at `127.0.0.1` (DNS rebinding) cannot read or edit the dashboard through the browser. `workbench serve` applies the same check whenever it listens on `127.0.0.1` or `localhost`; see `WORKBENCH_ALLOWED_HOSTS` in [reference.md](reference.md#4-server-environment-variables).

## 7. Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `Stopped: no data repo: …` | Follow §3: install the command line tool, run `workbench setup`, then **Restart Server**. |
| `Stopped: …` with a Git or SSH error | Check that `git fetch` works in `~/.workbench/server/repo` from a terminal. The server uses the same SSH keys and agent as the terminal. |
| `Port 8420 is used by another program` | Stop that program (`lsof -nP -iTCP:8420 -sTCP:LISTEN` shows which one); the app starts the server once the port is free. |
| An update never arrives | `~/.workbench/logs/desktop-updater.log` has the reason. **Check for Updates…** shows it in a dialog. Apps before 0.1.2 cannot update themselves; install the latest `.dmg` once. |
| The privacy review cannot find `claude` | The app gives the server the login shell's `PATH`; make sure the tool is on the `PATH` in `~/.zprofile` (not only `~/.zshrc`). |

## 8. How It Is Built

| Part | Location |
| :--- | :--- |
| Menu-bar app (Tauri 2, Rust) | `desktop/src-tauri/` (`src/main.rs` menu, `src/server.rs` server supervision, `src/cli_link.rs`, `src/environment.rs`) |
| Frozen CLI + server | `packaging/pyinstaller/workbench.spec`: a folder rather than one file, so `workbench hook-check` starts in a fraction of a second |
| Build | `make desktop-binary` (frozen binary only) and `make desktop` (the app) |

The release workflow (`.github/workflows/release.yml`) runs `make desktop-release` on a macOS runner for every version tag; see [releasing.md](releasing.md). Updates use [tauri-plugin-updater](https://v2.tauri.app/plugin/updater/) (`src/updater.rs`); the release workflow signs the update archive, as described in [releasing.md](releasing.md#5-updater-signing-key). Windows and Linux builds, Apple signing and notarization are not done yet.
