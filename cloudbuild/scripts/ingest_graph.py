#!/usr/bin/env python3
"""
Ingest a Terraform workspace resource graph into a Spanner Graph database.

Reads DOT output from `terraform graph`, extracts resource nodes and
dependency edges, then upserts them into a Spanner Graph database via the
google-cloud-spanner client (auth: Application Default Credentials).
Optionally uploads the DOT snapshot to GCS for debugging.

Usage:
    terraform graph > graph.dot
    python3 ingest_graph.py \\
        --dot-path graph.dot \\
        --repo-uri https://github.com/org/repo \\
        --project-id my-gcp-project \\
        --instance hashicorp-rag-graph \\
        --database tf-graph \\
        --bucket my-graph-staging \\
        --snapshot-key snapshots/repo/20260101T000000Z.dot

Parses `terraform graph` DOT output and ingests the resource dependency graph
into a Spanner property graph (`tf_graph`).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Iterable

from google.cloud import spanner, storage  # type: ignore[attr-defined]
from google.cloud.spanner_v1 import COMMIT_TIMESTAMP

logger = logging.getLogger("ingest_graph")

# Matches:   "[root] aws_iam_role.foo (expand)" [label = "aws_iam_role.foo", ...]
_NODE_RE = re.compile(r'"(\[.*?\])\s+(\S+)\s*(?:\(.*?\))?"')
# Matches edges:  "SRC" -> "DST"
_EDGE_RE = re.compile(r'"([^"]+)"\s*->\s*"([^"]+)"')

_RESOURCE_PREFIXES = (
    "aws_",
    "google_",
    "azurerm_",
    "vault_",
    "consul_",
    "nomad_",
    "hcp_",
    "kubernetes_",
    "helm_",
)


def _clean_addr(raw: str) -> str:
    """Strip DOT decorations: [root] / [module.x] prefix and (expand) suffix."""
    addr = re.sub(r"^\[.*?\]\s+", "", raw)
    addr = re.sub(r"\s*\(.*?\)\s*$", "", addr)
    return addr.strip()


def _leaf_addr(addr: str) -> str:
    """Strip leading module.X. prefixes to get the leaf resource address."""
    leaf = addr
    while leaf.startswith("module."):
        parts = leaf.split(".", 2)
        if len(parts) < 3:
            break
        leaf = parts[2]
    return leaf


def _is_resource(addr: str) -> bool:
    """True if the address looks like a real resource (not a meta-node)."""
    leaf = _leaf_addr(addr)
    skip = {"provider", "var.", "local.", "output.", "module.", "data."}
    return any(leaf.startswith(p) for p in _RESOURCE_PREFIXES) or (
        "." in leaf and not any(leaf.startswith(s) for s in skip)
    )


def parse_dot(dot_text: str) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges) lists from terraform graph DOT output."""
    nodes: dict[str, str] = {}
    edges: list[tuple[str, str]] = []

    for line in dot_text.splitlines():
        edge_m = _EDGE_RE.search(line)
        if edge_m and "->" in line:
            src_raw, dst_raw = edge_m.group(1), edge_m.group(2)
            src = _clean_addr(src_raw)
            dst = _clean_addr(dst_raw)
            if src and dst and src != dst:
                edges.append((src, dst))
            continue

        label_m = re.search(r'label\s*=\s*"([^"]+)"', line)
        if label_m:
            label = label_m.group(1)
            key_m = re.match(r'\s*"([^"]+)"\s*\[', line)
            if key_m:
                nodes[key_m.group(1)] = label

    resource_nodes: list[dict] = []
    for raw_key, label in nodes.items():
        addr = label if label else _clean_addr(raw_key)
        if _is_resource(addr):
            leaf = _leaf_addr(addr)
            parts = leaf.split(".", 1)
            resource_nodes.append(
                {
                    "id": addr,
                    "type": parts[0] if len(parts) == 2 else leaf,
                    "name": parts[1] if len(parts) == 2 else leaf,
                }
            )

    addr_by_raw = {raw_key: (label if label else _clean_addr(raw_key)) for raw_key, label in nodes.items()}
    resource_addr_set = {n["id"] for n in resource_nodes}

    resource_edges: list[dict] = []
    for src_raw, dst_raw in edges:
        src = addr_by_raw.get(src_raw, _clean_addr(src_raw))
        dst = addr_by_raw.get(dst_raw, _clean_addr(dst_raw))
        if src in resource_addr_set and dst in resource_addr_set and src != dst:
            resource_edges.append({"from": src, "to": dst})

    return resource_nodes, resource_edges


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def upsert_into_spanner(
    nodes: list[dict],
    edges: list[dict],
    project_id: str,
    instance_id: str,
    database_id: str,
    repo_uri: str,
    batch_size: int = 500,
) -> None:
    """Upsert nodes and edges into Spanner using batch insert_or_update.

    Auth uses ADC via the default Spanner client. The pipeline service
    account must have roles/spanner.databaseUser on the database.
    """
    client = spanner.Client(project=project_id)
    instance = client.instance(instance_id)
    database = instance.database(database_id)

    # First clear existing rows for this repo so the snapshot is authoritative.
    # ON DELETE CASCADE on DependsOn handles edge cleanup.
    def _delete_repo(transaction):
        transaction.execute_update(
            "DELETE FROM Resource WHERE repo_uri = @repo_uri",
            params={"repo_uri": repo_uri},
            param_types={"repo_uri": spanner.param_types.STRING},
        )

    database.run_in_transaction(_delete_repo)
    logger.info("Cleared existing rows for repo_uri=%s", repo_uri)

    # Insert nodes in batches.
    node_columns = ("repo_uri", "resource_id", "type", "name", "updated_at")
    inserted_nodes = 0
    for batch in _chunks(nodes, batch_size):
        rows = [
            (repo_uri, n["id"], n["type"], n["name"], COMMIT_TIMESTAMP)
            for n in batch
        ]
        with database.batch() as bw:
            bw.insert_or_update(table="Resource", columns=node_columns, values=rows)
        inserted_nodes += len(rows)

    # Insert edges in batches. Note: edges are interleaved in Resource so the
    # parent rows must already exist (which they do thanks to the loop above).
    edge_columns = ("repo_uri", "resource_id", "dst_id", "updated_at")
    inserted_edges = 0
    for batch in _chunks(edges, batch_size):
        rows = [
            (repo_uri, e["from"], e["to"], COMMIT_TIMESTAMP) for e in batch
        ]
        with database.batch() as bw:
            bw.insert_or_update(table="DependsOn", columns=edge_columns, values=rows)
        inserted_edges += len(rows)

    logger.info(
        "Upserted %d nodes, %d edges into spanner://%s/%s for repo_uri=%s",
        inserted_nodes,
        inserted_edges,
        instance_id,
        database_id,
        repo_uri,
    )


def upload_snapshot(dot_text: str, bucket_name: str, snapshot_key: str) -> None:
    """Upload the raw DOT snapshot to GCS for offline debugging."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(snapshot_key)
    blob.upload_from_string(dot_text, content_type="text/plain")
    logger.info("Snapshot uploaded to gs://%s/%s", bucket_name, snapshot_key)


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Ingest terraform graph into Spanner Graph")
    parser.add_argument("--dot-path", required=True, help="Path to terraform graph DOT output")
    parser.add_argument("--repo-uri", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--instance", required=True, help="Spanner instance name")
    parser.add_argument("--database", required=True, help="Spanner database name")
    parser.add_argument("--bucket", required=True, help="GCS bucket for snapshot upload")
    parser.add_argument("--snapshot-key", required=True, help="GCS object key for snapshot upload")
    args = parser.parse_args()

    with open(args.dot_path, encoding="utf-8") as f:
        dot_text = f.read()

    nodes, edges = parse_dot(dot_text)
    logger.info("Extracted %d resource nodes, %d dependency edges", len(nodes), len(edges))

    if not nodes:
        logger.error("No resource nodes found - nothing to ingest")
        sys.exit(1)

    upsert_into_spanner(
        nodes,
        edges,
        project_id=args.project_id,
        instance_id=args.instance,
        database_id=args.database,
        repo_uri=args.repo_uri,
    )

    upload_snapshot(dot_text, args.bucket, args.snapshot_key)


if __name__ == "__main__":
    main()
