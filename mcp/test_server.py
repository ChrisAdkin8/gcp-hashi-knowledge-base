#!/usr/bin/env python3
"""Smoke-test for the MCP server tool functions.

Invokes the docs (Vertex AI) and graph (Spanner) tools directly, bypassing
the MCP protocol, to confirm that environment variables are set correctly
and both backends return results.

Usage (run from the repository root):
    VERTEX_PROJECT=my-project \
    VERTEX_REGION=us-west1 \
    VERTEX_CORPUS_ID=6917529027641081856 \
    SPANNER_INSTANCE=hashicorp-rag-graph \
    SPANNER_DATABASE=tf-graph \
    .venv/bin/python3 mcp/test_server.py

Or via Task:
    task mcp:test CORPUS_ID=6917529027641081856

Graph tool tests are skipped (not failed) when SPANNER_INSTANCE is unset,
so the suite remains green for docs-only deployments.

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

import os
import sys

# Resolve the server module relative to this file to avoid any import
# path ambiguity when run from outside the mcp/ directory.
import importlib.util
from pathlib import Path

_server_path = Path(__file__).parent / "server.py"
_spec = importlib.util.spec_from_file_location("rag_server", _server_path)
assert _spec and _spec.loader, f"Cannot find server at {_server_path}"
_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_server)  # type: ignore[union-attr]

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"

all_passed = True


_ERROR_PREFIXES = ("Configuration error:", "Retrieval error:", "Error:", "Spanner query failed:")


def check(label: str, result: str, expect_contains: str | None = None) -> None:
    global all_passed
    # Only treat the result as an error when the server returns an explicit
    # error prefix — don't scan the whole body, which may contain "error"
    # as a normal word inside retrieved document text.
    stripped = result.strip()
    if any(stripped.startswith(p) for p in _ERROR_PREFIXES):
        print(f"[{FAIL}] {label}")
        print(f"       {result[:200]}")
        all_passed = False
        return
    if expect_contains and expect_contains.lower() not in result.lower():
        print(f"[{FAIL}] {label} — expected '{expect_contains}' in output")
        print(f"       {result[:200]}")
        all_passed = False
        return
    print(f"[{PASS}] {label}")


def skip(label: str, reason: str) -> None:
    print(f"[{SKIP}] {label} — {reason}")


# ── 1. Corpus info ─────────────────────────────────────────────────────────────
print("=" * 60)
print("Test 1: get_corpus_info")
print("=" * 60)
info = _server.get_corpus_info()
print(info)
check("corpus info — project set", info, os.environ.get("VERTEX_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT"))
check("corpus info — corpus ID set", info, os.environ.get("VERTEX_CORPUS_ID", ""))

# ── 2. Basic search ────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test 2: search_hashicorp_docs — basic query")
print("=" * 60)
result = _server.search_hashicorp_docs(
    query="How do I configure the AWS Terraform provider?",
    top_k=3,
)
print(result[:600])
check("basic search returns results", result, "Result 1")

# ── 3. Filtered search (product family) ───────────────────────────────────────
print()
print("=" * 60)
print("Test 3: search_hashicorp_docs — filtered by product_family=vault")
print("=" * 60)
result_vault = _server.search_hashicorp_docs(
    query="How do I enable the PKI secrets engine in Vault?",
    top_k=3,
    distance_threshold=0.5,
    product_family="vault",
)
print(result_vault[:600])
check("filtered search returns results", result_vault, "Result 1")

# ── 4. No-results case ─────────────────────────────────────────────────────────
print()
print("=" * 60)
print("Test 4: search_hashicorp_docs — query with no expected results")
print("=" * 60)
result_empty = _server.search_hashicorp_docs(
    query="xyzzy_nonexistent_token_for_testing",
    top_k=1,
    distance_threshold=0.1,
)
print(result_empty)
check("no-results returns friendly message", result_empty, "No results found")

# ── 5. Spanner Graph: get_graph_info ───────────────────────────────────────────
print()
print("=" * 60)
print("Test 5: get_graph_info")
print("=" * 60)
if not os.environ.get("SPANNER_INSTANCE"):
    skip("get_graph_info", "SPANNER_INSTANCE not set (graph backend not deployed)")
else:
    info = _server.get_graph_info()
    print(info)
    check("graph info — instance set", info, os.environ.get("SPANNER_INSTANCE", ""))
    check("graph info — database set", info, os.environ.get("SPANNER_DATABASE", "tf-graph"))

# ── 6. Spanner Graph: find_resources_by_type ───────────────────────────────────
print()
print("=" * 60)
print("Test 6: find_resources_by_type — google_storage_bucket")
print("=" * 60)
if not os.environ.get("SPANNER_INSTANCE"):
    skip("find_resources_by_type", "SPANNER_INSTANCE not set")
else:
    result = _server.find_resources_by_type(
        resource_type="google_storage_bucket",
        limit=10,
    )
    print(result[:600])
    # Either a "Found N" line or a friendly empty-result message — both
    # indicate the tool ran without raising. Treat both as a pass.
    if result.strip().startswith("No resources of type"):
        check("find_resources_by_type — clean empty result", result, "Check that the graph")
    else:
        check("find_resources_by_type — returns rows", result, "Found")

# ── 7. Spanner Graph: get_resource_dependencies ────────────────────────────────
print()
print("=" * 60)
print("Test 7: get_resource_dependencies — both directions, depth 1")
print("=" * 60)
if not os.environ.get("SPANNER_INSTANCE"):
    skip("get_resource_dependencies", "SPANNER_INSTANCE not set")
else:
    result = _server.get_resource_dependencies(
        resource_type="google_storage_bucket",
        resource_name="rag_docs",
        direction="both",
        max_depth=1,
    )
    print(result[:600])
    if result.strip().startswith("No dependencies found"):
        check("get_resource_dependencies — clean empty result", result, "Check that the graph")
    else:
        check("get_resource_dependencies — returns walk", result, "Dependency walk")

# ── Summary ────────────────────────────────────────────────────────────────────
print()
print("=" * 60)
if all_passed:
    print("All tests passed.")
    sys.exit(0)
else:
    print("One or more tests failed.")
    sys.exit(1)
