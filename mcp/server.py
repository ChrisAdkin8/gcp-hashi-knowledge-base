#!/usr/bin/env python3
"""MCP server exposing the HashiCorp knowledge base on GCP.

Implements five tools across two backends:
  Vertex AI RAG corpus
    - search_hashicorp_docs       — semantic search with metadata filters
    - get_corpus_info             — inspect active corpus configuration
  Spanner Graph (tf_graph property graph)
    - get_resource_dependencies   — traverse Terraform resource dependencies
    - find_resources_by_type      — list resources of a given type
    - get_graph_info              — inspect graph store configuration + counts

Environment variables:
    VERTEX_PROJECT          — GCP project ID (also accepts GOOGLE_CLOUD_PROJECT)
    VERTEX_REGION           — GCP region (default: us-west1)
    VERTEX_CORPUS_ID        — Vertex AI RAG corpus numeric ID
    SPANNER_INSTANCE        — Spanner instance hosting the graph database
    SPANNER_DATABASE        — Spanner database name (default: tf-graph)

Authentication uses Google Application Default Credentials (ADC).
Run `gcloud auth application-default login` before starting the server.
"""

import hashlib
import logging
import os

# Spanner Python client v3.49+ auto-enables OpenTelemetry metrics export, but
# local / Cloud Build runs lack the required resource labels (instance_id),
# causing noisy 400 errors against Cloud Monitoring.  Disable built-in metrics.
os.environ.setdefault("GOOGLE_CLOUD_SPANNER_ENABLE_BUILTIN_METRICS", "false")
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

PROJECT_ID: str = os.environ.get("VERTEX_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
REGION: str = os.environ.get("VERTEX_REGION", "us-west1")
CORPUS_ID: str = os.environ.get("VERTEX_CORPUS_ID", "")
SPANNER_INSTANCE: str = os.environ.get("SPANNER_INSTANCE", "")
SPANNER_DATABASE: str = os.environ.get("SPANNER_DATABASE", "tf-graph")

_vertexai_initialized: bool = False
_spanner_database: Any = None


def _init_vertexai() -> None:
    """Initialise the Vertex AI SDK (lazy, called on first tool use)."""
    global _vertexai_initialized
    if _vertexai_initialized:
        return
    if not PROJECT_ID:
        raise RuntimeError(
            "VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT environment variable is required."
        )
    if not CORPUS_ID:
        raise RuntimeError("VERTEX_CORPUS_ID environment variable is required.")
    import vertexai  # type: ignore[import]

    vertexai.init(project=PROJECT_ID, location=REGION)
    _vertexai_initialized = True


def _corpus_resource_name() -> str:
    return f"projects/{PROJECT_ID}/locations/{REGION}/ragCorpora/{CORPUS_ID}"


def _get_spanner_database() -> Any:
    """Lazy-init the Spanner database client. Raises RuntimeError on misconfig."""
    global _spanner_database
    if _spanner_database is not None:
        return _spanner_database
    if not PROJECT_ID:
        raise RuntimeError(
            "VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT environment variable is required."
        )
    if not SPANNER_INSTANCE:
        raise RuntimeError("SPANNER_INSTANCE environment variable is required.")
    if not SPANNER_DATABASE:
        raise RuntimeError("SPANNER_DATABASE environment variable is required.")
    from google.cloud import spanner  # type: ignore[attr-defined]

    client = spanner.Client(project=PROJECT_ID)
    instance = client.instance(SPANNER_INSTANCE)
    _spanner_database = instance.database(SPANNER_DATABASE)
    return _spanner_database


def _spanner_query(sql: str, params: dict | None = None, param_types: dict | None = None) -> list[list[Any]]:
    """Execute a read-only SQL query against the Spanner graph database.

    Returns a list of rows (each row is a list of column values). Raises
    RuntimeError on configuration error and re-raises Spanner exceptions.
    """
    database = _get_spanner_database()
    with database.snapshot() as snapshot:
        result = snapshot.execute_sql(sql, params=params, param_types=param_types)
        return [list(row) for row in result]


def _extract_uri_metadata(source_uri: str) -> dict[str, str]:
    """Infer product, product_family, and source_type from the GCS object path.

    URI structure (after the bucket prefix):
      provider/terraform-provider-{product}/...  → product_family=terraform, source_type=provider
      documentation/{product}/...                → product_family={product}, source_type=documentation
      issues/{product}/...                        → product_family={product}, source_type=issue
      module/{name}/...                           → product_family=terraform, source_type=module
      sentinel/{name}/...                         → product_family=sentinel, source_type=sentinel
      blogs/{...}                                 → source_type=blog
      discuss/{...}                               → source_type=discuss

    Falls back to empty strings when the pattern is unrecognised.
    """
    # Strip the gs://bucket/ prefix to get just the object path.
    path = source_uri
    if path.startswith("gs://"):
        parts = path.split("/", 3)  # ["gs:", "", "bucket", "rest"]
        path = parts[3] if len(parts) > 3 else ""

    segments = path.split("/")
    top = segments[0] if segments else ""

    if top == "provider":
        # e.g. provider/terraform-provider-vault/...
        repo = segments[1] if len(segments) > 1 else ""
        # repo looks like "terraform-provider-{product}"
        product = repo.replace("terraform-provider-", "") if repo.startswith("terraform-provider-") else repo
        return {"product": product, "product_family": "terraform", "source_type": "provider"}

    if top == "documentation":
        product = segments[1] if len(segments) > 1 else ""
        return {"product": product, "product_family": product, "source_type": "documentation"}

    if top == "issues":
        product = segments[1] if len(segments) > 1 else ""
        return {"product": product, "product_family": product, "source_type": "issue"}

    if top == "module":
        return {"product": "terraform", "product_family": "terraform", "source_type": "module"}

    if top == "sentinel":
        return {"product": "sentinel", "product_family": "sentinel", "source_type": "sentinel"}

    if top in ("blogs", "blog"):
        return {"product": "", "product_family": "", "source_type": "blog"}

    if top == "discuss":
        return {"product": "", "product_family": "", "source_type": "discuss"}

    return {"product": "", "product_family": "", "source_type": ""}


def _short_source_uri(source_uri: str) -> str:
    """Return a compact, human-readable path from a full GCS URI.

    Strips the ``gs://bucket/`` prefix, leaving only the object path.
    Falls back to the original string when the URI is not a GCS path.
    """
    if source_uri.startswith("gs://"):
        parts = source_uri.split("/", 3)  # ["gs:", "", "bucket", "rest"]
        if len(parts) > 3:
            return parts[3]
    return source_uri


def _strip_chunk_header(text: str) -> str:
    """Remove the compact metadata header prefix injected by process_docs.py.

    The header (e.g. ``[provider:aws] aws_instance — Arguments\\n\\n``) is
    already conveyed by the source URI, so repeating it in every chunk wastes
    tokens.
    """
    return re.sub(r'^\[[\w./-]+:[\w./-]*\]\s+.*?\n\n', '', text, count=1)


def _content_fingerprint(text: str) -> str:
    """Return a short hash of normalised text for near-duplicate detection."""
    normalised = re.sub(r'\s+', ' ', text.lower()).strip()
    return hashlib.sha256(normalised.encode('utf-8')).hexdigest()[:16]


def _matches_metadata(
    source_uri: str,
    product: str | None,
    product_family: str | None,
    source_type: str | None,
) -> bool:
    """Return True if the chunk's source URI satisfies all active filters.

    Uses URI path structure rather than chunk text because Vertex AI returns
    arbitrary document chunks — only the first chunk of each file contains
    the metadata header, so text-based filtering rejects most valid results.
    """
    if not any([product, product_family, source_type]):
        return True
    meta = _extract_uri_metadata(source_uri)
    if product and meta.get("product", "").lower() != product.lower():
        return False
    if product_family and meta.get("product_family", "").lower() != product_family.lower():
        return False
    if source_type and meta.get("source_type", "").lower() != source_type.lower():
        return False
    return True


# ── MCP server ─────────────────────────────────────────────────────────────────

mcp = FastMCP(
    "hashicorp-rag",
    instructions=(
        "Two backends are exposed:\n"
        "  • Vertex AI RAG corpus — search HashiCorp documentation, providers, "
        "GitHub issues, Discuss threads, and blog posts. Use search_hashicorp_docs "
        "for any natural-language question about Terraform, Vault, Consul, Nomad, "
        "Packer, Sentinel, or Boundary; use get_corpus_info to inspect the corpus.\n"
        "  • Spanner Graph (tf_graph) — Terraform resource dependency graph for "
        "your own workspace repos. Use get_resource_dependencies to walk what a "
        "resource depends on (or what depends on it), find_resources_by_type to "
        "list every resource of a given Terraform type, and get_graph_info to "
        "inspect the graph store."
    ),
)


@mcp.tool()
def search_hashicorp_docs(
    query: str,
    top_k: int = 3,
    distance_threshold: float = 0.28,
    product: str | None = None,
    product_family: str | None = None,
    source_type: str | None = None,
) -> str:
    """Search the HashiCorp documentation corpus for relevant chunks.

    Retrieves semantically relevant documentation, GitHub issues, Discourse
    threads, and blog posts from the HashiCorp knowledge base indexed in
    Vertex AI RAG Engine.

    Args:
        query: Natural language question or topic to search for.
        top_k: Number of results to return. Range 1–20, default 3.
        distance_threshold: Relevance cutoff. Range 0.1–1.0, default 0.28.
            Lower values are stricter (only close matches). Raise to 0.5+
            for broader but less precise coverage.
        product: Filter by specific product name. Examples: "aws", "vault",
            "consul", "nomad", "packer", "terraform", "boundary", "waypoint".
        product_family: Filter by product family. One of: "terraform",
            "vault", "consul", "nomad", "packer", "boundary", "sentinel".
        source_type: Filter by document source type. One of: "provider",
            "documentation", "module", "sentinel", "issue", "discuss", "blog".

    Returns:
        Formatted string containing the matching document chunks with their
        relevance scores and source URIs, or an error message on failure.
    """
    try:
        _init_vertexai()
    except RuntimeError as exc:
        return f"Configuration error: {exc}"

    from vertexai import rag  # type: ignore[import]

    top_k = max(1, min(top_k, 20))
    distance_threshold = max(0.1, min(distance_threshold, 1.0))

    # Over-fetch when metadata filters are active so we have enough candidates
    # after post-retrieval filtering to return the requested top_k results.
    fetch_k = top_k * 3 if any([product, product_family, source_type]) else top_k

    try:
        response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=_corpus_resource_name())],
            text=query,
            rag_retrieval_config=rag.RagRetrievalConfig(
                top_k=fetch_k,
                filter=rag.Filter(vector_distance_threshold=distance_threshold),
                ranking=rag.Ranking(
                    rank_service=rag.RankService(
                        model_name="semantic-ranker-512@latest",
                    ),
                ),
            ),
        )
    except Exception as exc:
        logger.exception("retrieval_query failed for query: %s", query)
        return f"Retrieval error: {exc}"

    contexts: list[dict] = []
    if response.contexts and response.contexts.contexts:
        for ctx in response.contexts.contexts:
            raw_text: str = getattr(ctx, "text", "") or ""
            uri: str = getattr(ctx, "source_uri", "") or ""
            if _matches_metadata(uri, product, product_family, source_type):
                # Strip the metadata header — the source URI already identifies the doc.
                text = _strip_chunk_header(raw_text)
                contexts.append(
                    {
                        "source_uri": uri,
                        "score": getattr(ctx, "score", 0.0),
                        "text": text,
                    }
                )

    # Deduplicate by source URI — keep only the highest-scoring chunk per document.
    seen_uris: dict[str, int] = {}
    deduped: list[dict] = []
    for ctx in contexts:
        uri = ctx["source_uri"]
        if uri in seen_uris:
            existing_idx = seen_uris[uri]
            if ctx["score"] > deduped[existing_idx]["score"]:
                deduped[existing_idx] = ctx
        else:
            seen_uris[uri] = len(deduped)
            deduped.append(ctx)

    # Cross-document dedup: drop chunks with near-identical content from
    # different source URIs (e.g. the same example in a guide and a provider doc).
    seen_fingerprints: set[str] = set()
    unique: list[dict] = []
    for ctx in deduped:
        fp = _content_fingerprint(ctx["text"])
        if fp not in seen_fingerprints:
            seen_fingerprints.add(fp)
            unique.append(ctx)
    contexts = unique[:top_k]

    if not contexts:
        active_filters: list[str] = []
        if product:
            active_filters.append(f"product={product}")
        if product_family:
            active_filters.append(f"product_family={product_family}")
        if source_type:
            active_filters.append(f"source_type={source_type}")
        filter_note = f" with filters ({', '.join(active_filters)})" if active_filters else ""
        return f'No results found for: "{query}"{filter_note}'

    lines: list[str] = [f'Found {len(contexts)} result(s) for: "{query}"\n']
    for i, ctx in enumerate(contexts, 1):
        short_source = _short_source_uri(ctx["source_uri"])
        lines.append(f"[{i}] {short_source} ({ctx['score']:.2f})")
        lines.append(ctx["text"])
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def get_corpus_info() -> str:
    """Return configuration details of the active HashiCorp RAG corpus.

    Returns:
        Formatted string with project, region, corpus ID, resource name,
        and a reference to the available metadata filters.
    """
    if not PROJECT_ID:
        return "Error: VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT is not set."
    if not CORPUS_ID:
        return "Error: VERTEX_CORPUS_ID is not set."

    lines = [
        "HashiCorp RAG Corpus",
        "=" * 40,
        f"Project:       {PROJECT_ID}",
        f"Region:        {REGION}",
        f"Corpus ID:     {CORPUS_ID}",
        f"Resource name: {_corpus_resource_name()}",
        "",
        "Metadata filters available in search_hashicorp_docs:",
        "  product:        aws | vault | consul | nomad | packer | terraform | boundary | waypoint",
        "  product_family: terraform | vault | consul | nomad | packer | boundary | sentinel",
        "  source_type:    provider | documentation | module | sentinel | issue | discuss | blog",
        "",
        "Default retrieval settings:",
        "  top_k:              3  (range 1–20)",
        "  distance_threshold: 0.28  (range 0.1–1.0; lower = stricter)",
    ]
    return "\n".join(lines)


# ── Spanner Graph tools ────────────────────────────────────────────────────────


@mcp.tool()
def get_resource_dependencies(
    resource_type: str,
    resource_name: str,
    direction: str = "both",
    max_depth: int = 2,
    repo_uri: str | None = None,
) -> str:
    """Traverse the Terraform resource dependency graph in Spanner.

    Finds resources that a given resource depends on (downstream), resources
    that depend on it (upstream), or both. The graph is populated by the
    `terraform graph` ingestion pipeline (`task graph:populate`).

    Args:
        resource_type: Terraform resource type (e.g. "google_compute_instance",
            "aws_lambda_function").
        resource_name: Terraform resource name (e.g. "web", "processor").
        direction: "downstream" (what this resource depends on),
            "upstream" (what depends on it), or "both" (default).
        max_depth: Maximum traversal depth. Range 1–5, default 2.
        repo_uri: Optional — restrict traversal to a single repository.

    Returns:
        Formatted string listing the matching dependent resources, or an
        error / empty-result message.
    """
    try:
        _get_spanner_database()
    except RuntimeError as exc:
        return f"Configuration error: {exc}"

    from google.cloud.spanner_v1 import param_types as pt  # type: ignore[attr-defined]

    max_depth = min(max(1, int(max_depth)), 5)
    direction = direction.lower()
    if direction not in ("downstream", "upstream", "both"):
        return "Error: direction must be one of: downstream, upstream, both"

    address = f"{resource_type}.{resource_name}"

    # Build a recursive WITH that walks the DependsOn edge table. Spanner
    # GoogleSQL supports recursive CTEs since 2023; this is more portable
    # than property-graph GQL across Spanner SDK versions.
    repo_filter_seed = "AND repo_uri = @repo_uri" if repo_uri else ""
    repo_filter_step = "AND e.repo_uri = @repo_uri" if repo_uri else ""

    params: dict[str, Any] = {"address": address, "max_depth": max_depth}
    types: dict[str, Any] = {"address": pt.STRING, "max_depth": pt.INT64}
    if repo_uri:
        params["repo_uri"] = repo_uri
        types["repo_uri"] = pt.STRING

    sections: list[str] = []

    def _walk(walk_direction: str) -> list[dict]:
        # downstream: start at @address, follow edges resource_id -> dst_id
        # upstream:   start at @address, follow edges dst_id -> resource_id
        if walk_direction == "downstream":
            seed_col, step_from, step_to = "resource_id", "resource_id", "dst_id"
        else:
            seed_col, step_from, step_to = "resource_id", "dst_id", "resource_id"

        sql = f"""
        WITH RECURSIVE walk AS (
          SELECT {step_to} AS hit_id, repo_uri, 1 AS depth
          FROM DependsOn
          WHERE {step_from} = @address {repo_filter_seed}

          UNION ALL

          SELECT e.{step_to} AS hit_id, e.repo_uri, w.depth + 1
          FROM DependsOn e
          JOIN walk w
            ON e.{step_from} = w.hit_id
           AND e.repo_uri    = w.repo_uri
          WHERE w.depth < @max_depth
            {repo_filter_step}
        )
        SELECT DISTINCT
          r.resource_id, r.type, r.name, r.repo_uri
        FROM walk w
        JOIN Resource r
          ON r.{seed_col} = w.hit_id
         AND r.repo_uri   = w.repo_uri
        ORDER BY r.repo_uri, r.resource_id
        LIMIT 200
        """
        try:
            rows = _spanner_query(sql, params=params, param_types=types)
        except Exception as exc:
            logger.exception("Spanner query failed for %s walk", walk_direction)
            raise RuntimeError(f"Spanner query failed: {exc}") from exc

        return [
            {"resource_id": r[0], "type": r[1], "name": r[2], "repo_uri": r[3]}
            for r in rows
        ]

    try:
        if direction in ("downstream", "both"):
            down = _walk("downstream")
            sections.append(_format_dep_section("Downstream (depends on)", down))
        if direction in ("upstream", "both"):
            up = _walk("upstream")
            sections.append(_format_dep_section("Upstream (depended on by)", up))
    except RuntimeError as exc:
        return str(exc)

    body = "\n\n".join(sections)
    if not body.strip() or "No matches" in body and direction != "both":
        return (
            f"No dependencies found for '{address}' "
            f"(direction={direction}, max_depth={max_depth}). "
            "Check that the graph has been populated (`task graph:populate`) "
            "and the address is correct."
        )
    return f"Dependency walk for '{address}' (max_depth={max_depth})\n\n{body}"


def _format_dep_section(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"{label}: No matches."
    lines = [f"{label}: {len(rows)} result(s)"]
    for r in rows:
        lines.append(f"  - {r['resource_id']}    [{r['type']}]    repo={r['repo_uri']}")
    return "\n".join(lines)


@mcp.tool()
def find_resources_by_type(
    resource_type: str,
    repo_uri: str | None = None,
    limit: int = 50,
) -> str:
    """List Terraform resources of a given type from the Spanner graph.

    Args:
        resource_type: Terraform resource type (e.g. "google_compute_instance",
            "aws_iam_role").
        repo_uri: Optional — restrict to a specific repository (HTTPS git URL).
        limit: Maximum rows to return. Range 1–500, default 50.

    Returns:
        Formatted string listing matching resources, or an error / empty-result
        message.
    """
    try:
        _get_spanner_database()
    except RuntimeError as exc:
        return f"Configuration error: {exc}"

    from google.cloud.spanner_v1 import param_types as pt  # type: ignore[attr-defined]

    limit = min(max(1, int(limit)), 500)

    params: dict[str, Any] = {"type": resource_type, "lim": limit}
    types: dict[str, Any] = {"type": pt.STRING, "lim": pt.INT64}
    where = "WHERE type = @type"
    if repo_uri:
        where += " AND repo_uri = @repo_uri"
        params["repo_uri"] = repo_uri
        types["repo_uri"] = pt.STRING

    sql = (
        "SELECT resource_id, name, repo_uri "
        f"FROM Resource {where} "
        "ORDER BY repo_uri, resource_id "
        "LIMIT @lim"
    )

    try:
        rows = _spanner_query(sql, params=params, param_types=types)
    except Exception as exc:
        logger.exception("Spanner query failed in find_resources_by_type")
        return f"Spanner query failed: {exc}"

    if not rows:
        scope = f" in repo {repo_uri}" if repo_uri else ""
        return (
            f"No resources of type '{resource_type}' found{scope}. "
            "Check that the graph has been populated (`task graph:populate`)."
        )

    lines = [f"Found {len(rows)} resource(s) of type '{resource_type}'"]
    if repo_uri:
        lines[0] += f" in {repo_uri}"
    for r in rows:
        lines.append(f"  - {r[0]}    name={r[1]}    repo={r[2]}")
    return "\n".join(lines)


@mcp.tool()
def get_graph_info() -> str:
    """Return configuration and basic counts for the Spanner graph store.

    Returns:
        Formatted string with project, instance, database, and the number of
        resources / dependencies / repos currently loaded — or an error if the
        graph store is not configured.
    """
    if not PROJECT_ID:
        return "Error: VERTEX_PROJECT or GOOGLE_CLOUD_PROJECT is not set."
    if not SPANNER_INSTANCE:
        return "Error: SPANNER_INSTANCE environment variable is not set."

    try:
        node_rows = _spanner_query("SELECT COUNT(*) FROM Resource")
        edge_rows = _spanner_query("SELECT COUNT(*) FROM DependsOn")
        repo_rows = _spanner_query("SELECT COUNT(DISTINCT repo_uri) FROM Resource")
    except RuntimeError as exc:
        return f"Configuration error: {exc}"
    except Exception as exc:
        logger.exception("Spanner count queries failed")
        return f"Spanner query failed: {exc}"

    nodes = node_rows[0][0] if node_rows else 0
    edges = edge_rows[0][0] if edge_rows else 0
    repos = repo_rows[0][0] if repo_rows else 0

    lines = [
        "Spanner Graph (tf_graph)",
        "=" * 40,
        f"Project:    {PROJECT_ID}",
        f"Instance:   {SPANNER_INSTANCE}",
        f"Database:   {SPANNER_DATABASE}",
        f"Resource:   {nodes} row(s)",
        f"DependsOn:  {edges} row(s)",
        f"Repos:      {repos} distinct repo_uri",
        "",
        "Tools:",
        "  get_resource_dependencies(resource_type, resource_name, direction, max_depth, repo_uri)",
        "  find_resources_by_type(resource_type, repo_uri, limit)",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
