#!/usr/bin/env python3
"""Discover HashiCorp modules and providers from the Terraform Registry.

Writes module GitHub URLs to /workspace/module_repos.txt and extra provider
URLs to /workspace/extra_provider_repos.txt.
"""

import logging
import time
from collections.abc import Iterator

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

REGISTRY_BASE = "https://registry.terraform.io/v1"
MODULE_REPOS_PATH = "/workspace/module_repos.txt"
EXTRA_PROVIDERS_PATH = "/workspace/extra_provider_repos.txt"
MAX_RETRIES = 3
PAGE_LIMIT = 100


def _get_with_retry(url: str, params: dict) -> dict:
    """GET a URL with exponential-backoff retry on failure.

    Args:
        url: Full URL to fetch.
        params: Query parameters.

    Returns:
        Parsed JSON response body.

    Raises:
        requests.HTTPError: If all retries are exhausted.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()  # type: ignore[no-any-return]
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                logger.error("All %d retries exhausted for %s: %s", MAX_RETRIES, url, exc)
                raise
            backoff = 2 ** (attempt - 1)
            logger.warning(
                "Request failed (attempt %d/%d), retrying in %ds: %s",
                attempt,
                MAX_RETRIES,
                backoff,
                exc,
            )
            time.sleep(backoff)
    # Unreachable but satisfies type checker
    raise RuntimeError("Unexpected exit from retry loop")


def _paginate_modules(namespace: str, verified: bool) -> Iterator[dict]:
    """Yield all module records for a namespace from the registry.

    Args:
        namespace: Registry namespace (e.g. "hashicorp").
        verified: If True, filter to verified modules only.

    Yields:
        Individual module metadata dicts.
    """
    offset = 0
    while True:
        data = _get_with_retry(
            f"{REGISTRY_BASE}/modules",
            params={
                "namespace": namespace,
                "verified": str(verified).lower(),
                "offset": offset,
                "limit": PAGE_LIMIT,
            },
        )
        modules = data.get("modules", [])
        if not modules:
            break
        yield from modules
        meta = data.get("meta", {})
        next_offset = meta.get("next_offset")
        if next_offset is None or next_offset <= offset:
            break
        offset = next_offset


def _paginate_providers(tier: str) -> Iterator[dict]:
    """Yield all provider records for a tier from the registry.

    Args:
        tier: Provider tier (e.g. "official").

    Yields:
        Individual provider metadata dicts.
    """
    offset = 0
    while True:
        data = _get_with_retry(
            f"{REGISTRY_BASE}/providers",
            params={
                "tier": tier,
                "offset": offset,
                "limit": PAGE_LIMIT,
            },
        )
        providers = data.get("providers", [])
        if not providers:
            break
        yield from providers
        meta = data.get("meta", {})
        next_offset = meta.get("next_offset")
        if next_offset is None or next_offset <= offset:
            break
        offset = next_offset


def discover_modules() -> list[str]:
    """Discover HashiCorp verified modules hosted on GitHub.

    Returns:
        Deduplicated list of GitHub clone URLs.
    """
    logger.info("Discovering HashiCorp verified modules from registry …")
    seen: set[str] = set()
    urls: list[str] = []

    for module in _paginate_modules(namespace="hashicorp", verified=True):
        source = module.get("source", "")
        if not source or "github.com" not in source:
            continue
        # source format: "github.com/org/repo"
        clone_url = f"https://{source}.git"
        if clone_url not in seen:
            seen.add(clone_url)
            urls.append(clone_url)

    logger.info("Discovered %d unique module repos on GitHub.", len(urls))
    return urls


def discover_extra_providers() -> list[str]:
    """Discover official provider repos not in the hardcoded list.

    Returns:
        List of GitHub clone URLs for official providers.
    """
    logger.info("Discovering official providers from registry …")
    seen: set[str] = set()
    urls: list[str] = []

    for provider in _paginate_providers(tier="official"):
        source = provider.get("source", "")
        if not source or "github.com" not in source:
            continue
        clone_url = f"https://{source}.git"
        if clone_url not in seen:
            seen.add(clone_url)
            urls.append(clone_url)

    logger.info("Discovered %d official provider repos on GitHub.", len(urls))
    return urls


def write_urls(urls: list[str], path: str) -> None:
    """Write a list of URLs to a file, one per line.

    Args:
        urls: URLs to write.
        path: Destination file path.
    """
    with open(path, "w") as fh:
        fh.write("\n".join(urls))
        if urls:
            fh.write("\n")
    logger.info("Wrote %d URLs to %s.", len(urls), path)


def main() -> None:
    """Entry point."""
    module_urls = discover_modules()
    write_urls(module_urls, MODULE_REPOS_PATH)

    provider_urls = discover_extra_providers()
    write_urls(provider_urls, EXTRA_PROVIDERS_PATH)


if __name__ == "__main__":
    main()
