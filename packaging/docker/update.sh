#!/usr/bin/env bash
# Moves a Docker Compose server to the latest Workbench release, and does nothing otherwise.
#
# "Latest release" is GitHub's latest published, non-pre-release Release, not the newest commit
# or tag: a tag whose release workflow has not finished (or failed) is never deployed.
# For that release it:
#   1. pulls ghcr.io/<owner>/workbench:<version> (on failure nothing changes; the next run retries)
#   2. checks out the release tag in this clone, so docker-compose.yml matches the image
#   3. recreates the container with that image (state lives in the volume, so it survives)
#
# Run from cron or the systemd timer in this folder (see docs/setup.md, "Automatic Updates").
# WORKBENCH_VERSION in .env pins a version and turns this off. WORKBENCH_REPO=owner/name
# overrides the repository read from the clone's origin remote.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

log() { printf '%s workbench-update: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

if [ -f .env ] && grep -Eq '^[[:space:]]*WORKBENCH_VERSION=[^[:space:]]' .env; then
    log "WORKBENCH_VERSION is pinned in .env; not updating"
    exit 0
fi

repo="${WORKBENCH_REPO:-$(git remote get-url origin | sed -E 's#^(git@github\.com:|ssh://git@github\.com/|https://github\.com/)##; s#\.git$##')}"

release_json="$(curl -fsS --max-time 30 -H 'Accept: application/vnd.github+json' \
    "https://api.github.com/repos/${repo}/releases/latest")" || {
    log "could not read the latest release of ${repo}; will retry next run"
    exit 1
}
tag="$(printf '%s' "$release_json" | sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1)"
case "$tag" in
    v[0-9]*) ;;
    *) log "no release tag found for ${repo}"; exit 1 ;;
esac
version="${tag#v}"
export WORKBENCH_VERSION="$version"

image="$(docker compose config --images | head -n 1)"
running="$(docker inspect -f '{{.Config.Image}}' workbench-server 2>/dev/null || true)"
if [ "$running" = "$image" ] && [ "$(git describe --tags --exact-match 2>/dev/null || true)" = "$tag" ]; then
    exit 0  # already on the latest release
fi

log "updating to ${tag} (${image}); was ${running:-not running}"
docker compose pull --quiet workbench || {
    log "pulling ${image} failed; will retry next run"
    exit 1
}
git fetch --quiet --tags origin
git -c advice.detachedHead=false checkout --quiet "$tag" || {
    log "could not check out ${tag} (local edits to tracked files?); the server was not changed"
    exit 1
}
docker compose up -d --no-build --remove-orphans
# Remove older release images that no container uses (only images built by the release workflow
# carry this label; locally built ones are left alone)
docker image prune -af --filter "label=org.opencontainers.image.source=https://github.com/${repo}" >/dev/null || true
log "running ${tag}"
