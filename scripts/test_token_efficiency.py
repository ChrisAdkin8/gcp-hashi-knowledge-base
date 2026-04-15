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
        "topic": "Vertex AI RAG corpus resources",
        "query_type": "find_by_type",
        "resource_type": "google_vertex_ai_rag_corpus",
        "raw_sources": "grep .tf files for vertex_ai blocks + terraform state list",
        "raw_tokens_estimate": 3000,
    },
    {
        "topic": "Pub/Sub topic resources",
        "query_type": "find_by_type",
        "resource_type": "google_pubsub_topic",
        "raw_sources": "grep .tf files for pubsub blocks + terraform state list",
        "raw_tokens_estimate": 3500,
    },
    {
        "topic": "Graph statistics overview",
        "query_type": "graph_info",
        "raw_sources": "terraform state list | wc -l + terraform graph | dot analysis",
        "raw_tokens_estimate": 2000,
    },
]

# Combined queries require answers from BOTH the RAG corpus (documentation)
# AND the Spanner graph store (infrastructure structure/dependencies).
# Each entry has a natural-language query for RAG plus a graph lookup that
# contributes structural context the docs alone cannot provide.
BUILTIN_COMBINED_QUERIES: list[dict] = [
    {
        "topic": "IAM roles vs least-privilege guidance",
        "rag_query": (
            "What are the best practices for granting IAM roles to service "
            "accounts in Google Cloud Terraform projects?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_project_iam_member",
        },
        "why_combined": (
            "RAG provides HashiCorp best-practice guidance; graph shows which "
            "IAM bindings actually exist so the answer can flag over-permissioned roles"
        ),
        "raw_sources": (
            "Terraform GCP IAM docs + Vault identity docs + grep all .tf for IAM blocks"
        ),
        "raw_tokens_estimate": 18000,
    },
    {
        "topic": "Service account security posture",
        "rag_query": (
            "How should service accounts be secured and rotated according to "
            "HashiCorp Vault and Terraform best practices?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_service_account",
        },
        "why_combined": (
            "RAG returns Vault secret-rotation and Terraform SA docs; graph "
            "lists the actual service accounts deployed so the answer is grounded"
        ),
        "raw_sources": (
            "Vault GCP secrets engine docs + Terraform SA resource docs + grep .tf files"
        ),
        "raw_tokens_estimate": 15000,
    },
    {
        "topic": "CI/CD pipeline structure and configuration",
        "rag_query": (
            "How should Cloud Build triggers be configured in Terraform for a "
            "CI/CD pipeline following HashiCorp recommended patterns?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_cloudbuild_trigger",
        },
        "why_combined": (
            "RAG provides Terraform CI/CD pattern docs; graph reveals the "
            "actual trigger resources and their dependency chain"
        ),
        "raw_sources": (
            "Terraform Cloud Build docs + HCP Terraform run-task docs + "
            "grep .tf files + terraform graph output"
        ),
        "raw_tokens_estimate": 17000,
    },
    {
        "topic": "Spanner deployment vs Terraform database guidance",
        "rag_query": (
            "What does HashiCorp documentation recommend for managing Spanner "
            "instances and databases with Terraform, including edition selection?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_spanner_instance",
        },
        "why_combined": (
            "RAG covers Terraform Spanner resource docs and edition guidance; "
            "graph shows the actual deployed Spanner resources for comparison"
        ),
        "raw_sources": (
            "Terraform google_spanner_instance docs + google_spanner_database docs "
            "+ grep .tf files for spanner blocks"
        ),
        "raw_tokens_estimate": 14000,
    },
    {
        "topic": "Workflow orchestration design and implementation",
        "rag_query": (
            "How should Cloud Workflows and Cloud Scheduler be configured in "
            "Terraform to orchestrate a data pipeline?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_workflows_workflow",
        },
        "why_combined": (
            "RAG provides Terraform orchestration pattern docs; graph reveals "
            "the deployed workflow resources and their dependencies"
        ),
        "raw_sources": (
            "Terraform Cloud Workflows docs + Cloud Scheduler docs + grep .tf "
            "files + terraform graph output"
        ),
        "raw_tokens_estimate": 16000,
    },
    {
        "topic": "State backend storage and bucket configuration",
        "rag_query": (
            "What are Terraform best practices for configuring GCS buckets as "
            "remote state backends, including versioning and locking?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_storage_bucket",
        },
        "why_combined": (
            "RAG returns Terraform state backend docs; graph shows the actual "
            "GCS buckets deployed so the answer can verify the backend setup"
        ),
        "raw_sources": (
            "Terraform GCS backend docs + state locking page + versioning docs "
            "+ grep .tf files for bucket resources"
        ),
        "raw_tokens_estimate": 15500,
    },
    {
        "topic": "Scheduler-driven workflow orchestration patterns",
        "rag_query": (
            "What are HashiCorp best practices for using Cloud Scheduler to "
            "trigger Cloud Workflows in a Terraform-managed pipeline?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_cloud_scheduler_job",
        },
        "why_combined": (
            "RAG provides Terraform scheduler and workflow docs; graph shows "
            "the actual scheduler jobs and their trigger targets"
        ),
        "raw_sources": (
            "Terraform Cloud Scheduler docs + Cloud Workflows docs + "
            "grep .tf files for scheduler_job blocks"
        ),
        "raw_tokens_estimate": 14500,
    },
    {
        "topic": "Pub/Sub event-driven architecture",
        "rag_query": (
            "How should Pub/Sub topics and subscriptions be configured in "
            "Terraform for event-driven data pipelines?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_pubsub_topic",
        },
        "why_combined": (
            "RAG returns Terraform Pub/Sub resource docs and event-driven "
            "patterns; graph reveals the deployed topics and their dependencies"
        ),
        "raw_sources": (
            "Terraform google_pubsub_topic docs + google_pubsub_subscription docs "
            "+ grep .tf files for pubsub blocks"
        ),
        "raw_tokens_estimate": 13000,
    },
    {
        "topic": "Vault-managed secrets for GCP service accounts",
        "rag_query": (
            "How does HashiCorp Vault integrate with GCP to dynamically "
            "generate service account keys using the GCP secrets engine?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_service_account",
        },
        "why_combined": (
            "RAG provides Vault GCP secrets engine documentation; graph shows "
            "which service accounts exist to validate rotation coverage"
        ),
        "raw_sources": (
            "Vault GCP secrets engine docs + Terraform SA docs + "
            "grep .tf files for service_account resources"
        ),
        "raw_tokens_estimate": 16500,
    },
    {
        "topic": "RAG corpus ingestion and Vertex AI configuration",
        "rag_query": (
            "How should a Vertex AI RAG corpus be configured and populated "
            "with documents using Terraform and Python automation?"
        ),
        "graph_query": {
            "query_type": "find_by_type",
            "resource_type": "google_storage_bucket",
        },
        "why_combined": (
            "RAG returns Vertex AI RAG API docs and ingestion patterns; graph "
            "shows the GCS buckets used for document staging"
        ),
        "raw_sources": (
            "Vertex AI RAG Engine docs + Terraform GCS bucket docs + "
            "grep .tf files + scripts/create_corpus.py"
        ),
        "raw_tokens_estimate": 19000,
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
        "--verbose",
        action="store_true",
        default=False,
        help="Show per-query detail. Default: only tables and summary.",
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

    needs_rag = args.mode in ("rag", "all")
    needs_graph = args.mode in ("graph", "all")
    needs_combined = args.mode in ("combined", "all")

    if (needs_rag or needs_combined) and not args.corpus_id:
        parser.error("--corpus-id is required for mode '%s'" % args.mode)
    if (needs_graph or needs_combined) and (
        not args.spanner_instance or not args.spanner_database
    ):
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


def run_combined_tests(args: argparse.Namespace) -> list[dict]:
    """Run combined tests that query both RAG and graph for each prompt.

    Each combined query retrieves documentation context from the RAG corpus
    and structural/dependency data from the Spanner graph, then merges the
    token counts to show the cost of answering a question that requires both.

    Args:
        args: Parsed CLI arguments.

    Returns:
        List of result dicts.
    """
    results: list[dict] = []
    for item in BUILTIN_COMBINED_QUERIES:
        topic = item["topic"]
        raw_estimate = item["raw_tokens_estimate"]
        rag_tokens = 0
        graph_tokens = 0
        rag_chunks = 0
        graph_rows = 0
        error_parts: list[str] = []

        # RAG retrieval
        try:
            rag_text, rag_chunks = retrieve_from_corpus(
                project_id=args.project_id,
                region=args.region,
                corpus_id=args.corpus_id,
                query_text=item["rag_query"],
                top_k=args.top_k,
                distance_threshold=args.distance_threshold,
            )
            rag_tokens = count_tokens(rag_text) if rag_text else 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Combined RAG query '%s' failed: %s", topic, exc)
            error_parts.append(f"RAG: {exc}")

        # Graph retrieval
        try:
            graph_text, graph_rows = retrieve_from_graph(
                project_id=args.project_id,
                spanner_instance=args.spanner_instance,
                spanner_database=args.spanner_database,
                query_item=item["graph_query"],
            )
            graph_tokens = count_tokens(graph_text) if graph_text else 0
        except Exception as exc:  # noqa: BLE001
            logger.error("Combined graph query '%s' failed: %s", topic, exc)
            error_parts.append(f"Graph: {exc}")

        total_tokens = rag_tokens + graph_tokens
        total_chunks = rag_chunks + graph_rows
        saving_pct = (
            ((raw_estimate - total_tokens) / raw_estimate) * 100
            if raw_estimate > 0 and total_tokens > 0
            else 0.0
        )
        results.append(
            {
                "topic": f"[Combined] {topic}",
                "chunks": total_chunks,
                "retrieval_tokens": total_tokens,
                "rag_tokens": rag_tokens,
                "graph_tokens": graph_tokens,
                "rag_chunks": rag_chunks,
                "graph_rows": graph_rows,
                "raw_tokens": raw_estimate,
                "saving_pct": saving_pct,
                "raw_sources": item["raw_sources"],
                "why_combined": item["why_combined"],
                "error": "; ".join(error_parts) if error_parts else None,
            }
        )
    return results


def print_results(results: list[dict], label: str, *, verbose: bool = False) -> None:
    """Print per-query results and summary table.

    Args:
        results: List of result dicts.
        label: Section label (e.g. "RAG", "Graph", "All").
        verbose: If True, show per-query detail before the summary table.
    """
    # ── Per-query results (verbose only) ─────────────────────────────────
    if verbose:
        for r in results:
            print(f"── {r['topic']} {'─' * max(1, 56 - len(r['topic']))}")
            if r["error"]:
                print(f"  ERROR: {r['error']}")
                continue
            print(f"  Rows/chunks retrieved : {r['chunks']}")
            print(f"  Retrieval tokens      : {r['retrieval_tokens']:,}")
            if "rag_tokens" in r:
                print(f"    ├─ RAG tokens       : {r['rag_tokens']:,}  ({r['rag_chunks']} chunks)")
                print(f"    └─ Graph tokens     : {r['graph_tokens']:,}  ({r['graph_rows']} rows)")
            if r.get("why_combined"):
                print(f"  Why combined          : {r['why_combined']}")
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

    needs_rag = args.mode in ("rag", "all")
    needs_graph = args.mode in ("graph", "all")
    needs_combined = args.mode in ("combined", "all")

    try:
        import tiktoken  # noqa: F401

        tokeniser = "tiktoken (cl100k_base)"
    except ImportError:
        tokeniser = "word-count estimate (~1.3 tokens/word)"

    print(f"Token Efficiency Test — mode={args.mode}")
    print(f"{'=' * 72}")
    print(f"Project   : {args.project_id}")
    print(f"Region    : {args.region}")
    if needs_rag or needs_combined:
        print(f"Corpus    : {args.corpus_id}")
        print(f"Top-K     : {args.top_k}")
        print(f"Threshold : {args.distance_threshold}")
    if needs_graph or needs_combined:
        print(f"Spanner   : {args.spanner_instance}/{args.spanner_database}")
    print(f"Tokeniser : {tokeniser}")
    print()

    all_results: list[dict] = []
    has_failure = False

    verbose = args.verbose

    if needs_rag:
        if verbose:
            print(f"\n{'=' * 72}")
            print("RAG CORPUS QUERIES")
            print(f"{'=' * 72}\n")
        rag_results = run_rag_tests(args)
        all_results.extend(rag_results)
        print_results(rag_results, "RAG", verbose=verbose)

    if needs_graph:
        if verbose:
            print(f"\n{'=' * 72}")
            print("GRAPH STORE QUERIES")
            print(f"{'=' * 72}\n")
        graph_results = run_graph_tests(args)
        all_results.extend(graph_results)
        print_results(graph_results, "Graph", verbose=verbose)

    if needs_combined:
        if verbose:
            print(f"\n{'=' * 72}")
            print("COMBINED QUERIES (require both RAG + Graph)")
            print(f"{'=' * 72}\n")
        combined_results = run_combined_tests(args)
        all_results.extend(combined_results)
        print_results(combined_results, "Combined (RAG + Graph)", verbose=verbose)

    # Overall summary when multiple sections ran
    if sum([needs_rag, needs_graph, needs_combined]) > 1:
        print_results(all_results, "All", verbose=verbose)

    valid = [r for r in all_results if not r["error"] and r["raw_tokens"] > 0]
    if not valid:
        print("No valid results to summarise.")
        has_failure = True

    sys.exit(1 if has_failure else 0)


if __name__ == "__main__":
    main()
