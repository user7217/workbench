"""GitHub REST API discovery via httpx (§10.3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from workbench.discovery.register import DiscoveredRepoCandidate, register_discovered_repos

LINK_REL_RE = re.compile(r'<([^>]+)>\s*;\s*rel="([^"]+)"')


@dataclass
class GitHubDiscoveryResult:
    registered_ids: list[str] = field(default_factory=list)
    rate_limited: bool = False
    retry_after: int | None = None
    errors: list[str] = field(default_factory=list)


def parse_link_header(link_header: str | None) -> dict[str, str]:
    """Parse HTTP Link header into {rel: url}."""
    if not link_header:
        return {}
    links: dict[str, str] = {}
    for match in LINK_REL_RE.finditer(link_header):
        url, rel = match.group(1), match.group(2)
        links[rel] = url
    return links


def _is_rate_limited(resp: httpx.Response) -> tuple[bool, int | None]:
    if resp.status_code in (403, 429):
        retry_after = resp.headers.get("Retry-After")
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if resp.status_code == 429 or remaining == "0" or retry_after is not None:
            ra_int = int(retry_after) if (retry_after and retry_after.isdigit()) else None
            return True, ra_int
    return False, None


def _listing_urls(base: str, github_owners: list[str], token: str | None) -> list[tuple[str, str]]:
    """(label, first page URL) of the repo listings to read.

    `/users/<owner>/repos` returns only public repos even with a token, so with a token the
    authenticated listing is used instead (it includes private repos) and filtered by owner.
    """
    if token:
        url = f"{base}/user/repos?affiliation=owner,organization_member&per_page=100"
        return [(", ".join(github_owners), url)]
    return [(owner, f"{base}/users/{owner}/repos?per_page=100") for owner in github_owners]


def _repo_owner(repo: dict, full_name: str) -> str:
    owner = repo.get("owner")
    login = owner.get("login") if isinstance(owner, dict) else None
    return str(login or full_name.split("/", 1)[0]).lower()


def fetch_github_candidates(
    github_owners: list[str],
    token: str | None = None,
    api_base: str = "https://api.github.com",
    client: httpx.Client | None = None,
) -> tuple[list[DiscoveredRepoCandidate], GitHubDiscoveryResult]:
    """Query GitHub REST API for non-fork, non-archived repos and their root commits (§10.3)."""
    result = GitHubDiscoveryResult()
    candidates: list[DiscoveredRepoCandidate] = []
    base = api_base.rstrip("/")
    wanted_owners = {owner.lower() for owner in github_owners}

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "workbench-discovery/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    own_client = client is None
    http = client or httpx.Client(headers=headers, timeout=15.0)

    try:
        if not github_owners:
            return candidates, result
        for owner, first_url in _listing_urls(base, github_owners, token):
            next_url: str | None = first_url
            while next_url:
                resp = http.get(next_url, headers=headers)
                limited, ra = _is_rate_limited(resp)
                if limited:
                    result.rate_limited = True
                    result.retry_after = ra
                    return candidates, result
                if resp.status_code != 200:
                    result.errors.append(
                        f"GitHub API error listing repos for {owner}: HTTP {resp.status_code}"
                    )
                    break

                repos_page = resp.json()
                if not isinstance(repos_page, list):
                    break

                for repo in repos_page:
                    if not isinstance(repo, dict):
                        continue
                    # Skip forks and archived repos by default (§10.3)
                    if repo.get("fork") or repo.get("archived"):
                        continue
                    repo_name = str(repo.get("name", "")).strip()
                    full_name = str(repo.get("full_name") or "").strip()
                    description = str(repo.get("description") or "")
                    if not repo_name or not full_name:
                        continue
                    if _repo_owner(repo, full_name) not in wanted_owners:
                        # The authenticated listing also has repos owned by others
                        continue

                    # Fetch root commit via commits?per_page=1 and follow rel="last" link
                    commits_url = f"{base}/repos/{full_name}/commits?per_page=1"
                    c_resp = http.get(commits_url, headers=headers)
                    limited, ra = _is_rate_limited(c_resp)
                    if limited:
                        result.rate_limited = True
                        result.retry_after = ra
                        return candidates, result
                    if c_resp.status_code == 409:
                        # Empty repository (no commits)
                        continue
                    if c_resp.status_code != 200:
                        result.errors.append(
                            f"GitHub API error fetching commits for {full_name}: HTTP {c_resp.status_code}"
                        )
                        continue

                    c_links = parse_link_header(c_resp.headers.get("Link"))
                    if "last" in c_links:
                        last_resp = http.get(c_links["last"], headers=headers)
                        limited, ra = _is_rate_limited(last_resp)
                        if limited:
                            result.rate_limited = True
                            result.retry_after = ra
                            return candidates, result
                        if last_resp.status_code != 200:
                            continue
                        commits_data = last_resp.json()
                    else:
                        commits_data = c_resp.json()

                    if isinstance(commits_data, list) and commits_data:
                        root_sha = str(commits_data[-1].get("sha", "")).strip()
                        if root_sha:
                            candidates.append(
                                DiscoveredRepoCandidate(
                                    name=repo_name,
                                    root_commit=root_sha,
                                    github=full_name,
                                    description=description,
                                )
                            )

                page_links = parse_link_header(resp.headers.get("Link"))
                next_url = page_links.get("next")
    finally:
        if own_client:
            http.close()

    return candidates, result


def run_github_discovery(
    data_repo_dir: Path,
    github_owners: list[str],
    token: str | None = None,
    branch: str = "main",
    api_base: str = "https://api.github.com",
    work_root: Path | None = None,
    env: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> GitHubDiscoveryResult:
    """Run GitHub discovery and register unknown repositories in a single commit (§10.3)."""
    candidates, res = fetch_github_candidates(
        github_owners=github_owners,
        token=token,
        api_base=api_base,
        client=client,
    )
    if candidates and not res.rate_limited:
        registered = register_discovered_repos(
            data_repo_dir=data_repo_dir,
            candidates=candidates,
            branch=branch,
            work_root=work_root,
            env=env,
            commit_source="GitHub discovery",
        )
        res.registered_ids = registered
    return res
