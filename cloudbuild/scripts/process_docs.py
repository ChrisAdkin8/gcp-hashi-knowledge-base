#!/usr/bin/env python3
"""Process HashiCorp documentation markdown files for ingestion into Vertex AI RAG Engine.

Walks /workspace/repos/, finds docs directories, extracts YAML front matter,
prepends a structured metadata header, splits documents into semantic sections
at heading boundaries, filters out navigational bloat/stubs, and writes 
token-efficient cleaned files to /workspace/cleaned/.
"""

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

GITHUB_BASE = "https://github.com/hashicorp"

REPO_CONFIG: dict[str, dict[str, str]] = {
    # Core products — from legacy standalone repos
    "terraform-website": {"source_type": "documentation", "product": "terraform", "product_family": "terraform", "docs_subdir": "content"},
    "terraform": {"source_type": "documentation", "product": "terraform", "product_family": "terraform", "docs_subdir": "website/docs"},
    # Providers
    "terraform-provider-aws": {"source_type": "provider", "product": "aws", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-azurerm": {"source_type": "provider", "product": "azurerm", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-google": {"source_type": "provider", "product": "google", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-kubernetes": {"source_type": "provider", "product": "kubernetes", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-helm": {"source_type": "provider", "product": "helm", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-docker": {"source_type": "provider", "product": "docker", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-vault": {"source_type": "provider", "product": "vault", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-consul": {"source_type": "provider", "product": "consul", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-nomad": {"source_type": "provider", "product": "nomad", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-random": {"source_type": "provider", "product": "random", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-null": {"source_type": "provider", "product": "null", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-local": {"source_type": "provider", "product": "local", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-tls": {"source_type": "provider", "product": "tls", "product_family": "terraform", "docs_subdir": "website/docs"},
    "terraform-provider-http": {"source_type": "provider", "product": "http", "product_family": "terraform", "docs_subdir": "website/docs"},
    # Sentinel
    "terraform-sentinel-policies": {"source_type": "sentinel", "product": "sentinel", "product_family": "terraform", "docs_subdir": ""},
    "policy-library-aws-networking-terraform": {"source_type": "sentinel", "product": "sentinel", "product_family": "terraform", "docs_subdir": ""},
    "policy-library-azurerm-networking-terraform": {"source_type": "sentinel", "product": "sentinel", "product_family": "terraform", "docs_subdir": ""},
    "policy-library-gcp-networking-terraform": {"source_type": "sentinel", "product": "sentinel", "product_family": "terraform", "docs_subdir": ""},
}

# Products sourced from hashicorp/web-unified-docs (versioned layout).
# Each key is the subdirectory under content/; value is the product metadata.
UNIFIED_DOCS_PRODUCTS: dict[str, dict[str, str]] = {
    "nomad":                  {"source_type": "documentation", "product": "nomad",          "product_family": "nomad"},
    "vault":                  {"source_type": "documentation", "product": "vault",          "product_family": "vault"},
    "consul":                 {"source_type": "documentation", "product": "consul",         "product_family": "consul"},
    "boundary":               {"source_type": "documentation", "product": "boundary",       "product_family": "boundary"},
    "packer":                 {"source_type": "documentation", "product": "packer",         "product_family": "packer"},
    "terraform-docs-agents":  {"source_type": "documentation", "product": "terraform",      "product_family": "terraform"},
    "terraform-docs-common":  {"source_type": "documentation", "product": "hcp-terraform",  "product_family": "terraform"},
}

# Subpath within the latest version dir that holds the actual content.
# Most products: <version>/content/; terraform-docs-common: docs/
UNIFIED_CONTENT_SUBPATHS: dict[str, str] = {
    "terraform-docs-common": "docs",
}

DOCS_SEARCH_PATHS = ["docs", "website/docs", "content", "website/content"]
MIN_BODY_LENGTH = 100
MIN_SECTION_SIZE = 200

# ENHANCEMENT 2: Define a denylist for token-heavy, zero-value navigational sections.
EXCLUDED_SECTIONS = {"table of contents", "toc", "related links", "see also", "references", "navigation"}

# Heading pattern for section splitting: matches lines starting with ## or ###.
_HEADING_RE = re.compile(r"(?=^#{2,3}\s+.+$)", re.MULTILINE)

# Pattern to detect fenced code blocks.
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)


# ── Metadata helpers ─────────────────────────────────────────────────────────


def _construct_url(repo_name: str, docs_subdir: str, relative_path: str) -> str:
    if not repo_name:
        return ""
    base_path = f"{docs_subdir}/{relative_path}" if docs_subdir else relative_path
    return f"{GITHUB_BASE}/{repo_name}/blob/main/{base_path}"


def _infer_doc_category(relative_path: str) -> str:
    parts = Path(relative_path).parts
    path_lower = relative_path.lower()

    if parts and parts[0] == "r": return "resource-reference"
    if parts and parts[0] == "d": return "data-source-reference"
    if "guide" in path_lower or "tutorial" in path_lower: return "guide"
    if "getting-started" in path_lower or "intro" in path_lower: return "getting-started"
    if "api-docs" in path_lower or "api/" in path_lower: return "api-reference"
    if "commands" in path_lower or "cli" in path_lower: return "cli-reference"
    if "internals" in path_lower: return "internals"
    if "upgrade" in path_lower or "migration" in path_lower: return "upgrade-guide"
    if "configuration" in path_lower or "config" in path_lower: return "configuration"

    return "documentation"


def _infer_resource_type(product: str, source_type: str, relative_path: str) -> str:
    if source_type != "provider":
        return ""
    parts = Path(relative_path).parts
    if not parts or parts[0] not in ("r", "d"):
        return ""

    filename = Path(relative_path).stem
    if filename.endswith(".html"):
        filename = filename[:-5]

    return f"{product}_{filename}"


def _get_git_file_date(filepath: str) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%aI", "--", filepath],
            capture_output=True, text=True, timeout=10, cwd=str(Path(filepath).parent),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()[:10]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _format_compact_header(metadata: dict[str, str]) -> str:
    """Format a compact single-line attribution prefix for the document body.

    The full metadata is stored in the RAG Engine's metadata fields via
    generate_metadata.py. Only enough context to orient the LLM is written
    into the body, minimising tokens consumed per retrieved chunk.
    """
    source_type = metadata.get("source_type", "doc")
    product = metadata.get("product", "")
    resource_type = metadata.get("resource_type", "")
    title = metadata.get("title", "")
    section_title = metadata.get("section_title", "")

    label = resource_type or title
    if section_title and section_title.lower() != label.lower():
        label = f"{label} — {section_title}"

    return f"[{source_type}:{product}] {label}\n\n"


# ENHANCEMENT 1: Helper function to strip layout HTML tags while keeping content.
def _strip_layout_html(text: str) -> str:
    """Strips common structural/JSX tags that consume tokens without adding semantic value."""
    text = re.sub(r'', '', text, flags=re.DOTALL)  # Remove HTML comments
    text = re.sub(r'</?(div|span|br|hr|a|img|nav|footer)[^>]*>', '', text, flags=re.IGNORECASE)
    # Strip MDX/JSX components common in web-unified-docs
    text = re.sub(r'</?(?:Tabs|Tab|Highlight|Note|Warning|Tip|EnterpriseAlert|CodeBlockConfig|CodeTabs|Placement)[^>]*>', '', text, flags=re.IGNORECASE)
    # Strip import statements (MDX imports)
    text = re.sub(r'^import\s+.*$', '', text, flags=re.MULTILINE)
    return text


def _compress_code_blocks(text: str) -> str:
    """Compress fenced code blocks by stripping comments and collapsing blank lines.

    Targets HCL, JSON, YAML, and shell examples common in HashiCorp docs.
    Preserves the code fence markers and language annotation.
    """
    def _compress_block(match: re.Match) -> str:
        fence_open = match.group(1)   # e.g. ```hcl
        code = match.group(2)
        fence_close = match.group(3)  # ```

        # Strip single-line comments (# and //) but keep shebang lines
        code = re.sub(r'^(?!#!)[ \t]*(?:#|//)(?!\!).*\n?', '', code, flags=re.MULTILINE)
        # Strip trailing inline comments (keep quoted strings safe by requiring whitespace before #)
        code = re.sub(r'[ \t]+(?:#|//)[ \t]+[^\n]*$', '', code, flags=re.MULTILINE)
        # Collapse runs of blank lines to a single blank line
        code = re.sub(r'\n{3,}', '\n\n', code)

        return f"{fence_open}{code}{fence_close}"

    return re.sub(
        r'(```[^\n]*\n)(.*?)(```)',
        _compress_block,
        text,
        flags=re.DOTALL,
    )


# ── Semantic section splitting ────────────────────────────────────────────────


def split_into_sections(body: str) -> list[tuple[str, str]]:
    parts = _HEADING_RE.split(body)
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
            title = first_line.lstrip("#").strip()
            section_body = lines[1].strip() if len(lines) > 1 else ""
        else:
            title = ""
            section_body = part

        # ENHANCEMENT 2: Filter out token-heavy, low-value navigational sections.
        if title.lower() in EXCLUDED_SECTIONS:
            continue

        if sections and len(section_body) < MIN_SECTION_SIZE:
            prev_title, prev_body = sections[-1]
            if first_line.startswith("##"):
                merged = f"{prev_body}\n\n{first_line}\n{section_body}".strip()
            else:
                merged = f"{prev_body}\n\n{section_body}".strip()
            sections[-1] = (prev_title, merged)
        else:
            if first_line.startswith("##"):
                full_body = f"{first_line}\n{section_body}".strip()
            else:
                full_body = section_body
            sections.append((title, full_body))

    return sections if sections else [("", body.strip())]


def _split_large_section(title: str, body: str, max_chars: int = 2000) -> list[tuple[str, str]]:
    if len(body) <= max_chars:
        return [(title, body)]

    fences = [m.start() for m in _CODE_FENCE_RE.finditer(body)]
    if len(fences) < 4:
        return [(title, body)]

    split_points: list[int] = []
    for i in range(1, len(fences) - 1, 2):
        close_end = body.index("\n", fences[i]) + 1 if "\n" in body[fences[i]:] else fences[i] + 3
        split_points.append(close_end)

    if not split_points:
        return [(title, body)]

    parts: list[tuple[str, str]] = []
    start = 0
    heading_prefix = f"## {title}\n\n" if title else ""

    for sp in split_points:
        if len(body) - start > max_chars:
            chunk = body[start:sp].strip()
            if chunk:
                if not parts:
                    parts.append((title, chunk))
                else:
                    parts.append((title, heading_prefix + chunk))
            start = sp

    remainder = body[start:].strip()
    if remainder:
        if parts:
            parts.append((title, heading_prefix + remainder))
        else:
            parts.append((title, remainder))

    return parts if parts else [(title, body)]


# ── Core functions ─────────────────────────────────────────────────────────────


def extract_front_matter(content: str) -> tuple[dict, str]:
    pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
    match = pattern.match(content)
    if not match:
        return {}, content

    raw_yaml = match.group(1)
    try:
        data = yaml.safe_load(raw_yaml) or {}
        if not isinstance(data, dict):
            return {}, content
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse YAML front matter: %s", exc)
        return {}, content[match.end():]

    body = content[match.end():]
    return data, body


def _find_docs_dir(repo_path: Path, preferred_subdir: str) -> Path | None:
    candidates = []
    if preferred_subdir:
        candidates.append(repo_path / preferred_subdir)
    candidates.extend(repo_path / p for p in DOCS_SEARCH_PATHS)

    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def process_file(
    filepath: str,
    source_type: str,
    product: str,
    repo_name: str,
    *,
    product_family: str = "",
    docs_subdir: str = "",
    relative_path: str = "",
) -> tuple[dict[str, str], str] | None:
    try:
        raw = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Cannot read %s: %s", filepath, exc)
        return None

    front_matter, body = extract_front_matter(raw)

    body_stripped = body.strip()
    
    # ENHANCEMENT 1: Strip HTML tags
    body_stripped = _strip_layout_html(body_stripped)

    # ENHANCEMENT 6: Compress code blocks (strip comments, collapse blank lines)
    body_stripped = _compress_code_blocks(body_stripped)

    # ENHANCEMENT 3: Collapse excessive blank lines to save tokens
    body_stripped = re.sub(r'\n{3,}', '\n\n', body_stripped)
    
    if len(body_stripped) < MIN_BODY_LENGTH:
        logger.warning("Skipping %s — body too short (%d chars)", filepath, len(body_stripped))
        return None

    # ENHANCEMENT 4: Aggressive front matter pruning (inclusion list instead of exclusion)
    title = str(front_matter.get("page_title") or front_matter.get("title") or Path(filepath).stem)
    description = str(front_matter.get("description") or "")

    url = _construct_url(repo_name, docs_subdir, relative_path)
    doc_category = _infer_doc_category(relative_path)
    resource_type = _infer_resource_type(product, source_type, relative_path)
    last_updated = _get_git_file_date(filepath)

    metadata: dict[str, str] = {
        "source_type": source_type,
        "product": product,
        "product_family": product_family or product,
        "repo": repo_name,
        "title": title,
        "description": description,
        "url": url,
        "doc_category": doc_category,
        "last_updated": last_updated,
    }
    if resource_type:
        metadata["resource_type"] = resource_type

    return metadata, body_stripped


def process_directory(
    input_dir: str,
    output_dir: str,
    source_type: str,
    product: str,
    repo_name: str,
    *,
    product_family: str = "",
    docs_subdir: str = "",
) -> int:
    processed = 0
    input_path = Path(input_dir)
    output_path = Path(output_dir)

    for root, _dirs, files in os.walk(input_dir):
        for filename in files:
            if not filename.endswith((".md", ".mdx")):
                continue

            src = Path(root) / filename
            rel = src.relative_to(input_path)
            relative_path = str(rel)

            try:
                result = process_file(
                    str(src), source_type, product, repo_name,
                    product_family=product_family,
                    docs_subdir=docs_subdir,
                    relative_path=relative_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Unexpected error processing %s: %s", src, exc)
                continue

            if result is None:
                continue

            metadata, body = result
            sections = split_into_sections(body)

            expanded: list[tuple[str, str]] = []
            for sec_title, sec_body in sections:
                expanded.extend(_split_large_section(sec_title, sec_body))

            # ENHANCEMENT 5: Filter out empty/stub sections
            final_sections: list[tuple[str, str]] = []
            for sec_title, sec_body in expanded:
                # Exclude chunks under 50 characters that contain no code blocks
                if len(sec_body.strip()) < 50 and "```" not in sec_body:
                    continue
                final_sections.append((sec_title, sec_body))
            
            sections = final_sections
            
            if not sections:
                continue

            if len(sections) == 1:
                dest = output_path / rel.with_suffix(".md")
                header = _format_compact_header(metadata)
                content = header + sections[0][1]
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dest.write_text(content, encoding="utf-8")
                    processed += 1
                except OSError as exc:
                    logger.error("Failed to write %s: %s", dest, exc)
            else:
                stem = rel.with_suffix("").as_posix().replace("/", "_")
                for i, (section_title, section_body) in enumerate(sections):
                    section_metadata = dict(metadata)
                    if section_title:
                        section_metadata["section_title"] = section_title

                    dest = output_path / f"{stem}_s{i}.md"
                    header = _format_compact_header(section_metadata)
                    content = header + section_body
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        dest.write_text(content, encoding="utf-8")
                        processed += 1
                    except OSError as exc:
                        logger.error("Failed to write %s: %s", dest, exc)

    return processed


# ── Main ───────────────────────────────────────────────────────────────────────


def _resolve_latest_version(product_dir: Path) -> Path | None:
    """Return the latest non-RC versioned subdirectory (e.g. v1.11.x)."""
    candidates = [
        d for d in product_dir.iterdir()
        if d.is_dir() and d.name.startswith("v") and "rc" not in d.name.lower()
    ]
    if not candidates:
        return None
    # Sort by version segments: v1.11.x → (1, 11)
    def _version_key(p: Path) -> tuple[int, ...]:
        parts = p.name.lstrip("v").rstrip(".x").split(".")
        return tuple(int(x) for x in parts if x.isdigit())
    candidates.sort(key=_version_key)
    return candidates[-1]


def main() -> None:
    repos_root = Path("/workspace/repos")
    cleaned_root = Path("/workspace/cleaned")

    if not repos_root.exists():
        logger.error("Repos directory %s does not exist.", repos_root)
        sys.exit(1)

    counts: dict[str, int] = {
        "documentation": 0,
        "provider": 0,
        "module": 0,
        "sentinel": 0,
    }

    for repo_dir in sorted(repos_root.iterdir()):
        if not repo_dir.is_dir():
            continue

        repo_name = repo_dir.name

        # web-unified-docs is handled separately below.
        if repo_name == "web-unified-docs":
            continue

        config = REPO_CONFIG.get(repo_name)

        if config is None:
            logger.info("No config for repo %s — using autodiscovery.", repo_name)
            if "provider" in repo_name:
                source_type = "provider"
                product = repo_name.replace("terraform-provider-", "")
                product_family = "terraform"
            elif "sentinel" in repo_name or "policy-library" in repo_name:
                source_type = "sentinel"
                product = repo_name
                product_family = "terraform"
            else:
                source_type = "module"
                product = repo_name
                product_family = "terraform"
            preferred_subdir = ""
        else:
            source_type = config["source_type"]
            product = config["product"]
            product_family = config.get("product_family", product)
            preferred_subdir = config.get("docs_subdir", "")

        docs_dir = _find_docs_dir(repo_dir, preferred_subdir)
        if docs_dir is None:
            logger.warning("No docs directory found for %s — skipping.", repo_name)
            continue

        output_dir = cleaned_root / source_type / repo_name
        logger.info("Processing %s (%s) → %s", repo_name, source_type, output_dir)

        n = process_directory(
            str(docs_dir), str(output_dir), source_type, product, repo_name,
            product_family=product_family,
            docs_subdir=preferred_subdir,
        )
        counts[source_type] = counts.get(source_type, 0) + n
        logger.info("  %s: %d files processed.", repo_name, n)

    # ── Process web-unified-docs (versioned, multi-product) ──────────────────
    unified_root = repos_root / "web-unified-docs" / "content"
    if unified_root.is_dir():
        for product_slug, meta in UNIFIED_DOCS_PRODUCTS.items():
            product_content_root = unified_root / product_slug
            if not product_content_root.is_dir():
                logger.warning("Unified docs: no directory for %s — skipping.", product_slug)
                continue

            # Versioned products (nomad, vault, …) vs. unversioned (terraform-docs-common)
            content_subpath = UNIFIED_CONTENT_SUBPATHS.get(product_slug, "content")
            latest = _resolve_latest_version(product_content_root)
            if latest is not None:
                docs_dir = latest / content_subpath
            else:
                docs_dir = product_content_root / content_subpath

            if not docs_dir.is_dir():
                logger.warning("Unified docs: content dir not found for %s at %s — skipping.", product_slug, docs_dir)
                continue

            source_type = meta["source_type"]
            product = meta["product"]
            product_family = meta["product_family"]
            output_dir = cleaned_root / source_type / product

            version_label = latest.name if latest else "unversioned"
            logger.info("Processing unified %s (%s) → %s", product_slug, version_label, output_dir)

            n = process_directory(
                str(docs_dir), str(output_dir), source_type, product,
                "web-unified-docs",
                product_family=product_family,
                docs_subdir=f"content/{product_slug}/{version_label}/{content_subpath}",
            )
            counts[source_type] = counts.get(source_type, 0) + n
            logger.info("  %s: %d files processed.", product_slug, n)
    else:
        logger.warning("web-unified-docs/content not found — skipping unified docs.")

    total = sum(counts.values())
    rows = "\n".join(f"  {cat:<20} {cnt:>6}" for cat, cnt in sorted(counts.items()))
    summary = (
        f"\n=== Processing summary ===\n"
        f"  {'Category':<20} {'Files':>6}\n"
        f"  {'-'*20} {'-'*6}\n"
        f"{rows}\n"
        f"  {'TOTAL':<20} {total:>6}"
    )
    print(summary)


if __name__ == "__main__":
    main()