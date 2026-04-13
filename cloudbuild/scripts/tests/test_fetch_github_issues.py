"""Tests for fetch_github_issues.py."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetch_github_issues import _get_product_family, _has_maintainer_response, _is_pull_request, _is_useful, format_issue


class TestIsPullRequest:
    """Tests for the _is_pull_request helper."""

    def test_is_pull_request_true(self) -> None:
        issue = {"number": 1, "pull_request": {"url": "https://..."}}
        assert _is_pull_request(issue) is True

    def test_is_pull_request_false(self) -> None:
        issue = {"number": 1, "title": "A real issue"}
        assert _is_pull_request(issue) is False


class TestFormatIssue:
    """Tests for format_issue."""

    def _make_issue(self, **overrides: object) -> dict:
        base = {
            "number": 42,
            "title": "Something is broken",
            "state": "open",
            "body": "This is the issue body with enough content to pass the length check easily. Adding more text to exceed the one hundred character threshold.",
            "labels": [{"name": "bug"}, {"name": "terraform"}],
            "user": {"login": "testuser"},
            "created_at": "2025-06-15T10:00:00Z",
            "updated_at": "2025-06-20T10:00:00Z",
            "comments_url": "https://api.github.com/repos/hashicorp/terraform/issues/42/comments",
        }
        base.update(overrides)
        return base

    def test_format_issue_basic(self) -> None:
        issue = self._make_issue()
        result = format_issue(issue, [], "terraform", "terraform")

        assert result.startswith("[issue:terraform] #42 (open): Something is broken")
        assert "# Something is broken" in result
        assert "This is the issue body" in result

    def test_format_issue_with_comments(self) -> None:
        issue = self._make_issue()
        comments = [
            {
                "user": {"login": "helper"},
                "created_at": "2025-06-16T10:00:00Z",
                "body": "Have you tried restarting?",
            },
        ]
        result = format_issue(issue, comments, "terraform", "terraform")

        assert "## Comments" in result
        assert "Have you tried restarting?" in result

    def test_format_issue_empty_body(self) -> None:
        issue = self._make_issue(body="")
        result = format_issue(issue, [], "terraform", "terraform")

        # Should still produce the compact prefix + heading.
        assert result.startswith("[issue:terraform]")

    def test_format_issue_closed_state(self) -> None:
        issue = self._make_issue(state="closed")
        result = format_issue(issue, [], "aws", "terraform-provider-aws")

        assert "[issue:aws]" in result
        assert "(closed)" in result

    def test_format_issue_truncates_comments(self) -> None:
        issue = self._make_issue()
        comments = [
            {"user": {"login": f"user{i}"}, "created_at": "2025-06-16T10:00:00Z", "body": f"Comment {i}"}
            for i in range(15)
        ]
        result = format_issue(issue, comments, "terraform", "terraform")

        # MAX_COMMENTS is 10, so comments 10-14 should not appear.
        assert "Comment 9" in result
        assert "Comment 10" not in result

    def test_compact_prefix_format(self) -> None:
        issue = self._make_issue()
        result = format_issue(issue, [], "vault", "vault")

        assert result.startswith("[issue:vault] #42 (open): Something is broken\n\n")

    def test_format_issue_provider_compact_prefix(self) -> None:
        issue = self._make_issue()
        result = format_issue(issue, [], "aws", "terraform-provider-aws")

        assert result.startswith("[issue:aws] #42 (open): Something is broken")


class TestGetProductFamily:
    """Tests for _get_product_family helper."""

    def test_core_product(self) -> None:
        assert _get_product_family("terraform", "terraform") == "terraform"

    def test_provider_repo(self) -> None:
        assert _get_product_family("terraform-provider-aws", "aws") == "terraform"

    def test_sentinel_repo(self) -> None:
        assert _get_product_family("terraform-sentinel-policies", "sentinel") == "terraform"

    def test_vault_core(self) -> None:
        assert _get_product_family("vault", "vault") == "vault"


class TestIsUseful:
    """Tests for _is_useful quality filter."""

    def _make_issue(self, labels: list[str] | None = None, **overrides: object) -> dict:
        issue = {
            "number": 1,
            "title": "Test",
            "body": "A" * 150,
            "comments": 2,
            "labels": [{"name": label} for label in (labels or [])],
            "state": "open",
            "user": {"login": "test"},
            "created_at": "2025-01-01T00:00:00Z",
        }
        issue.update(overrides)
        return issue

    def test_useful_issue_passes(self) -> None:
        assert _is_useful(self._make_issue(labels=["bug"]), min_comments=1)

    def test_stale_only_rejected(self) -> None:
        assert not _is_useful(self._make_issue(labels=["stale"]), min_comments=1)

    def test_wontfix_only_rejected(self) -> None:
        assert not _is_useful(self._make_issue(labels=["wontfix"]), min_comments=1)

    def test_duplicate_only_rejected(self) -> None:
        assert not _is_useful(self._make_issue(labels=["duplicate"]), min_comments=1)

    def test_mixed_labels_kept(self) -> None:
        assert _is_useful(self._make_issue(labels=["stale", "bug"]), min_comments=1)

    def test_no_labels_kept(self) -> None:
        assert _is_useful(self._make_issue(labels=[]), min_comments=1)

    def test_pull_request_rejected(self) -> None:
        issue = self._make_issue()
        issue["pull_request"] = {"url": "..."}
        assert not _is_useful(issue, min_comments=1)

    def test_short_body_rejected(self) -> None:
        assert not _is_useful(self._make_issue(body="short"), min_comments=1)

    def test_low_comments_rejected(self) -> None:
        assert not _is_useful(self._make_issue(comments=0), min_comments=1)


class TestResolutionQuality:
    """Tests for maintainer detection and resolution quality."""

    def test_maintainer_by_username(self) -> None:
        comments = [{"user": {"login": "hashicorp-copywrite"}, "author_association": "NONE"}]
        assert _has_maintainer_response(comments)

    def test_maintainer_by_association(self) -> None:
        comments = [{"user": {"login": "someuser"}, "author_association": "MEMBER"}]
        assert _has_maintainer_response(comments)

    def test_no_maintainer(self) -> None:
        comments = [{"user": {"login": "randomuser"}, "author_association": "NONE"}]
        assert not _has_maintainer_response(comments)

    def test_empty_comments(self) -> None:
        assert not _has_maintainer_response([])
