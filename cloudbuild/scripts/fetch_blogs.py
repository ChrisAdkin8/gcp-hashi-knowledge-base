#!/usr/bin/env python3
"""Fetch blog posts from HashiCorp and Medium SE blog for RAG ingestion.

Sources:
  1. HashiCorp official blog — Atom feed at hashicorp.com/blog/feed.xml
     plus paginated archive at hashicorp.com/en/blog/all
  2. HashiCorp Solutions Engineering blog — Medium RSS at
     medium.com/feed/hashicorp-engineering

Each post is written as a markdown file with a metadata header to
/workspace/cleaned/blog/.
"""

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_ROOT = Path("/workspace/cleaned/blog")
LOOKBACK_DAYS = 365
MAX_RETRIES = 3
REQUEST_DELAY = 1.0

HASHICORP_FEED = "https://www.hashicorp.com/blog/feed.xml"
HASHICORP_ARCHIVE = "https://www.hashicorp.com/en/blog/all"
MEDIUM_FEED = "https://medium.com/feed/hashicorp-engineering"

# Keywords for detecting product family from blog post titles/content.
_PRODUCT_KEYWORDS: dict[str, str] = {
    "terraform": "terraform",
    "terraform cloud": "terraform",
    "terraform enterprise": "terraform",
    "hcp terraform": "terraform",
    "vault": "vault",
    "consul": "consul",
    "nomad": "nomad",
    "packer": "packer",
    "boundary": "boundary",
    "waypoint": "waypoint",
    "sentinel": "terraform",
    "vagrant": "vagrant",
    "hcp": "hashicorp",
}


def _detect_product_family(title: str, body: str = "") -> str:
    """Detect the product family from a blog post title and body.

    Scans the title (weighted 3x) and full body for product keywords.
    Returns the product with the highest weighted frequency. Falls back
    to ``hashicorp`` if no product keywords are found.

    Args:
        title: Blog post title.
        body: Blog post body text.

    Returns:
        Product family string, or ``hashicorp`` if no specific product detected.
    """
    title_lower = title.lower()
    body_lower = body.lower()

    scores: dict[str, int] = {}
    for keyword, family in _PRODUCT_KEYWORDS.items():
        # Title matches weighted 3x.
        title_count = title_lower.count(keyword) * 3
        body_count = body_lower.count(keyword)
        total = title_count + body_count
        if total > 0:
            scores[family] = scores.get(family, 0) + total

    if not scores:
        return "hashicorp"

    return max(scores, key=scores.get)


# ── Shared utilities ──────────────────────────────────────────────────────────


def _fetch(url: str, **kwargs: object) -> requests.Response:
    """Fetch a URL with retry logic.

    Args:
        url: URL to fetch.
        **kwargs: Passed to requests.get.

    Returns:
        Response object.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(REQUEST_DELAY)
            resp = requests.get(url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            backoff = 2 ** (attempt - 1)
            logger.warning("Fetch failed (attempt %d/%d), retrying in %ds: %s", attempt, MAX_RETRIES, backoff, exc)
            time.sleep(backoff)
    raise RuntimeError("Unexpected exit from retry loop")


def _html_to_markdown(raw_html: str) -> str:
    """Convert HTML to simplified markdown text.

    Args:
        raw_html: HTML content string.

    Returns:
        Cleaned text with basic markdown formatting.
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

    # Convert lists.
    for li in soup.find_all("li"):
        li.replace_with(f"- {li.get_text()}\n")

    text = soup.get_text()
    # Collapse excessive newlines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _slug_from_url(url: str) -> str:
    """Extract a filename-safe slug from a URL.

    Args:
        url: Full URL.

    Returns:
        Slug string suitable for a filename.
    """
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] or "index"
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug)
    return slug[:120]


_BLOG_HEADING_RE = re.compile(r"(?=^#{2,3}\s+.+$)", re.MULTILINE)
_MIN_BLOG_SECTION = 200


def _split_blog_body(body: str) -> list[tuple[str, str]]:
    """Split a blog post body at ## / ### heading boundaries.

    Mirrors the semantic splitting in process_docs.py so that long blog posts
    are chunked along structural boundaries rather than being blindly re-cut at
    a fixed token limit by Vertex AI RAG Engine.

    Returns:
        List of (section_title, section_body) tuples. Single-section posts
        return a list with one entry whose title is an empty string.
    """
    parts = _BLOG_HEADING_RE.split(body)
    if len(parts) <= 1:
        return [("", body.strip())]

    sections: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        lines = part.split("\n", 1)
        first_line = lines[0].strip()
        if first_line.startswith("##"):
            sec_title = first_line.lstrip("#").strip()
            sec_body = f"{first_line}\n{lines[1].strip()}" if len(lines) > 1 else first_line
        else:
            sec_title = ""
            sec_body = part

        if not sec_body:
            continue

        if sections and len(sec_body) < _MIN_BLOG_SECTION:
            prev_title, prev_body = sections[-1]
            sections[-1] = (prev_title, f"{prev_body}\n\n{sec_body}".strip())
        else:
            sections.append((sec_title, sec_body))

    return sections if sections else [("", body.strip())]


def _parse_iso_date(date_str: str) -> datetime | None:
    """Parse various ISO 8601 date formats.

    Args:
        date_str: Date string.

    Returns:
        Timezone-aware datetime, or None on failure.
    """
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    # Try fromisoformat as fallback.
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt
    except ValueError:
        return None


# ── HashiCorp Blog (Atom feed) ────────────────────────────────────────────────


def _parse_atom_feed(xml_text: str, cutoff: datetime) -> list[dict]:
    """Parse an Atom feed and return entries within the cutoff window.

    Args:
        xml_text: Raw XML string.
        cutoff: Oldest date to include.

    Returns:
        List of dicts with keys: title, url, author, date, content, summary.
    """
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    entries: list[dict] = []

    for entry in root.findall("atom:entry", ns):
        updated = entry.findtext("atom:updated", "", ns)
        dt = _parse_iso_date(updated)
        if dt and dt < cutoff:
            continue

        title = entry.findtext("atom:title", "Untitled", ns)
        link_el = entry.find("atom:link", ns)
        url = link_el.get("href", "") if link_el is not None else ""
        author = entry.findtext("atom:author/atom:name", "HashiCorp", ns)
        summary = entry.findtext("atom:summary", "", ns)
        content_el = entry.find("atom:content", ns)
        content = content_el.text if content_el is not None and content_el.text else summary

        entries.append({
            "title": title,
            "url": url,
            "author": author,
            "date": updated[:10],
            "content": content,
            "summary": summary,
        })

    return entries


def fetch_hashicorp_blog(cutoff: datetime) -> int:
    """Fetch HashiCorp blog posts from Atom feed and archive.

    Args:
        cutoff: Oldest publication date to include.

    Returns:
        Number of posts written.
    """
    output_dir = OUTPUT_ROOT / "hashicorp"
    output_dir.mkdir(parents=True, exist_ok=True)
    seen_slugs: set[str] = set()
    written = 0

    # 1. Atom feed (recent posts with full content).
    logger.info("Fetching HashiCorp blog Atom feed …")
    try:
        resp = _fetch(HASHICORP_FEED)
        entries = _parse_atom_feed(resp.text, cutoff)
        logger.info("  Atom feed: %d entries within cutoff.", len(entries))

        for entry in entries:
            slug = _slug_from_url(entry["url"])
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            body = _html_to_markdown(entry["content"]) if entry["content"] else ""
            if len(body) < 100:
                continue

            product_family = _detect_product_family(entry["title"], body)
            sections = _split_blog_body(body)

            for i, (sec_title, sec_body) in enumerate(sections):
                label = f"{entry['title']} — {sec_title}" if sec_title else entry["title"]
                md = f"[blog:{product_family}] {label}\n\n{sec_body}\n"
                dest = output_dir / (f"{slug}.md" if len(sections) == 1 else f"{slug}_s{i}.md")
                dest.write_text(md, encoding="utf-8")
                written += 1
    except requests.RequestException as exc:
        logger.error("Failed to fetch Atom feed: %s", exc)

    # 2. Archive pages (broader coverage, extract links then fetch each).
    logger.info("Fetching HashiCorp blog archive pages …")
    page = 1
    while True:
        try:
            url = f"{HASHICORP_ARCHIVE}?page={page}" if page > 1 else HASHICORP_ARCHIVE
            resp = _fetch(url)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Find blog post links — look for <a> tags with /blog/ in href.
            links = soup.find_all("a", href=re.compile(r"/blog/[a-z0-9]"))
            post_urls: list[str] = []
            for link in links:
                href = link.get("href", "")
                if href.startswith("/"):
                    href = f"https://www.hashicorp.com{href}"
                if "/blog/" in href and href not in post_urls:
                    post_urls.append(href)

            if not post_urls:
                break

            for post_url in post_urls:
                slug = _slug_from_url(post_url)
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)

                try:
                    post_resp = _fetch(post_url)
                    post_soup = BeautifulSoup(post_resp.text, "html.parser")

                    # Extract article content.
                    article = post_soup.find("article") or post_soup.find("main") or post_soup.find("div", class_=re.compile("content|post|article"))
                    if not article:
                        continue

                    title_el = post_soup.find("h1")
                    title = title_el.get_text().strip() if title_el else slug

                    # Check publication date from meta tags.
                    date_meta = post_soup.find("meta", property="article:published_time")
                    pub_date = date_meta.get("content", "") if date_meta else ""
                    if pub_date:
                        dt = _parse_iso_date(pub_date)
                        if dt and dt < cutoff:
                            continue
                        pub_date = pub_date[:10]

                    body = _html_to_markdown(str(article))
                    if len(body) < 100:
                        continue

                    product_family = _detect_product_family(title, body)
                    sections = _split_blog_body(body)

                    for i, (sec_title, sec_body) in enumerate(sections):
                        label = f"{title} — {sec_title}" if sec_title else title
                        md = f"[blog:{product_family}] {label}\n\n{sec_body}\n"
                        dest = output_dir / (f"{slug}.md" if len(sections) == 1 else f"{slug}_s{i}.md")
                        dest.write_text(md, encoding="utf-8")
                        written += 1
                except requests.RequestException:
                    logger.warning("Failed to fetch blog post: %s", post_url)

            page += 1
            # Safety limit on archive pages.
            if page > 50:
                break
        except requests.RequestException as exc:
            logger.error("Failed to fetch archive page %d: %s", page, exc)
            break

    return written


# ── Medium SE Blog (RSS feed) ────────────────────────────────────────────────


def _parse_rss_feed(xml_text: str, cutoff: datetime) -> list[dict]:
    """Parse a Medium RSS feed and return items within the cutoff window.

    Args:
        xml_text: Raw XML string.
        cutoff: Oldest date to include.

    Returns:
        List of dicts with keys: title, url, author, date, content.
    """
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    entries: list[dict] = []
    for item in channel.findall("item"):
        pub_date_str = item.findtext("pubDate", "")
        # Medium uses RFC 822 dates like "Fri, 14 Mar 2025 12:00:00 GMT".
        dt = None
        if pub_date_str:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date_str)
            except (ValueError, TypeError):
                pass

        if dt and dt < cutoff:
            continue

        title = item.findtext("title", "Untitled")
        url = item.findtext("link", "")
        author = item.findtext("{http://purl.org/dc/elements/1.1/}creator", "HashiCorp SE")
        # Medium puts full HTML in content:encoded.
        content = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", "")
        if not content:
            content = item.findtext("description", "")

        entries.append({
            "title": title,
            "url": url,
            "author": author,
            "date": dt.strftime("%Y-%m-%d") if dt else "",
            "content": content,
        })

    return entries


def fetch_medium_se_blog(cutoff: datetime) -> int:
    """Fetch Solutions Engineering blog posts from Medium RSS.

    Args:
        cutoff: Oldest publication date to include.

    Returns:
        Number of posts written.
    """
    output_dir = OUTPUT_ROOT / "solutions-engineering"
    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0

    logger.info("Fetching Medium SE blog RSS feed …")
    try:
        resp = _fetch(MEDIUM_FEED)
        entries = _parse_rss_feed(resp.text, cutoff)
        logger.info("  Medium feed: %d entries within cutoff.", len(entries))

        for entry in entries:
            slug = _slug_from_url(entry["url"])
            body = _html_to_markdown(entry["content"]) if entry["content"] else ""
            if len(body) < 100:
                continue

            product_family = _detect_product_family(entry["title"], body)
            sections = _split_blog_body(body)

            for i, (sec_title, sec_body) in enumerate(sections):
                label = f"{entry['title']} — {sec_title}" if sec_title else entry["title"]
                md = f"[blog:{product_family}] {label}\n\n{sec_body}\n"
                dest = output_dir / (f"{slug}.md" if len(sections) == 1 else f"{slug}_s{i}.md")
                dest.write_text(md, encoding="utf-8")
                written += 1
    except requests.RequestException as exc:
        logger.error("Failed to fetch Medium RSS feed: %s", exc)

    return written


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    """Fetch blog posts from all configured sources."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    hc_count = fetch_hashicorp_blog(cutoff)
    logger.info("HashiCorp blog: %d posts written.", hc_count)

    se_count = fetch_medium_se_blog(cutoff)
    logger.info("SE blog: %d posts written.", se_count)

    logger.info("Blog fetch complete: %d total posts.", hc_count + se_count)


if __name__ == "__main__":
    main()
