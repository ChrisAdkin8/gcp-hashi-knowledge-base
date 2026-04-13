#!/usr/bin/env python3
"""Fetch GitHub issues from HashiCorp repositories for RAG ingestion.

Queries the GitHub REST API for issues updated in the last 365 days. Repos are
split into two tiers:

  REPOS_PRIORITY — 8 high-signal repos always fetched (core products + major
                   providers). These generate the bulk of useful RAG content
                   and fit comfortably within the 60 req/hr unauthenticated limit.

  REPOS_EXTENDED — 15 lower-volume repos fetched only when GITHUB_TOKEN is set
                   (5000 req/hr). Utility providers (null, random, tls, etc.)
                   produce very little issue content that is relevant for RAG.

Quality filters applied to every issue:
  - Pull requests excluded (GitHub returns PRs in the issues endpoint).
  - Body length < 100 characters excluded (too short to be useful).
  - Issues with 0 comments excluded when unauthenticated; comment count >= 2
    required when authenticated (commented issues contain problem + resolution,
    which is the highest-value RAG content).

Comment fetching:
  - Unauthenticated: skipped entirely (preserves quota for issue listing).
  - Authenticated: up to MAX_COMMENTS comments per issue.

Rate limit handling:
  - Unauthenticated: fails fast on 403 rate-limit response (does not wait for
    reset — a 1-hour wait would exceed the Cloud Build step timeout). Partial
    data from completed repos is still uploaded.
  - Authenticated: waits for rate-limit reset as normal.

Set GITHUB_TOKEN via Secret Manager for best results. See CLAUDE.md for setup.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
OUTPUT_ROOT = Path("/workspace/cleaned/issues")
LOOKBACK_DAYS = 365
MAX_COMMENTS = 10
PER_PAGE = 100
MAX_RETRIES = 3
MIN_BODY_LENGTH = 100

# Labels that indicate an issue has no useful resolution content.
# Issues with ONLY these labels are skipped. Issues with at least one
# non-denied label are kept (the useful label wins).
_LABEL_DENYLIST = {"stale", "wontfix", "won't fix", "duplicate", "invalid", "spam"}

# GitHub usernames and org patterns for HashiCorp maintainers.
# Used to detect official responses in issue comments.
_MAINTAINER_PATTERNS = {"hashicorp", "hashicorp-"}

# Page limits per repo.
# Unauthenticated (60 req/hr): 1 page = 100 issues max per repo.
# Authenticated (5000 req/hr): up to 5 pages = 500 issues max per repo.
_MAX_PAGES_UNAUTH = 1
_MAX_PAGES_AUTH = 5

# Minimum comment count filter.
# Without a token, skip comment fetching entirely and require >= 1 comment
# so that unresolved one-liners are excluded without spending quota on comment
# requests. With a token, require >= 2 comments (problem + at least one
# response, ideally a resolution).
_MIN_COMMENTS_UNAUTH = 1
_MIN_COMMENTS_AUTH = 2

# ── Repo tiers ────────────────────────────────────────────────────────────────
#
# PRIORITY: always fetched. Core products and high-traffic providers generate
# the bulk of useful RAG content. 8 repos × 1 page = 8 API calls, well within
# the 60 req/hr unauthenticated limit.
#
# EXTENDED: fetched only with GITHUB_TOKEN. Utility providers have low issue
# volume and low RAG relevance. Adding them without a token would exhaust quota
# before reaching the priority repos.

REPOS_PRIORITY: dict[str, str] = {
    "terraform": "terraform",
    "vault": "vault",
    "consul": "consul",
    "nomad": "nomad",
    "terraform-provider-aws": "aws",
    "terraform-provider-azurerm": "azurerm",
    "terraform-provider-google": "google",
    "terraform-provider-kubernetes": "kubernetes",
}

REPOS_EXTENDED: dict[str, str] = {
    "packer": "packer",
    "boundary": "boundary",
    "waypoint": "waypoint",
    "terraform-provider-helm": "helm",
    "terraform-provider-docker": "docker",
    "terraform-provider-vault": "vault",
    "terraform-provider-consul": "consul",
    "terraform-provider-nomad": "nomad",
    "terraform-provider-random": "random",
    "terraform-provider-null": "null",
    "terraform-provider-local": "local",
    "terraform-provider-tls": "tls",
    "terraform-provider-http": "http",
    "terraform-sentinel-policies": "sentinel",
}


def _get_session() -> requests.Session:
    """Create a requests session with optional GitHub token auth."""
    session = requests.Session()
    session.headers["Accept"] = "application/vnd.github+json"
    session.headers["X-GitHub-Api-Version"] = "2022-11-28"
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
        logger.info("Using GITHUB_TOKEN for authenticated API access.")
    else:
        logger.warning(
            "No GITHUB_TOKEN set — using unauthenticated access (60 req/hr limit). "
            "Only priority repos will be fetched. Set GITHUB_TOKEN via Secret Manager "
            "to fetch all %d repos.",
            len(REPOS_PRIORITY) + len(REPOS_EXTENDED),
        )
    return session


def _get_with_retry(session: requests.Session, url: str, params: dict | None = None) -> dict | list:
    """GET a URL with retry and rate-limit handling.

    Args:
        session: Requests session.
        url: API URL.
        params: Query parameters.

    Returns:
        Parsed JSON response.

    Raises:
        requests.HTTPError: On rate limit when unauthenticated (fail fast).
        requests.RequestException: After MAX_RETRIES failures.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, timeout=30)

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                if not os.environ.get("GITHUB_TOKEN", ""):
                    logger.warning(
                        "Rate limited without GITHUB_TOKEN — stopping early. "
                        "Partial data from completed repos will be uploaded."
                    )
                    raise requests.HTTPError("Rate limited (unauthenticated)", response=resp)
                reset = int(resp.headers.get("X-RateLimit-Reset", 0))
                wait = max(reset - int(time.time()), 10)
                logger.warning("Rate limited. Waiting %ds for reset.", wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            backoff = 2 ** (attempt - 1)
            logger.warning("Request failed (attempt %d/%d), retrying in %ds: %s", attempt, MAX_RETRIES, backoff, exc)
            time.sleep(backoff)
    raise RuntimeError("Unexpected exit from retry loop")


def _is_pull_request(issue: dict) -> bool:
    """Check if a GitHub 'issue' is actually a pull request."""
    return "pull_request" in issue


def _is_useful(issue: dict, min_comments: int) -> bool:
    """Return True if an issue passes quality filters.

    Filters:
      - Not a pull request.
      - Body is at least MIN_BODY_LENGTH characters.
      - Has at least min_comments comments (ensures problem + response present).
      - Not labelled exclusively with low-signal labels (stale, wontfix, etc.).

    Args:
        issue: GitHub issue JSON object.
        min_comments: Minimum comment count threshold.

    Returns:
        True if the issue should be written to disk.
    """
    if _is_pull_request(issue):
        return False
    body = issue.get("body") or ""
    if len(body.strip()) < MIN_BODY_LENGTH:
        return False
    if issue.get("comments", 0) < min_comments:
        return False
    labels = {label["name"].lower() for label in issue.get("labels", [])}
    if labels and labels.issubset(_LABEL_DENYLIST):
        return False
    return True


def _has_maintainer_response(comments: list[dict]) -> bool:
    """Check if any comment is from a likely HashiCorp maintainer.

    Checks for usernames containing 'hashicorp' and for users
    with the 'MEMBER' or 'COLLABORATOR' association.

    Args:
        comments: List of comment JSON objects.

    Returns:
        True if a maintainer response was found.
    """
    for comment in comments:
        author = (comment.get("user", {}).get("login") or "").lower()
        assoc = (comment.get("author_association") or "").upper()
        if any(p in author for p in _MAINTAINER_PATTERNS):
            return True
        if assoc in ("MEMBER", "COLLABORATOR", "OWNER"):
            return True
    return False


def _get_product_family(repo_name: str, product: str) -> str:
    """Derive the product family from a repository name.

    All provider and sentinel repos belong to the ``terraform`` family.
    Core product repos use the product name as the family.

    Args:
        repo_name: GitHub repository name.
        product: Product short name.

    Returns:
        Product family string.
    """
    if "provider" in repo_name or "sentinel" in repo_name:
        return "terraform"
    return product


def format_issue(issue: dict, comments: list[dict], product: str, repo_name: str) -> str:
    """Format a GitHub issue and its comments as markdown with metadata header.

    Args:
        issue: GitHub issue JSON object.
        comments: List of comment JSON objects.
        product: Product name for metadata.
        repo_name: Repository name for metadata.

    Returns:
        Formatted markdown string.
    """
    title = issue.get("title", "Untitled")
    number = issue["number"]
    state = issue.get("state", "unknown")
    body = issue.get("body") or ""

    header = f"[issue:{product}] #{number} ({state}): {title}\n\n"

    content = f"# {title}\n\n{body}\n"

    if comments:
        content += "\n---\n\n## Comments\n\n"
        for comment in comments[:MAX_COMMENTS]:
            c_body = comment.get("body") or ""
            content += f"{c_body}\n\n---\n\n"

    return header + content


def fetch_repo_issues(
    session: requests.Session,
    repo_name: str,
    product: str,
    since: str,
    authenticated: bool,
) -> int:
    """Fetch recent issues for a single repo and write to disk.

    Args:
        session: Requests session.
        repo_name: GitHub repo name (under hashicorp org).
        product: Product name for metadata.
        since: ISO 8601 date string for the lookback window.
        authenticated: Whether a GITHUB_TOKEN is in use.

    Returns:
        Number of issues written.
    """
    output_dir = OUTPUT_ROOT / repo_name
    output_dir.mkdir(parents=True, exist_ok=True)

    max_pages = _MAX_PAGES_AUTH if authenticated else _MAX_PAGES_UNAUTH
    min_comments = _MIN_COMMENTS_AUTH if authenticated else _MIN_COMMENTS_UNAUTH
    written = 0
    page = 1

    while True:
        issues = _get_with_retry(
            session,
            f"{GITHUB_API}/repos/hashicorp/{repo_name}/issues",
            params={
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "since": since,
                "per_page": PER_PAGE,
                "page": page,
            },
        )

        if not issues:
            break

        for issue in issues:
            if not _is_useful(issue, min_comments):
                continue

            comments: list[dict] = []
            if authenticated and issue.get("comments", 0) > 0:
                try:
                    comments = _get_with_retry(session, issue["comments_url"], params={"per_page": MAX_COMMENTS})
                except requests.RequestException:
                    logger.warning("Failed to fetch comments for %s#%d", repo_name, issue["number"])

            md = format_issue(issue, comments, product, repo_name)
            dest = output_dir / f"{issue['number']}.md"
            dest.write_text(md, encoding="utf-8")
            written += 1

        if len(issues) < PER_PAGE or page >= max_pages:
            break
        page += 1

    return written


def main() -> None:
    """Fetch issues from HashiCorp repos.

    Always fetches REPOS_PRIORITY. Fetches REPOS_EXTENDED only when
    GITHUB_TOKEN is set.
    """
    since = (datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = _get_session()
    authenticated = bool(os.environ.get("GITHUB_TOKEN", ""))

    repos = dict(REPOS_PRIORITY)
    if authenticated:
        repos.update(REPOS_EXTENDED)
        logger.info("Fetching %d repos (priority + extended).", len(repos))
    else:
        logger.info("Fetching %d priority repos (set GITHUB_TOKEN to include %d extended repos).",
                    len(repos), len(REPOS_EXTENDED))

    total = 0
    for repo_name, product in repos.items():
        logger.info("Fetching issues for hashicorp/%s …", repo_name)
        try:
            n = fetch_repo_issues(session, repo_name, product, since, authenticated)
            total += n
            logger.info("  %s: %d issues written.", repo_name, n)
        except requests.HTTPError as exc:
            if "Rate limited" in str(exc):
                logger.warning("Stopping issue fetch early due to rate limiting — partial data will be uploaded.")
                break
            logger.error("Failed to fetch issues for %s: %s", repo_name, exc)
        except requests.RequestException as exc:
            logger.error("Failed to fetch issues for %s: %s", repo_name, exc)

    logger.info("GitHub issues complete: %d issues written across %d repos.", total, len(repos))


if __name__ == "__main__":
    main()
