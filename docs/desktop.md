# Workbench Desktop App (macOS)

Workbench runs its dashboard in one of two ways:

| | Server mode | Desktop mode |
| :--- | :--- | :--- |
| **Where** | Docker on an always-on machine ([setup.md](setup.md)) | The laptop itself |
| **Reached from** | Every device on the tailnet | This computer only (`http://localhost:8420`) |
| **Runs** | `docker compose up -d` | The Workbench menu-bar app, started at login |

Both run the same `workbench serve`. The desktop app is a menu-bar icon that starts the bundled server in the background, keeps it running, and opens the dashboard in the default browser. It has no window of its own and no Dock icon.

The app is macOS-only for now. It is built from source and is not signed, so it is not yet a download on the Releases page.

---

## 1. Requirements

- macOS 12 or later on Apple silicon.
- Git (the Xcode command-line tools: `xcode-select --install`), and a Git login that can push to `workbench-data`, the same one the CLI uses.
- A configured `~/.workbench/config.toml` with `data_repo_url` (from `workbench setup`, see [setup.md §3](setup.md)). The server clones the data repo into `~/.workbench/server`, apart from the CLI's clone.

To build it: [uv](https://docs.astral.sh/uv/), Node.js 22+, and Rust (`brew install rust` or [rustup](https://rustup.rs)).

## 2. Build and Install

```bash
make desktop
```

This builds the web UI, freezes the CLI and server into one folder with PyInstaller (`desktop/build/workbench/`), and bundles the app around it. The results are:

- `desktop/src-tauri/target/release/bundle/macos/Workbench.app`
- `desktop/src-tauri/target/release/bundle/dmg/Workbench_<version>_aarch64.dmg`

Drag `Workbench.app` into `/Applications` (directly or from the `.dmg`) and open it. Because the app is unsigned, a copy downloaded from another machine is blocked by Gatekeeper: allow it under **System Settings → Privacy & Security → Open Anyway**. A copy built on the same Mac opens normally.

## 3. Using the App

On first launch the app turns on **Start at Login**, starts the server and opens the dashboard. At later logins it starts quietly in the menu bar. Opening the app again while it runs opens the dashboard.

| Menu item | What it does |
| :--- | :--- |
| *Status line* | `Starting…`, `Running on localhost:8420`, or `Stopped: <reason>` |
| **Open Dashboard** | Opens `http://localhost:8420` in the default browser |
| **Restart Server** | Stops and starts the bundled server (also retries after a failure) |
| **Start at Login** | Adds or removes the login item (`~/Library/LaunchAgents/Workbench.plist`) |
| **Install Command Line Tool…** | Links `~/.local/bin/workbench` to the binary inside the app (see §4) |
| **Show Server Log** | Opens `~/.workbench/logs/desktop-server.log` |
| **Quit Workbench** | Stops the server and quits |

A server that crashes is restarted after 2 seconds. One that fails three times within 30 seconds of starting stays stopped, with its last error in the status line, until **Restart Server** is chosen. If the app itself is force-quit or crashes, the server notices and stops within a few seconds, so it never holds the port on its own.

If another Workbench server already answers on port 8420 (for example `workbench serve` in a terminal), the app shows it as running and opens it, without starting a second one. Any other program on that port shows `Port 8420 is used by another program`.

## 4. The Command Line Tool

The coding-agent hooks run `workbench hook-check` after every turn, so the laptop needs `workbench` on the PATH. The app ships the same CLI as its server. **Install Command Line Tool…** links `~/.local/bin/workbench` to it, so the hooks, the terminal and the dashboard run one version.

If `~/.local/bin/workbench` already exists from another install (such as `uv tool install`), the app asks before replacing it. Keeping the other install also works; the two copies are then updated separately. After replacing a `uv` install, `uv tool uninstall workbench` removes the leftover.

## 5. Network Access

The server listens on `127.0.0.1` only, so no other machine can connect to it. It also rejects any request whose `Host` header is not `localhost` or `127.0.0.1`, so a web page that points its own domain at `127.0.0.1` (DNS rebinding) cannot read or edit the dashboard through the browser. `workbench serve` applies the same check whenever it listens on `127.0.0.1` or `localhost`; see `WORKBENCH_ALLOWED_HOSTS` in [reference.md](reference.md#4-server-environment-variables).

## 6. Troubleshooting

| Symptom | Fix |
| :--- | :--- |
| `Stopped: no data repo: …` | Run `workbench setup` (or set `data_repo_url` in `~/.workbench/config.toml`), then **Restart Server**. |
| `Stopped: …` with a Git or SSH error | Check that `git fetch` works in `~/.workbench/server/repo` from a terminal. The server uses the same SSH keys and agent as the terminal. |
| `Port 8420 is used by another program` | Stop that program (`lsof -nP -iTCP:8420 -sTCP:LISTEN` shows which one); the app starts the server once the port is free. |
| The privacy review cannot find `claude` | The app gives the server the login shell's `PATH`; make sure the tool is on the `PATH` in `~/.zprofile` (not only `~/.zshrc`). |

## 7. How It Is Built

| Part | Location |
| :--- | :--- |
| Menu-bar app (Tauri 2, Rust) | `desktop/src-tauri/` (`src/main.rs` menu, `src/server.rs` server supervision, `src/cli_link.rs`, `src/environment.rs`) |
| Frozen CLI + server | `packaging/pyinstaller/workbench.spec`: a folder rather than one file, so `workbench hook-check` starts in a fraction of a second |
| Build | `make desktop-binary` (frozen binary only) and `make desktop` (the app) |

Windows and Linux builds, signing, notarization and auto-update are not done yet.
