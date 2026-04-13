#!/usr/bin/env python3
"""Fetch discussion threads from HashiCorp Discuss (Discourse) for RAG ingestion.

Queries the Discourse JSON API at discuss.hashicorp.com for recent topics across
HashiCorp product categories. Each topic with replies is written as a markdown
file with a metadata header to /workspace/cleaned/discuss/.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DISCUSS_BASE = "https://discuss.hashicorp.com"
OUTPUT_ROOT = Path("/workspace/cleaned/discuss")
LOOKBACK_DAYS = 365
MAX_REPLIES_PER_TOPIC = 5
MAX_RETRIES = 3
REQUEST_DELAY = 1.0  # seconds between API calls

# Category slugs on discuss.hashicorp.com mapped to product names.
CATEGORIES: dict[str, str] = {
    "terraform-core": "terraform",
    "terraform-providers": "terraform",
    "vault": "vault",
    "consul": "consul",
    "nomad": "nomad",
    "packer": "packer",
    "boundary": "boundary",
    "waypoint": "waypoint",
    "sentinel": "sentinel",
}


def _get_with_retry(url: str, params: dict | None = None) -> dict:
    """GET a URL with retry and polite delay.

    Args:
        url: Full URL to fetch.
        params: Query parameters.

    Returns:
        Parsed JSON response.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                logger.warning("Rate limited. Waiting %ds.", wait)
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


def _html_to_markdown(raw_html: str) -> str:
    """Convert HTML content to markdown using BeautifulSoup.

    Preserves code blocks, links, headings, lists, blockquotes, and tables
    that the previous regex-based approach would strip.

    Args:
        raw_html: HTML string from Discourse.

    Returns:
        Cleaned text with markdown formatting preserved.
    """
    soup = BeautifulSoup(raw_html, "html.parser")

    # Convert code blocks.
    for pre in soup.find_all("pre"):
        code = pre.get_text()
        pre.replace_with(f"\n```\n{code}\n```\n")

    for code in soup.find_all("code"):
        code.replace_with(f"`{code.get_text()}`")

    # Convert links — keep text only; URLs are not followable in retrieved context.
    for a in soup.find_all("a"):
        a.replace_with(a.get_text())

    # Convert headings.
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            h.replace_with(f"\n{'#' * level} {h.get_text()}\n")

    # Convert blockquotes.
    for bq in soup.find_all("blockquote"):
        lines = bq.get_text().strip().split("\n")
        bq.replace_with("\n".join(f"> {line}" for line in lines) + "\n")

    # Convert tables.
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        md_rows: list[str] = []
        for i, row in enumerate(rows):
            cells = row.find_all(["th", "td"])
            cell_texts = [c.get_text().strip() for c in cells]
            md_rows.append("| " + " | ".join(cell_texts) + " |")
            if i == 0:
                md_rows.append("| " + " | ".join("---" for _ in cells) + " |")
        table.replace_with("\n" + "\n".join(md_rows) + "\n")

    # Convert lists.
    for li in soup.find_all("li"):
        li.replace_with(f"- {li.get_text()}\n")

    text = soup.get_text()
    # Collapse excessive newlines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_date(date_str: str) -> datetime:
    """Parse an ISO 8601 date string from Discourse.

    Args:
        date_str: Date string like '2025-01-15T10:30:00.000Z'.

    Returns:
        Timezone-aware datetime.
    """
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


def _get_product_family(product: str, category: str) -> str:
    """Derive the product family from product name and category.

    Args:
        product: Product short name.
        category: Discourse category slug.

    Returns:
        Product family string.
    """
    if category.startswith("terraform") or product == "sentinel":
        return "terraform"
    return product


def format_topic(topic_data: dict, posts: list[dict], category: str, product: str) -> str:
    """Format a Discourse topic and its posts as markdown.

    If an accepted answer exists, it is placed immediately after the question
    (before other replies) so that the highest-value content appears first
    in the chunk.

    Args:
        topic_data: Topic metadata from the API.
        posts: List of post objects (first post + replies).
        category: Discourse category slug.
        product: Product name for metadata.

    Returns:
        Formatted markdown string.
    """
    title = topic_data.get("title", "Untitled")

    header = f"[discuss:{product}] {title}\n\n"

    content = f"# {title}\n\n"

    if posts:
        # First post is the question/topic body.
        first = posts[0]
        body = _html_to_markdown(first.get("cooked", ""))
        content += f"{body}\n"

        # Separate accepted answers from other replies.
        replies = posts[1:MAX_REPLIES_PER_TOPIC + 1]
        accepted_replies = [p for p in replies if p.get("accepted_answer")]
        other_replies = [p for p in replies if not p.get("accepted_answer")]

        # Emit accepted answers first for maximum RAG relevance.
        if accepted_replies:
            content += "\n## Accepted Answer\n\n"
            for post in accepted_replies:
                r_body = _html_to_markdown(post.get("cooked", ""))
                content += f"{r_body}\n"

        if other_replies:
            content += "\n---\n\n## Replies\n\n"
            for post in other_replies:
                r_body = _html_to_markdown(post.get("cooked", ""))
                content += f"{r_body}\n\n---\n\n"

    return header + content


def fetch_category_topics(category: str, product: str, cutoff: datetime) -> int:
    """Fetch recent topics for a single Discourse category.

    Args:
        category: Category slug.
        product: Product name for metadata.
        cutoff: Oldest topic creation date to include.

    Returns:
        Number of topics written.
    """
    output_dir = OUTPUT_ROOT / category
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    page = 0

    while True:
        try:
            data = _get_with_retry(f"{DISCUSS_BASE}/c/{category}/l/latest.json", params={"page": page})
        except requests.RequestException as exc:
            logger.error("Failed to fetch topic list for %s page %d: %s", category, page, exc)
            break

        topics = data.get("topic_list", {}).get("topics", [])
        if not topics:
            break

        reached_cutoff = False
        for topic in topics:
            created_at = _parse_date(topic.get("created_at", "2000-01-01T00:00:00Z"))
            if created_at < cutoff:
                reached_cutoff = True
                break

            # Skip topics with no replies — they're unanswered questions.
            if topic.get("reply_count", 0) < 1:
                continue

            topic_id = topic["id"]

            # Fetch full topic with posts.
            try:
                topic_data = _get_with_retry(f"{DISCUSS_BASE}/t/{topic_id}.json")
            except requests.RequestException as exc:
                logger.warning("Failed to fetch topic %d: %s", topic_id, exc)
                continue

            posts = topic_data.get("post_stream", {}).get("posts", [])
            md = format_topic(topic_data, posts, category, product)

            dest = output_dir / f"{topic_id}.md"
            dest.write_text(md, encoding="utf-8")
            written += 1

        if reached_cutoff or len(topics) < 30:
            break
        page += 1

    return written


def main() -> None:
    """Fetch discuss threads from all configured categories."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    total = 0

    for category, product in CATEGORIES.items():
        logger.info("Fetching discuss topics for %s …", category)
        try:
            n = fetch_category_topics(category, product, cutoff)
            total += n
            logger.info("  %s: %d topics written.", category, n)
        except Exception as exc:
            logger.error("Failed to process category %s: %s", category, exc)

    logger.info("Discuss fetch complete: %d topics written across %d categories.", total, len(CATEGORIES))


if __name__ == "__main__":
    main()
