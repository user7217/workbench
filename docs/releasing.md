# Releasing Workbench

A release is a Git tag such as `v0.2.0`. Pushing the tag runs [`.github/workflows/release.yml`](../.github/workflows/release.yml), which builds every artifact and publishes one [GitHub Release](https://github.com/user7217/workbench/releases) with them.

| Artifact | For | Built on |
| :--- | :--- | :--- |
| `Workbench_<version>_aarch64.dmg` | Desktop mode on macOS (Apple silicon, unsigned) | macOS runner |
| `Workbench_<version>_aarch64.app.tar.gz`, `.sig`, `latest.json` | Self-update of installed apps: the signed app archive and the manifest apps read from `releases/latest/download/latest.json` | macOS runner |
| `ghcr.io/user7217/workbench:<version>`, `:<major>.<minor>`, `:latest` | Server mode (`linux/amd64`, `linux/arm64`) | Linux runner |
| `workbench_dashboard-<version>-py3-none-any.whl`, `workbench_dashboard-<version>.tar.gz` | `uv tool install`; the same files are published as [`workbench-dashboard`](https://pypi.org/project/workbench-dashboard/) on PyPI | Linux runner |
| `SHA256SUMS` | Checking downloads | — |
| Cask `workbench-dashboard` in [user7217/homebrew-workbench](https://github.com/user7217/homebrew-workbench) | `brew install --cask user7217/workbench/workbench-dashboard` | Linux runner, after the release |

The release notes are the version's section of [CHANGELOG.md](../CHANGELOG.md), followed by the Docker pull command and a note on opening the unsigned app.

---

## 1. Versions

Workbench follows [Semantic Versioning](https://semver.org). Before 1.0:

- **0.x.0** (minor) for new features, and for any change to the data format, the CLI or the config that needs action from users.
- **0.x.y** (patch) for fixes only.

A version with a suffix, such as `0.3.0-rc.1`, becomes a pre-release: it is marked as such on GitHub and does not move the `latest` image tag.

The version is written once, in `src/workbench/__init__.py`. `pyproject.toml` reads it from there, and `desktop/src-tauri/tauri.conf.json` reads `desktop/package.json`. [`scripts/version.py`](../scripts/version.py) keeps the remaining copies (`web/package.json`, `desktop/package.json`, `desktop/src-tauri/Cargo.toml` and their lock files) in step; CI fails if they differ.

```bash
uv run python scripts/version.py show     # every copy of the version
uv run python scripts/version.py check    # all agree; CHANGELOG.md has an Unreleased section
```

## 2. The Changelog

While working, each notable change gets a line under `## [Unreleased]` in `CHANGELOG.md`, in the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) groups: **Added**, **Changed**, **Deprecated**, **Removed**, **Fixed**, **Security**. Lines describe the effect for someone using Workbench, not the implementation. A change that needs action (a data format change, a renamed command or setting) says what to do.

Refactors, CI tweaks and documentation fixes do not need an entry.

## 3. Cutting a Release

On an up-to-date `main` with a clean working tree:

1. **Check the Unreleased section** of `CHANGELOG.md`: complete, and grouped as above.
2. **Set the version.** This writes it to every file, turns `## [Unreleased]` into `## [0.2.0] - <today>` with a new empty Unreleased section above it, updates the compare links, and refreshes `uv.lock`:
   ```bash
   uv run python scripts/version.py set 0.2.0
   ```
3. **Review and commit:**
   ```bash
   git diff
   git commit -am "chore(workbench): release 0.2.0"
   ```
4. **Tag and push** (the tag is what starts the release):
   ```bash
   git tag v0.2.0
   git push origin main v0.2.0
   ```
5. **Watch the Release workflow** in the repository's Actions tab. When it finishes, the release appears on the Releases page with all artifacts.

`set` refuses an empty Unreleased section and leaves every file untouched in that case. The workflow's first job runs `scripts/version.py check --tag <tag>`, so a tag that does not match the committed version, or a version without a changelog section, fails before anything is built or published.

## 4. If a Release Fails

- **Before the `GitHub Release` job:** nothing public exists except possibly the Docker image. Fix the problem on `main`, then move the tag to the fixed commit and push it again:
  ```bash
  git tag -f v0.2.0 && git push -f origin v0.2.0
  ```
- **After publishing:** do not reuse the version. Fix the problem and release the next patch version (`0.2.1`), with the fix under **Fixed**.

The first published image creates the `workbench` package on GHCR. The workflow links it to this repository, so it takes the repository's visibility: public here, so servers pull it without logging in. A package that shows as private can be made public under its page → Package settings → Change visibility.

## 5. Updater Signing Key

Installed apps only accept an update whose archive is signed with the updater key; its public half is in `desktop/src-tauri/tauri.conf.json` (`plugins.updater.pubkey`). The private key and its password are two repository secrets, used by the release workflow's macOS job:

| Secret | Value |
| :--- | :--- |
| `TAURI_SIGNING_PRIVATE_KEY` | The contents of the private key file (`~/.tauri/workbench-updater.key` on the maintainer's Mac) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Its password (`~/.tauri/workbench-updater.key.password`) |

They are set under the repository's **Settings → Secrets and variables → Actions → New repository secret**. Without them the macOS job stops with an error before building.

**Keep a backup of both files outside the laptop** (a password manager works). A lost key cannot be recovered: a new key means a new public key in the app, so every installed copy would have to be replaced by hand once. A leaked key lets anyone who can also publish a release on this repository push code to installed apps; if that happens, make a new key, ship it in a release and treat older installs as needing a manual update.

To make a new key pair (for a fork, say):

```bash
cd desktop && npx tauri signer generate -w ~/.tauri/workbench-updater.key
```

then put the new `.pub` contents in `tauri.conf.json` and update both secrets.

`make desktop` builds without the key and without update archives; `make desktop-release` (used by the workflow) needs both environment variables set.

## 6. PyPI

The `PyPI` job uploads the wheel and sdist after the GitHub Release exists, using [trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI accepts the workflow's short-lived OIDC token, so no API token is stored anywhere. PyPI's project page shows `README.md`, with its relative links and images rewritten to absolute GitHub URLs at build time (`tool.hatch.metadata.hooks.fancy-pypi-readme` in `pyproject.toml`).

The trusted publisher is set up once, on the PyPI account that owns the project (account settings → **Publishing** → add a **pending publisher** before the first upload, or the project's **Manage → Publishing** after it):

| Field | Value |
| :--- | :--- |
| PyPI project name | `workbench-dashboard` |
| Owner | `user7217` |
| Repository name | `workbench` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

A version on PyPI can never be uploaded again, even after deleting it. If the job fails (for example before the publisher was set up), fix the cause and use **Re-run failed jobs** on the same run; do not retag. Pre-releases (`0.3.0-rc.1`) are uploaded too; pip and uv skip them unless asked for one.

## 7. Homebrew Tap

The cask's source is [`packaging/homebrew/workbench-dashboard.rb`](../packaging/homebrew/workbench-dashboard.rb), with `@VERSION@` and `@SHA256@` placeholders. After each release (not pre-releases) the workflow's `Homebrew cask` job fills them in for the new dmg and pushes the result to the tap repository as `Casks/workbench-dashboard.rb`. Changes to the cask are made in this repository and reach the tap with the next release.

The job needs the `HOMEBREW_TAP_TOKEN` secret: a fine-grained personal access token limited to the `homebrew-workbench` repository, with **Contents: Read and write**. Without it the job only logs a warning. Fine-grained tokens expire (a year at most), so renew it before then; an expired token fails the job, and installs keep getting the previous version until it is renewed.

To check the cask locally:

```bash
brew tap user7217/workbench && brew audit --cask --strict --online user7217/workbench/workbench-dashboard
```

## 8. Not Yet Automated

These need accounts outside GitHub and are planned for later:

- **Apple signing and notarization** of the macOS app (an Apple Developer account), which removes the Gatekeeper prompt on first install. (Updates installed by the app itself do not show the prompt, since the app downloads them directly.)
- **Windows and Linux** builds of the desktop app.
