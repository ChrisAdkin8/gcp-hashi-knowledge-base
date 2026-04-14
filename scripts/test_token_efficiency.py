#!/usr/bin/env python3
"""Compare token efficiency: RAG corpus retrieval vs raw documentation sources.

Runs a set of queries against the Vertex AI RAG corpus and/or the Spanner
graph store, measures the token count of retrieved results, then estimates
the token cost of providing the same information from raw documentation
pages or Terraform source files.

Usage:
    # RAG only:
    python3 scripts/test_token_efficiency.py \
        --project-id my-project --region us-west1 \
        --corpus-id 12345678 --mode rag

    # Graph only:
    python3 scripts/test_token_efficiency.py \
        --project-id my-project --region us-west1 \
        --spanner-instance hashicorp-rag-graph --spanner-database tf-graph \
        --mode graph

    # All modes:
    python3 scripts/test_token_efficiency.py \
        --project-id my-project --region us-west1 \
        --corpus-id 12345678 \
        --spanner-instance hashicorp-rag-graph --spanner-database tf-graph \
        --mode all
"""

import argparse
import logging
import os
import sys
import textwrap
from typing import Any

# Suppress noisy gRPC C-core info logs (ev_poll_posix.cc fork warnings).
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")

# Disable Spanner built-in metrics export to Cloud Monitoring.  The client
# auto-enables OpenTelemetry metrics in v3.49+, but local runs lack the
# required resource labels (instance_id), causing noisy 400 errors.
# The env var changed in spanner-python ≥3.64: SPANNER_DISABLE_BUILTIN_METRICS=true.
os.environ.setdefault("SPANNER_DISABLE_BUILTIN_METRICS", "true")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# Each entry includes estimated raw doc sizes based on actual HashiCorp doc pages.
# raw_tokens is measured from the full source pages a human would need to read
# to answer the question (titles, navigation, boilerplate excluded — just content).
BUILTIN_RAG_QUERIES: list[dict] = [
    {
        "topic": "S3 backend configuration",
        "query": "How do I configure an S3 backend in Terraform?",
        "raw_sources": "Terraform S3 backend docs + state locking page + workspaces page",
        "raw_tokens_estimate": 9500,
    },
    {
        "topic": "AWS provider setup",
        "query": "How do I configure the AWS provider in Terraform?",
        "raw_sources": "AWS provider docs main page + authentication page + region config",
        "raw_tokens_estimate": 11000,
    },
    {
        "topic": "Vault dynamic secrets",
        "query": "How do I generate dynamic database credentials using HashiCorp Vault?",
        "raw_sources": "Vault database secrets engine docs + PostgreSQL plugin + lease management",
        "raw_tokens_estimate": 14000,
    },
    {
        "topic": "Consul service mesh",
        "query": "How do I set up mTLS between services using Consul Connect?",
        "raw_sources": "Consul Connect overview + intentions + proxy config + TLS docs",
        "raw_tokens_estimate": 16000,
    },
    {
        "topic": "Packer AMI builds",
        "query": "How do I build an AMI with Packer using an HCL2 template?",
        "raw_sources": "Packer HCL2 docs + builders reference + AMI configuration",
        "raw_tokens_estimate": 8500,
    },
    {
        "topic": "Cross-product: Vault + AWS provider",
        "query": "How do I use Vault dynamic secrets with the Terraform AWS provider?",
        "raw_sources": "Vault AWS secrets engine + Terraform Vault provider + AWS provider auth docs",
        "raw_tokens_estimate": 22000,
    },
    {
        "topic": "Nomad job scheduling",
        "query": "How do I write a Nomad job specification to run a Docker container?",
        "raw_sources": "Nomad job spec docs + task drivers reference + Docker driver page",
        "raw_tokens_estimate": 12000,
    },
    {
        "topic": "Sentinel policy enforcement",
        "query": "How do I write a Sentinel policy to enforce Terraform resource tagging?",
        "raw_sources": "Sentinel language docs + Terraform Cloud policy sets + tfplan import reference",
        "raw_tokens_estimate": 13500,
    },
    {
        "topic": "Terraform module composition",
        "query": "How do I call a Terraform module and pass outputs between modules?",
        "raw_sources": "Terraform modules docs + module sources + output values + variable passing",
        "raw_tokens_estimate": 10000,
    },
    {
        "topic": "Cross-product: Consul + Vault",
        "query": "How do I use Vault to manage TLS certificates for Consul service mesh?",
        "raw_sources": "Consul TLS docs + Vault PKI secrets engine + Consul agent TLS config",
        "raw_tokens_estimate": 19500,
    },
]

# Graph queries test structured dependency lookups vs reading raw .tf files
# or running terraform graph manually and parsing DOT output.
BUILTIN_GRAPH_QUERIES: list[dict] = [
    {
        "topic": "GCS bucket dependencies",
        "query_type": "find_by_type",
        "resource_type": "google_storage_bucket",
        "raw_sources": "terraform plan output + manual grep of .tf files",
        "raw_tokens_estimate": 4500,
    },
    {
        "topic": "IAM member resources",
        "query_type": "find_by_type",
        "resource_type": "google_project_iam_member",
        "raw_sources": "grep all .tf files for google_project_iam_member blocks",
        "raw_tokens_estimate": 6000,
    },
    {
        "topic": "Service account resources",
        "query_type": "find_by_type",
        "resource_type": "google_service_account",
        "raw_sources": "grep .tf files + terraform state list filtering",
        "raw_tokens_estimate": 3500,
    },
    {
        "topic": "Cloud Build trigger chain",
        "query_type": "find_by_type",
        "resource_type": "google_cloudbuild_trigger",
        "raw_sources": "terraform graph DOT output + manual parsing",
        "raw_tokens_estimate": 5000,
    },
    {
        "topic": "Spanner resources",
        "query_type": "find_by_type",
        "resource_type": "google_spanner_instance",
        "raw_sources": "grep .tf files for spanner blocks + state inspection",
        "raw_tokens_estimate": 4000,
    },
    {
        "topic": "Workflow resources",
        "query_type": "find_by_type",
        "resource_type": "google_workflows_workflow",
        "raw_sources": "grep .tf files + terraform state list",
        "raw_tokens_estimate": 3000,
    },
    {
        "topic": "Scheduler job resources",
        "query_type": "find_by_type",
        "resource_type": "google_cloud_scheduler_job",
        "raw_sources": "grep .tf files + terraform state list",
        "raw_tokens_estimate": 3500,
    },
    {
        "topic": "Graph statistics overview",
        "query_type": "graph_info",
        "raw_sources": "terraform state list | wc -l + terraform graph | dot analysis",
        "raw_tokens_estimate": 2000,
    },
]


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken if available, else estimate from words.

    Args:
        text: The text to tokenise.

    Returns:
        Approximate token count.
    """
    try:
        import tiktoken  # type: ignore[import]

        enc = tiktoken.encoding_for_model("gpt-4o")
        return len(enc.encode(text))
    except ImportError:
        # Fallback: ~1.3 tokens per word is a reasonable approximation
        # for technical English with code blocks.
        return int(len(text.split()) * 1.3)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Compare token efficiency of RAG retrieval vs raw documentation."
    )
    parser.add_argument("--project-id", required=True, help="GCP project ID.")
    parser.add_argument("--region", required=True, help="GCP region.")
    parser.add_argument("--corpus-id", default=None, help="Vertex AI RAG corpus ID.")
    parser.add_argument(
        "--mode",
        choices=["rag", "graph", "combined", "all"],
        default="rag",
        help="Test mode: rag, graph, combined, or all. Default: rag",
    )
    parser.add_argument(
        "--spanner-instance",
        default=None,
        help="Spanner instance name for graph queries.",
    )
    parser.add_argument(
        "--spanner-database",
        default=None,
        help="Spanner database name for graph queries.",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Run a single custom query instead of the built-in suite.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of chunks to retrieve per query. Default: 3",
    )
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=0.28,
        help="Vector distance threshold. Default: 0.28",
    )
    args = parser.parse_args()

    needs_rag = args.mode in ("rag", "combined", "all")
    needs_graph = args.mode in ("graph", "combined", "all")

    if needs_rag and not args.corpus_id:
        parser.error("--corpus-id is required for mode '%s'" % args.mode)
    if needs_graph and (not args.spanner_instance or not args.spanner_database):
        parser.error(
            "--spanner-instance and --spanner-database are required for mode '%s'"
            % args.mode
        )

    return args


def retrieve_from_corpus(
    project_id: str,
    region: str,
    corpus_id: str,
    query_text: str,
    top_k: int,
    distance_threshold: float,
) -> tuple[str, int]:
    """Retrieve chunks from the RAG corpus and return the combined text and chunk count.

    Args:
        project_id: GCP project ID.
        region: GCP region.
        corpus_id: Vertex AI RAG corpus ID.
        query_text: The query string.
        top_k: Max results.
        distance_threshold: Min relevance score.

    Returns:
        Tuple of (combined_text, chunk_count).
    """
    try:
        from vertexai import rag  # type: ignore[import]
        import vertexai  # type: ignore[import]
    except ImportError:
        logger.error(
            "google-cloud-aiplatform is not installed. "
            "Run: pip install google-cloud-aiplatform"
        )
        sys.exit(1)

    vertexai.init(project=project_id, location=region)

    corpus_name = f"projects/{project_id}/locations/{region}/ragCorpora/{corpus_id}"
    rag_resource = rag.RagResource(rag_corpus=corpus_name)

    retrieval_config = rag.RagRetrievalConfig(
        top_k=top_k,
        filter=rag.Filter(vector_distance_threshold=distance_threshold),
    )
    response = rag.retrieval_query(
        rag_resources=[rag_resource],
        text=query_text,
        rag_retrieval_config=retrieval_config,
    )

    texts: list[str] = []
    if response.contexts and response.contexts.contexts:
        for ctx in response.contexts.contexts:
            text = getattr(ctx, "text", "") or ""
            if text:
                texts.append(text)

    combined = "\n\n---\n\n".join(texts)
    return combined, len(texts)


def _spanner_query(
    database: Any,
    sql: str,
    params: dict | None = None,
    param_types: dict | None = None,
) -> list[list[Any]]:
    """Execute a read-only SQL query against Spanner.

    Args:
        database: Spanner database handle.
        sql: SQL query string.
        params: Query parameter values.
        param_types: Spanner param type hints.

    Returns:
        List of rows (each row is a list of column values).
    """
    with database.snapshot() as snapshot:
        result = snapshot.execute_sql(sql, params=params, param_types=param_types)
        return [list(row) for row in result]


def retrieve_from_graph(
    project_id: str,
    spanner_instance: str,
    spanner_database: str,
    query_item: dict,
) -> tuple[str, int]:
    """Query the Spanner graph store and return formatted results and row count.

    Args:
        project_id: GCP project ID.
        spanner_instance: Spanner instance name.
        spanner_database: Spanner database name.
        query_item: Dict with query_type and type-specific fields.

    Returns:
        Tuple of (formatted_text, row_count).
    """
    try:
        from google.cloud import spanner  # type: ignore[attr-defined]
    except ImportError:
        logger.error(
            "google-cloud-spanner is not installed. "
            "Run: pip install google-cloud-spanner"
        )
        sys.exit(1)

    client = spanner.Client(project=project_id)
    instance = client.instance(spanner_instance)
    database = instance.database(spanner_database)

    query_type = query_item["query_type"]

    if query_type == "find_by_type":
        resource_type = query_item["resource_type"]
        rows = _spanner_query(
            database,
            "SELECT resource_id, name, repo_uri FROM Resource "
            "WHERE type = @type ORDER BY repo_uri, resource_id LIMIT 50",
            params={"type": resource_type},
            param_types={"type": spanner.param_types.STRING},
        )
        lines = [f"resource_id={r[0]}, name={r[1]}, repo={r[2]}" for r in rows]
        return "\n".join(lines) if lines else "(no results)", len(rows)

    elif query_type == "graph_info":
        node_count = _spanner_query(database, "SELECT COUNT(*) FROM Resource")
        edge_count = _spanner_query(database, "SELECT COUNT(*) FROM DependsOn")
        repo_count = _spanner_query(
            database, "SELECT COUNT(DISTINCT repo_uri) FROM Resource"
        )
        text = (
            f"Nodes: {node_count[0][0]}\n"
            f"Edges: {edge_count[0][0]}\n"
            f"Repos: {repo_count[0][0]}"
        )
        return text, 3  # 3 stat rows

    else:
        return f"(unknown query_type: {query_type})", 0


def run_rag_tests(args: argparse.Namespace) -> list[dict]:
    """Run RAG token efficiency tests.

    Args:
        args: Parsed CLI arguments.

    Returns:
        List of result dicts.
    """
    if args.query:
        queries = [
            {
                "topic": "custom query",
                "query": args.query,
                "raw_sources": "(manual estimate required)",
                "raw_tokens_estimate": 0,
            }
        ]
    else:
        queries = BUILTIN_RAG_QUERIES

    results: list[dict] = []
    for item in queries:
        topic = item["topic"]
        raw_estimate = item["raw_tokens_estimate"]
        try:
            combined_text, chunk_count = retrieve_from_corpus(
                project_id=args.project_id,
                region=args.region,
                corpus_id=args.corpus_id,
                query_text=item["query"],
                top_k=args.top_k,
                distance_threshold=args.distance_threshold,
            )
            rag_tokens = count_tokens(combined_text) if combined_text else 0
            saving_pct = (
                ((raw_estimate - rag_tokens) / raw_estimate) * 100
                if raw_estimate > 0 and rag_tokens > 0
                else 0.0
            )
            results.append(
                {
                    "topic": f"[RAG] {topic}",
                    "chunks": chunk_count,
                    "retrieval_tokens": rag_tokens,
                    "raw_tokens": raw_estimate,
                    "saving_pct": saving_pct,
                    "raw_sources": item["raw_sources"],
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("RAG query '%s' failed: %s", topic, exc)
            results.append(
                {
                    "topic": f"[RAG] {topic}",
                    "chunks": 0,
                    "retrieval_tokens": 0,
                    "raw_tokens": raw_estimate,
                    "saving_pct": 0.0,
                    "raw_sources": item["raw_sources"],
                    "error": str(exc),
                }
            )
    return results


def run_graph_tests(args: argparse.Namespace) -> list[dict]:
    """Run graph store token efficiency tests.

    Args:
        args: Parsed CLI arguments.

    Returns:
        List of result dicts.
    """
    results: list[dict] = []
    for item in BUILTIN_GRAPH_QUERIES:
        topic = item["topic"]
        raw_estimate = item["raw_tokens_estimate"]
        try:
            text, row_count = retrieve_from_graph(
                project_id=args.project_id,
                spanner_instance=args.spanner_instance,
                spanner_database=args.spanner_database,
                query_item=item,
            )
            graph_tokens = count_tokens(text) if text else 0
            saving_pct = (
                ((raw_estimate - graph_tokens) / raw_estimate) * 100
                if raw_estimate > 0 and graph_tokens > 0
                else 0.0
            )
            results.append(
                {
                    "topic": f"[Graph] {topic}",
                    "chunks": row_count,
                    "retrieval_tokens": graph_tokens,
                    "raw_tokens": raw_estimate,
                    "saving_pct": saving_pct,
                    "raw_sources": item["raw_sources"],
                    "error": None,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Graph query '%s' failed: %s", topic, exc)
            results.append(
                {
                    "topic": f"[Graph] {topic}",
                    "chunks": 0,
                    "retrieval_tokens": 0,
                    "raw_tokens": raw_estimate,
                    "saving_pct": 0.0,
                    "raw_sources": item["raw_sources"],
                    "error": str(exc),
                }
            )
    return results


def print_results(results: list[dict], label: str) -> None:
    """Print per-query results and summary table.

    Args:
        results: List of result dicts.
        label: Section label (e.g. "RAG", "Graph", "All").
    """
    # ── Per-query results ────────────────────────────────────────────────
    for r in results:
        print(f"── {r['topic']} {'─' * max(1, 56 - len(r['topic']))}")
        if r["error"]:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  Rows/chunks retrieved : {r['chunks']}")
        print(f"  Retrieval tokens      : {r['retrieval_tokens']:,}")
        if r["raw_tokens"] > 0:
            print(f"  Raw tokens estimate   : {r['raw_tokens']:,}  ({r['raw_sources']})")
            print(f"  Token saving          : {r['saving_pct']:.0f}%")
        print()

    # ── Summary table ────────────────────────────────────────────────────
    valid = [r for r in results if not r["error"] and r["raw_tokens"] > 0]
    if not valid:
        print(f"No valid {label} results to summarise.")
        return

    total_ret = sum(r["retrieval_tokens"] for r in valid)
    total_raw = sum(r["raw_tokens"] for r in valid)
    overall_saving = ((total_raw - total_ret) / total_raw) * 100 if total_raw else 0

    # Compute query column width from data so all columns stay aligned.
    qw = max(len(r["topic"]) for r in valid)
    qw = max(qw, len("TOTAL"), len("Query")) + 2  # pad for readability
    table_width = qw + 10 + 8 + 8  # Retrieved + Raw + Saving columns

    print(f"\n{'=' * table_width}")
    print(f"SUMMARY — {label}")
    print(f"{'=' * table_width}")
    print()
    header = f"{'Query':<{qw}} {'Retrieved':>10} {'Raw':>8} {'Saving':>8}"
    print(header)
    print("─" * table_width)
    for r in valid:
        print(
            f"{r['topic']:<{qw}} {r['retrieval_tokens']:>9,} {r['raw_tokens']:>7,}"
            f" {r['saving_pct']:>6.0f}%"
        )
    print("─" * table_width)
    print(
        f"{'TOTAL':<{qw}} {total_ret:>9,} {total_raw:>7,} {overall_saving:>6.0f}%"
    )
    print()

    avg_chunks = sum(r["chunks"] for r in valid) / len(valid)
    avg_ret = total_ret / len(valid)
    avg_raw = total_raw / len(valid)

    print(
        textwrap.dedent(f"""\
    Key findings ({label}):
    • Average retrieval: {avg_ret:,.0f} tokens ({avg_chunks:.1f} rows/chunks)
    • Average raw:       {avg_raw:,.0f} tokens
    • Overall saving:    {overall_saving:.0f}%
    • Retrieval delivers focused context using {100 - overall_saving:.0f}% of
      the tokens required by raw sources.""")
    )


def main() -> None:
    """Entry point."""
    args = parse_args()

    needs_rag = args.mode in ("rag", "combined", "all")
    needs_graph = args.mode in ("graph", "combined", "all")

    try:
        import tiktoken  # noqa: F401

        tokeniser = "tiktoken (cl100k_base)"
    except ImportError:
        tokeniser = "word-count estimate (~1.3 tokens/word)"

    print(f"Token Efficiency Test — mode={args.mode}")
    print(f"{'=' * 72}")
    print(f"Project   : {args.project_id}")
    print(f"Region    : {args.region}")
    if needs_rag:
        print(f"Corpus    : {args.corpus_id}")
        print(f"Top-K     : {args.top_k}")
        print(f"Threshold : {args.distance_threshold}")
    if needs_graph:
        print(f"Spanner   : {args.spanner_instance}/{args.spanner_database}")
    print(f"Tokeniser : {tokeniser}")
    print()

    all_results: list[dict] = []
    has_failure = False

    if needs_rag:
        print(f"\n{'=' * 72}")
        print("RAG CORPUS QUERIES")
        print(f"{'=' * 72}\n")
        rag_results = run_rag_tests(args)
        all_results.extend(rag_results)
        print_results(rag_results, "RAG")

    if needs_graph:
        print(f"\n{'=' * 72}")
        print("GRAPH STORE QUERIES")
        print(f"{'=' * 72}\n")
        graph_results = run_graph_tests(args)
        all_results.extend(graph_results)
        print_results(graph_results, "Graph")

    # Combined summary when both ran
    if needs_rag and needs_graph:
        print_results(all_results, "Combined (RAG + Graph)")

    valid = [r for r in all_results if not r["error"] and r["raw_tokens"] > 0]
    if not valid:
        print("No valid results to summarise.")
        has_failure = True

    sys.exit(1 if has_failure else 0)


if __name__ == "__main__":
    main()
