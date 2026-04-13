"""Tests for deduplicate.py."""

import sys
from pathlib import Path

# Allow imports from the parent scripts directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deduplicate import _content_hash, _extract_body, _normalise, deduplicate


class TestExtractBody:
    """Tests for _extract_body."""

    def test_with_header(self) -> None:
        content = "source_type: docs\nproduct: vault\n\n# Title\n\nBody text here."
        assert _extract_body(content) == "# Title\n\nBody text here."

    def test_without_header(self) -> None:
        content = "Just a body with no metadata."
        assert _extract_body(content) == "Just a body with no metadata."

    def test_empty(self) -> None:
        assert _extract_body("") == ""


class TestNormalise:
    """Tests for _normalise."""

    def test_collapses_whitespace(self) -> None:
        assert _normalise("hello   world\n\nfoo") == "hello world foo"

    def test_lowercases(self) -> None:
        assert _normalise("Hello World") == "hello world"


class TestDeduplicate:
    """Tests for deduplicate."""

    def test_removes_exact_duplicate(self, tmp_path: Path) -> None:
        body = "A" * 200
        (tmp_path / "a.md").write_text(f"source_type: docs\n\n{body}")
        (tmp_path / "b.md").write_text(f"source_type: issue\n\n{body}")
        total, removed = deduplicate(tmp_path)
        assert total == 2
        assert removed == 1
        assert (tmp_path / "a.md").exists()
        assert not (tmp_path / "b.md").exists()

    def test_keeps_unique_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text(f"source_type: docs\n\n{'A' * 200}")
        (tmp_path / "b.md").write_text(f"source_type: docs\n\n{'B' * 200}")
        total, removed = deduplicate(tmp_path)
        assert total == 2
        assert removed == 0

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        body = "C" * 200
        (tmp_path / "a.md").write_text(f"source_type: docs\n\n{body}")
        (tmp_path / "b.md").write_text(f"source_type: docs\n\n{body}")
        total, removed = deduplicate(tmp_path, dry_run=True)
        assert removed == 1
        assert (tmp_path / "b.md").exists()  # Not deleted in dry-run

    def test_skips_short_bodies(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("source_type: docs\n\nshort")
        (tmp_path / "b.md").write_text("source_type: docs\n\nshort")
        total, removed = deduplicate(tmp_path)
        assert removed == 0

    def test_whitespace_normalisation_catches_duplicates(self, tmp_path: Path) -> None:
        base = "X" * 200
        (tmp_path / "a.md").write_text(f"source_type: docs\n\n{base}")
        (tmp_path / "b.md").write_text(f"source_type: issue\n\n  {base}  \n\n")
        total, removed = deduplicate(tmp_path)
        assert removed == 1

    def test_different_metadata_same_body_deduped(self, tmp_path: Path) -> None:
        body = "Y" * 200
        (tmp_path / "a.md").write_text(f"source_type: docs\nproduct: vault\n\n{body}")
        (tmp_path / "b.md").write_text(f"source_type: blog\nproduct: terraform\n\n{body}")
        total, removed = deduplicate(tmp_path)
        assert removed == 1
