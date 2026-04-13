#!/usr/bin/env python3
"""Test Vertex AI RAG corpus retrieval quality.

Runs a suite of queries against the corpus and verifies that each returns at
least one result. Exits with code 0 if all queries succeed, 1 otherwise.

Usage:
    python3 scripts/test_retrieval.py \\
        --project-id my-project \\
        --region us-central1 \\
        --corpus-id 12345678

    # Or with a custom single query:
    python3 scripts/test_retrieval.py \\
        --project-id my-project \\
        --region us-central1 \\
        --corpus-id 12345678 \\
        --query "How do I use Vault dynamic secrets?"
"""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

BUILTIN_QUERIES: list[dict[str, str]] = [
    {
        "topic": "AWS provider",
        "query": "How do I configure the AWS provider in Terraform?",
    },
    {
        "topic": "Vault dynamic secrets",
        "query": "How do I generate dynamic database credentials using HashiCorp Vault?",
    },
    {
        "topic": "Consul service mesh",
        "query": "How do I set up mTLS between services using Consul Connect?",
    },
    {
        "topic": "Packer AMI builds",
        "query": "How do I build an AMI with Packer using an HCL2 template?",
    },
    {
        "topic": "Terraform modules",
        "query": "What is the structure of a reusable Terraform module?",
    },
    {
        "topic": "Nomad job specs",
        "query": "How do I define a Nomad job specification for a Docker container?",
    },
]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Test retrieval quality against a Vertex AI RAG corpus."
    )
    parser.add_argument("--project-id", required=True, help="GCP project ID.")
    parser.add_argument("--region", required=True, help="GCP region (e.g. us-central1).")
    parser.add_argument("--corpus-id", required=True, help="Vertex AI RAG corpus ID.")
    parser.add_argument(
        "--query",
        default=None,
        help="Run a single custom query instead of the built-in test suite.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of results to retrieve per query. Default: 5",
    )
    parser.add_argument(
        "--distance-threshold",
        type=float,
        default=0.35,
        help="Minimum vector distance threshold. Results below this relevance "
             "score are excluded. Lower = stricter. Default: 0.3",
    )
    return parser.parse_args()


def run_query(
    project_id: str,
    region: str,
    corpus_id: str,
    query_text: str,
    top_k: int,
    distance_threshold: float = 0.35,
) -> list[dict]:
    """Run a retrieval query against the corpus.

    Args:
        project_id: GCP project ID.
        region: GCP region.
        corpus_id: Vertex AI RAG corpus ID.
        query_text: The natural language query.
        top_k: Maximum number of results to return.
        distance_threshold: Minimum vector distance score. Results below
            this threshold are excluded. Lower = stricter filtering.

    Returns:
        List of context dicts, each with ``source_uri`` and ``score`` keys.
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

    results = []
    if response.contexts and response.contexts.contexts:
        for ctx in response.contexts.contexts:
            results.append(
                {
                    "source_uri": getattr(ctx, "source_uri", ""),
                    "score": getattr(ctx, "score", 0.0),
                    "text_snippet": (getattr(ctx, "text", "") or "")[:200],
                }
            )
    return results


def print_query_results(topic: str, query_text: str, results: list[dict]) -> None:
    """Print formatted query results.

    Args:
        topic: Short topic label.
        query_text: The query string.
        results: List of result dicts from run_query.
    """
    print(f"\n{'=' * 60}")
    print(f"Topic : {topic}")
    print(f"Query : {query_text}")
    print(f"Results: {len(results)}")
    for i, r in enumerate(results[:3], 1):
        score = r.get("score", 0)
        uri = r.get("source_uri", "")
        print(f"  [{i}] score={score:.4f}  uri={uri}")


def main() -> None:
    """Entry point."""
    args = parse_args()

    queries: list[dict[str, str]]
    if args.query:
        queries = [{"topic": "custom", "query": args.query}]
    else:
        queries = BUILTIN_QUERIES

    print(f"Testing corpus {args.corpus_id} in {args.project_id}/{args.region}")
    print(f"Running {len(queries)} queries (top_k={args.top_k})")

    all_passed = True
    for item in queries:
        topic = item["topic"]
        query_text = item["query"]
        try:
            results = run_query(
                project_id=args.project_id,
                region=args.region,
                corpus_id=args.corpus_id,
                query_text=query_text,
                top_k=args.top_k,
                distance_threshold=args.distance_threshold,
            )
            print_query_results(topic, query_text, results)
            if not results:
                logger.warning("Query '%s' returned 0 results.", topic)
                all_passed = False
        except Exception as exc:  # noqa: BLE001
            logger.error("Query '%s' failed: %s", topic, exc)
            all_passed = False

    print(f"\n{'=' * 60}")
    if all_passed:
        print("PASS: All queries returned at least 1 result.")
        sys.exit(0)
    else:
        print("FAIL: One or more queries returned 0 results.")
        sys.exit(1)


if __name__ == "__main__":
    main()
