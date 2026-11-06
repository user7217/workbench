# Releasing Workbench

A release is a Git tag such as `v0.2.0`. Pushing the tag runs [`.github/workflows/release.yml`](../.github/workflows/release.yml), which builds every artifact and publishes one [GitHub Release](https://github.com/user7217/workbench/releases) with them.

| Artifact | For | Built on |
| :--- | :--- | :--- |
| `Workbench_<version>_aarch64.dmg` | Desktop mode on macOS (Apple silicon, unsigned) | macOS runner |
| `ghcr.io/user7217/workbench:<version>`, `:<major>.<minor>`, `:latest` | Server mode (`linux/amd64`, `linux/arm64`) | Linux runner |
| `workbench-<version>-py3-none-any.whl`, `workbench-<version>.tar.gz` | `uv tool install` from the release | Linux runner |
| `SHA256SUMS` | Checking downloads | — |

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

The first published image creates a private `workbench` package on GHCR. Set it to public once under GitHub → Packages → `workbench` → Package settings, so servers can pull it without logging in.

## 5. Not Yet Automated

These need accounts outside GitHub and are planned for later:

- **PyPI:** the name `workbench` is taken there, so publishing needs another distribution name; the command would stay `workbench`.
- **Signing and notarization** of the macOS app (an Apple Developer account), which removes the Gatekeeper prompt.
- **Windows and Linux** builds of the desktop app.
