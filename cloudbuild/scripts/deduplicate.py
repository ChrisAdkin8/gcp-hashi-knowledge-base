#!/usr/bin/env python3
"""Remove near-duplicate documents from the cleaned output before upload.

Walks /workspace/cleaned/, computes a content hash for each file (ignoring
the metadata header), and removes files whose body text matches a previously
seen file. Keeps the first file encountered (sorted by path for determinism).

Usage:
    python3 cloudbuild/scripts/deduplicate.py [--dry-run]
"""

import hashlib
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

CLEANED_ROOT = Path("/workspace/cleaned")

# Metadata header is all lines before the first blank line.
_HEADER_END_RE = re.compile(r"\n\n", re.MULTILINE)


def _extract_body(content: str) -> str:
    """Extract the body text after the metadata header.

    Args:
        content: Full file content including metadata header.

    Returns:
        Body text with leading/trailing whitespace stripped.
    """
    match = _HEADER_END_RE.search(content)
    if match:
        return content[match.end():].strip()
    return content.strip()


def _normalise(text: str) -> str:
    """Normalise text for comparison: lowercase, collapse whitespace.

    Args:
        text: Raw body text.

    Returns:
        Normalised string.
    """
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _content_hash(text: str) -> str:
    """Compute a SHA-256 hash of normalised text.

    Args:
        text: Normalised body text.

    Returns:
        Hex digest string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def deduplicate(root: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Remove duplicate files under root.

    Args:
        root: Directory to walk.
        dry_run: If True, log but do not delete.

    Returns:
        Tuple of (total_files, duplicates_removed).
    """
    seen: dict[str, Path] = {}
    total = 0
    removed = 0

    # Sort for determinism — first file alphabetically wins.
    for filepath in sorted(root.rglob("*.md")):
        if not filepath.is_file():
            continue
        total += 1

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", filepath, exc)
            continue

        body = _extract_body(content)
        if not body:
            continue

        normalised = _normalise(body)

        # Skip very short bodies — they're likely stubs and will
        # hash-collide frequently without being true duplicates.
        if len(normalised) < 100:
            continue

        digest = _content_hash(normalised)

        if digest in seen:
            original = seen[digest]
            if dry_run:
                logger.info("DUPLICATE (dry-run): %s duplicates %s", filepath, original)
            else:
                filepath.unlink()
                logger.info("REMOVED: %s (duplicate of %s)", filepath, original)
            removed += 1
        else:
            seen[digest] = filepath

    return total, removed


def main() -> None:
    """Entry point."""
    dry_run = "--dry-run" in sys.argv

    if not CLEANED_ROOT.exists():
        logger.error("Cleaned directory %s does not exist.", CLEANED_ROOT)
        sys.exit(1)

    total, removed = deduplicate(CLEANED_ROOT, dry_run=dry_run)

    logger.info(
        "Deduplication %s: %d files scanned, %d duplicates %s.",
        "dry-run" if dry_run else "complete",
        total,
        removed,
        "would be removed" if dry_run else "removed",
    )


if __name__ == "__main__":
    main()
