#!/usr/bin/env python3
"""Create a Vertex AI RAG corpus for the HashiCorp knowledge base.

Usage:
    # Human-readable output (interactive):
    python3 scripts/create_corpus.py --project-id my-project --region us-central1

    # Machine-readable output (used by scripts/deploy.sh):
    python3 scripts/create_corpus.py --project-id my-project --region us-central1 --output-id-only
"""

import argparse
import logging
import re
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Create a Vertex AI RAG corpus for the HashiCorp knowledge base."
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="GCP project ID.",
    )
    parser.add_argument(
        "--region",
        required=True,
        help="GCP region (e.g. us-central1).",
    )
    parser.add_argument(
        "--display-name",
        default="hashicorp-knowledge-base",
        help="Display name for the RAG corpus. Default: hashicorp-knowledge-base",
    )
    parser.add_argument(
        "--embedding-model",
        default="publishers/google/models/text-embedding-005",
        help="Vertex AI embedding model resource path.",
    )
    parser.add_argument(
        "--output-id-only",
        action="store_true",
        help="Print only the corpus ID to stdout. All other output goes to stderr. "
             "Used by scripts/deploy.sh for machine-readable capture.",
    )
    return parser.parse_args()


def extract_corpus_id(corpus_name: str) -> str:
    """Extract the numeric corpus ID from a full resource name.

    Args:
        corpus_name: Full resource name, e.g.
            ``projects/123/locations/us-central1/ragCorpora/456``.

    Returns:
        Corpus ID string (e.g. ``"456"``).

    Raises:
        ValueError: If the corpus ID cannot be extracted.
    """
    match = re.search(r"/ragCorpora/(\d+)$", corpus_name)
    if not match:
        raise ValueError(f"Cannot extract corpus ID from: {corpus_name}")
    return match.group(1)


def get_or_create_corpus(
    project_id: str,
    region: str,
    display_name: str,
    embedding_model: str,
) -> tuple[str, str]:
    """Return an existing corpus matching *display_name*, or create one.

    This is the primary entry point.  It eliminates the race condition that
    caused duplicate corpora when the old workflow auto-provisioned on every run.

    Args:
        project_id: GCP project ID.
        region: GCP region.
        display_name: Human-readable corpus name to match or create.
        embedding_model: Vertex AI embedding model resource path.

    Returns:
        Tuple of (corpus_name, corpus_id).
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

    logger.info("Initialising Vertex AI for project=%s region=%s", project_id, region)
    vertexai.init(project=project_id, location=region)

    # Check for an existing corpus with the same display name.
    logger.info("Listing existing RAG corpora …")
    for corpus in rag.list_corpora():
        if corpus.display_name == display_name:
            corpus_name: str = corpus.name
            corpus_id = extract_corpus_id(corpus_name)
            logger.info("Found existing corpus: %s (id=%s)", corpus_name, corpus_id)
            return corpus_name, corpus_id

    # No match — create a new one.
    logger.info("No corpus named %r found — creating …", display_name)
    embedding_config = rag.RagEmbeddingModelConfig(
        vertex_prediction_endpoint=rag.VertexPredictionEndpoint(
            publisher_model=embedding_model,
        ),
    )
    backend_config = rag.RagVectorDbConfig(
        rag_embedding_model_config=embedding_config,
    )
    corpus = rag.create_corpus(
        display_name=display_name,
        backend_config=backend_config,
    )

    corpus_name = corpus.name
    corpus_id = extract_corpus_id(corpus_name)
    logger.info("Created corpus: %s (id=%s)", corpus_name, corpus_id)
    return corpus_name, corpus_id


def main() -> None:
    """Entry point."""
    args = parse_args()

    if args.output_id_only:
        # Redirect logging to stderr so only the corpus ID reaches stdout.
        for handler in logging.getLogger().handlers:
            handler.stream = sys.stderr  # type: ignore[attr-defined]

    corpus_name, corpus_id = get_or_create_corpus(
        project_id=args.project_id,
        region=args.region,
        display_name=args.display_name,
        embedding_model=args.embedding_model,
    )

    if args.output_id_only:
        print(corpus_id)
        return

    print(f"\nCorpus created successfully!")
    print(f"  Full resource name : {corpus_name}")
    print(f"  Corpus ID          : {corpus_id}")
    print()
    print("The corpus ID has been captured. If running manually, add it to")
    print(f'terraform/corpus.auto.tfvars:')
    print(f'    corpus_id = "{corpus_id}"')


if __name__ == "__main__":
    main()
