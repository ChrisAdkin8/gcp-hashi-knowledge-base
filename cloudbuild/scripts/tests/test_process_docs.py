"""Unit tests for process_docs.py."""

import sys
from pathlib import Path

import pytest

# Allow importing process_docs from the parent directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from process_docs import (
    _construct_url,
    _format_compact_header,
    _infer_doc_category,
    _infer_resource_type,
    _split_large_section,
    extract_front_matter,
    process_directory,
    process_file,
    split_into_sections,
)


# ── extract_front_matter ───────────────────────────────────────────────────────


def test_extract_front_matter_with_valid_yaml() -> None:
    """Valid front matter is parsed correctly; body is the remaining content."""
    content = "---\ntitle: My Page\ndescription: A test page.\n---\n\nBody content here."
    fm, body = extract_front_matter(content)
    assert fm == {"title": "My Page", "description": "A test page."}
    assert body.strip() == "Body content here."


def test_extract_front_matter_missing_returns_empty_dict() -> None:
    """Content without front matter returns empty dict and the full content as body."""
    content = "# Just a heading\n\nSome body text."
    fm, body = extract_front_matter(content)
    assert fm == {}
    assert body == content


def test_extract_front_matter_malformed_yaml_returns_empty_dict() -> None:
    """Malformed YAML in front matter returns empty dict; body is content after the block."""
    content = "---\ntitle: [unclosed bracket\n---\n\nBody here."
    fm, body = extract_front_matter(content)
    assert fm == {}
    # Body should be what follows the front matter delimiters
    assert "Body here." in body


def test_extract_front_matter_empty_block() -> None:
    """An empty front matter block returns empty dict and original body."""
    content = "---\n---\n\nBody content."
    fm, body = extract_front_matter(content)
    assert fm == {}
    assert "Body content." in body


# ── _construct_url ─────────────────────────────────────────────────────────────


def test_construct_url_with_docs_subdir() -> None:
    """URL includes docs subdirectory and relative path."""
    url = _construct_url("terraform-provider-aws", "website/docs", "r/instance.html.markdown")
    assert url == "https://github.com/hashicorp/terraform-provider-aws/blob/main/website/docs/r/instance.html.markdown"


def test_construct_url_without_docs_subdir() -> None:
    """URL uses relative path directly when no docs subdirectory."""
    url = _construct_url("terraform-sentinel-policies", "", "some/policy.md")
    assert url == "https://github.com/hashicorp/terraform-sentinel-policies/blob/main/some/policy.md"


def test_construct_url_empty_repo_name() -> None:
    """Empty repo name returns empty string."""
    assert _construct_url("", "docs", "file.md") == ""


# ── _infer_doc_category ──────────────────────────────────────────────────────


def test_infer_doc_category_resource() -> None:
    """Files under r/ are resource references."""
    assert _infer_doc_category("r/instance.html.markdown") == "resource-reference"


def test_infer_doc_category_data_source() -> None:
    """Files under d/ are data source references."""
    assert _infer_doc_category("d/ami.html.markdown") == "data-source-reference"


def test_infer_doc_category_guide() -> None:
    """Files with 'guide' in path are guides."""
    assert _infer_doc_category("guides/getting-started/intro.md") == "guide"


def test_infer_doc_category_cli() -> None:
    """Files with 'commands' in path are CLI references."""
    assert _infer_doc_category("commands/apply.md") == "cli-reference"


def test_infer_doc_category_default() -> None:
    """Unrecognised paths default to 'documentation'."""
    assert _infer_doc_category("overview.md") == "documentation"


def test_infer_doc_category_api() -> None:
    """Files with 'api' in path are API references."""
    assert _infer_doc_category("api-docs/secret.md") == "api-reference"


def test_infer_doc_category_upgrade() -> None:
    """Files with 'upgrade' in path are upgrade guides."""
    assert _infer_doc_category("upgrade/v2.md") == "upgrade-guide"


# ── _infer_resource_type ─────────────────────────────────────────────────────


def test_infer_resource_type_resource() -> None:
    """Resource file yields product_filename."""
    assert _infer_resource_type("aws", "provider", "r/instance.html.markdown") == "aws_instance"


def test_infer_resource_type_data_source() -> None:
    """Data source file yields product_filename."""
    assert _infer_resource_type("google", "provider", "d/compute_instance.html.markdown") == "google_compute_instance"


def test_infer_resource_type_non_provider() -> None:
    """Non-provider source types return empty string."""
    assert _infer_resource_type("terraform", "documentation", "r/instance.md") == ""


def test_infer_resource_type_no_r_or_d_dir() -> None:
    """Provider files not under r/ or d/ return empty string."""
    assert _infer_resource_type("aws", "provider", "guides/intro.md") == ""


# ── _format_compact_header ───────────────────────────────────────────────────


def test_format_compact_header_uses_resource_type() -> None:
    """Compact header prefers resource_type as the label, with [source:product] prefix."""
    metadata = {"source_type": "provider", "product": "aws", "resource_type": "aws_instance"}
    result = _format_compact_header(metadata)
    assert result == "[provider:aws] aws_instance\n\n"


def test_format_compact_header_falls_back_to_title() -> None:
    """When resource_type is empty, the title is used as the label."""
    metadata = {"source_type": "documentation", "product": "vault", "title": "Getting Started"}
    result = _format_compact_header(metadata)
    assert result == "[documentation:vault] Getting Started\n\n"


def test_format_compact_header_appends_section_title() -> None:
    """A distinct section_title is appended after an em dash."""
    metadata = {
        "source_type": "provider",
        "product": "aws",
        "resource_type": "aws_instance",
        "section_title": "Argument Reference",
    }
    result = _format_compact_header(metadata)
    assert result == "[provider:aws] aws_instance — Argument Reference\n\n"


# ── split_into_sections ──────────────────────────────────────────────────────


def test_split_into_sections_no_headings() -> None:
    """Body without headings returns a single section."""
    body = "Just some text without any headings.\n\nAnother paragraph."
    sections = split_into_sections(body)
    assert len(sections) == 1
    assert sections[0][0] == ""
    assert "Just some text" in sections[0][1]


def test_split_into_sections_multiple_headings() -> None:
    """Body with multiple ## headings splits into sections."""
    body = (
        "Preamble text here.\n\n"
        "## Section One\n\n" + "A" * 300 + "\n\n"
        "## Section Two\n\n" + "B" * 300 + "\n\n"
        "## Section Three\n\n" + "C" * 300
    )
    sections = split_into_sections(body)
    assert len(sections) >= 3
    # Each section should contain its heading.
    titles = [s[0] for s in sections]
    assert "Section One" in titles
    assert "Section Two" in titles
    assert "Section Three" in titles


def test_split_into_sections_merges_small() -> None:
    """Sections smaller than MIN_SECTION_SIZE are merged with the previous."""
    body = (
        "## Big Section\n\n" + "X" * 300 + "\n\n"
        "## Tiny\n\nSmall.\n\n"
        "## Another Big\n\n" + "Y" * 300
    )
    sections = split_into_sections(body)
    # "Tiny" section is too small, should be merged.
    titles = [s[0] for s in sections]
    assert "Tiny" not in titles
    # The merged content should appear in the Big Section body.
    big_body = next(b for t, b in sections if t == "Big Section")
    assert "Tiny" in big_body


def test_split_into_sections_preserves_heading_in_body() -> None:
    """Each section body starts with its own heading for self-containment."""
    body = "## My Section\n\n" + "Z" * 300
    sections = split_into_sections(body)
    assert sections[0][1].startswith("## My Section")


def test_split_into_sections_h3_headings() -> None:
    """### headings also trigger section splits."""
    body = (
        "### Sub One\n\n" + "A" * 300 + "\n\n"
        "### Sub Two\n\n" + "B" * 300
    )
    sections = split_into_sections(body)
    assert len(sections) == 2


# ── process_file ───────────────────────────────────────────────────────────────


def test_process_file_with_front_matter(tmp_path: Path) -> None:
    """A file with valid front matter returns metadata dict and preserved body."""
    md = tmp_path / "test.md"
    body = "A" * 150  # Long enough body
    md.write_text(f"---\ntitle: Test Title\ndescription: A description.\n---\n\n{body}")

    result = process_file(str(md), "documentation", "terraform", "my-repo")

    assert result is not None
    metadata, content = result
    assert metadata["source_type"] == "documentation"
    assert metadata["product"] == "terraform"
    assert metadata["repo"] == "my-repo"
    assert metadata["title"] == "Test Title"
    assert metadata["description"] == "A description."
    assert body in content


def test_process_file_too_short_returns_none(tmp_path: Path) -> None:
    """A file with body content shorter than 100 chars returns None."""
    md = tmp_path / "short.md"
    md.write_text("---\ntitle: Short\n---\n\nToo short.")

    result = process_file(str(md), "documentation", "terraform", "my-repo")

    assert result is None


def test_process_file_no_front_matter(tmp_path: Path) -> None:
    """A file with no front matter is processed using the filename as title."""
    md = tmp_path / "no_front_matter.md"
    body = "B" * 200
    md.write_text(f"# Heading\n\n{body}")

    result = process_file(str(md), "provider", "aws", "terraform-provider-aws")

    assert result is not None
    metadata, _content = result
    assert metadata["source_type"] == "provider"
    assert metadata["product"] == "aws"
    # Title falls back to filename stem.
    assert metadata["title"] == "no_front_matter"


def test_process_file_strips_navigation_keys(tmp_path: Path) -> None:
    """Navigation-only keys (layout, sidebar_current) are not in the output metadata."""
    md = tmp_path / "nav.md"
    body = "C" * 200
    md.write_text(
        f"---\ntitle: Nav Page\nlayout: docs\nsidebar_current: my-section\n---\n\n{body}"
    )

    result = process_file(str(md), "documentation", "vault", "vault")

    assert result is not None
    metadata, _content = result
    assert "layout" not in metadata
    assert "sidebar_current" not in metadata
    assert metadata["title"] == "Nav Page"


def test_process_file_enriched_metadata(tmp_path: Path) -> None:
    """process_file returns product_family, url, doc_category, and resource_type."""
    md = tmp_path / "instance.html.md"
    body = "D" * 200
    md.write_text(f"---\ntitle: aws_instance\n---\n\n{body}")

    result = process_file(
        str(md), "provider", "aws", "terraform-provider-aws",
        product_family="terraform",
        docs_subdir="website/docs",
        relative_path="r/instance.html.markdown",
    )

    assert result is not None
    metadata, _content = result
    assert metadata["product_family"] == "terraform"
    assert "terraform-provider-aws/blob/main/website/docs/r/instance.html.markdown" in metadata["url"]
    assert metadata["doc_category"] == "resource-reference"
    assert metadata["resource_type"] == "aws_instance"


# ── process_directory ─────────────────────────────────────────────────────────


def test_process_directory_only_processes_markdown(tmp_path: Path) -> None:
    """Only .md and .mdx files are processed; .txt and other files are ignored."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    body = "D" * 200
    (input_dir / "page.md").write_text(f"---\ntitle: MD\n---\n\n{body}")
    (input_dir / "page.mdx").write_text(f"---\ntitle: MDX\n---\n\n{body}")
    (input_dir / "ignored.txt").write_text("This should be ignored.")
    (input_dir / "ignored.json").write_text("{}")

    count = process_directory(str(input_dir), str(output_dir), "documentation", "terraform", "tf")

    assert count == 2
    assert not (tmp_path / "output" / "ignored.txt").exists()


def test_process_directory_returns_correct_count(tmp_path: Path) -> None:
    """process_directory returns the number of successfully processed files."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    body = "E" * 200
    for i in range(5):
        (input_dir / f"doc_{i}.md").write_text(f"---\ntitle: Doc {i}\n---\n\n{body}")

    count = process_directory(str(input_dir), str(output_dir), "documentation", "consul", "consul")

    assert count == 5


def test_process_directory_skips_short_files(tmp_path: Path) -> None:
    """Files with bodies shorter than 100 chars are not counted or written."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    body = "F" * 200
    (input_dir / "valid.md").write_text(f"---\ntitle: Valid\n---\n\n{body}")
    (input_dir / "short.md").write_text("---\ntitle: Short\n---\n\nToo short.")

    count = process_directory(str(input_dir), str(output_dir), "documentation", "nomad", "nomad")

    assert count == 1


def test_process_directory_semantic_sections(tmp_path: Path) -> None:
    """Documents with headings are split into separate section files."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    body = (
        "Intro text.\n\n"
        "## Section One\n\n" + "A" * 300 + "\n\n"
        "## Section Two\n\n" + "B" * 300
    )
    (input_dir / "multi.md").write_text(f"---\ntitle: Multi\n---\n\n{body}")

    count = process_directory(str(input_dir), str(output_dir), "documentation", "vault", "vault")

    # Should produce multiple section files.
    assert count >= 2
    output_files = list(Path(output_dir).rglob("*.md"))
    assert len(output_files) >= 2
    # Section files should carry the compact attribution prefix.
    for f in output_files:
        text = f.read_text()
        assert text.startswith("[documentation:vault]")


def test_process_directory_single_section_preserves_structure(tmp_path: Path) -> None:
    """A document with no headings keeps its original relative path."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    sub = input_dir / "guides"
    sub.mkdir(parents=True)

    body = "G" * 200
    (sub / "intro.md").write_text(f"---\ntitle: Intro\n---\n\n{body}")

    process_directory(str(input_dir), str(output_dir), "documentation", "vault", "vault")

    assert (output_dir / "guides" / "intro.md").exists()


def test_process_directory_mixed_extensions(tmp_path: Path) -> None:
    """Both .md and .mdx files are processed and written as .md in output."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    body = "H" * 200
    (input_dir / "page.md").write_text(f"---\ntitle: MD Page\n---\n\n{body}")
    (input_dir / "page2.mdx").write_text(f"---\ntitle: MDX Page\n---\n\n{body}")

    count = process_directory(str(input_dir), str(output_dir), "provider", "google", "terraform-provider-google")

    assert count == 2


def test_process_directory_writes_compact_attribution_prefix(tmp_path: Path) -> None:
    """Output files start with the compact [source:product] attribution prefix."""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    body = "I" * 200
    (input_dir / "page.md").write_text(f"---\ntitle: Page\n---\n\n{body}")

    process_directory(
        str(input_dir), str(output_dir), "provider", "aws", "terraform-provider-aws",
        product_family="terraform",
    )

    output_files = list(Path(output_dir).rglob("*.md"))
    assert len(output_files) >= 1
    text = output_files[0].read_text()
    assert text.startswith("[provider:aws]")


class TestSplitLargeSection:
    """Tests for _split_large_section."""

    def test_small_section_unchanged(self) -> None:
        result = _split_large_section("Title", "Short body", max_chars=4000)
        assert result == [("Title", "Short body")]

    def test_large_section_without_code_unchanged(self) -> None:
        body = "A" * 5000
        result = _split_large_section("Title", body, max_chars=4000)
        assert len(result) == 1  # No code fences to split at

    def test_large_section_splits_between_code_blocks(self) -> None:
        block1 = "```hcl\nresource \"aws_instance\" \"example\" {\n  ami = \"abc\"\n}\n```"
        block2 = "```hcl\nresource \"aws_vpc\" \"main\" {\n  cidr_block = \"10.0.0.0/16\"\n}\n```"
        prose = "A" * 2000
        body = f"{prose}\n\n{block1}\n\n{prose}\n\n{block2}\n\n{prose}"
        result = _split_large_section("Example", body, max_chars=3000)
        assert len(result) >= 2
        # Each part should not contain a partial code fence.
        for _, part_body in result:
            fence_count = part_body.count("```")
            assert fence_count % 2 == 0, f"Odd number of fences: {fence_count}"

    def test_preserves_heading_in_subsequent_parts(self) -> None:
        block = "```\ncode\n```"
        prose = "B" * 3000
        body = f"{block}\n\n{prose}\n\n{block}\n\n{prose}"
        result = _split_large_section("Usage", body, max_chars=2000)
        if len(result) > 1:
            assert result[1][1].startswith("## Usage")
