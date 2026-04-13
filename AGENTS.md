# AGENTS.md - Universal AI Operational Guide

This repository provisions a high-precision Vertex AI RAG pipeline plus an
opt-in Spanner Graph for Terraform dependency analysis, both surfaced through
a unified MCP server, for the HashiCorp ecosystem (Terraform, Vault, Consul,
Nomad, Packer, Boundary, Waypoint).

---

## Quick Commands

| Category | Command |
| :--- | :--- |
| **Deploy** | `task up REPO_URI={url}` |
| **Docs pipeline** | `task docs:run` (corpus auto-provisioned on first run by `setup_corpus`) |
| **Docs validation** | `task docs:test` |
| **Graph populate** | `task graph:populate GRAPH_REPO_URIS="https://github.com/org/infra"` |
| **Graph validate** | `task graph:test` |
| **MCP setup** | `task mcp:setup` |
| **Terraform** | `task plan` \| `task apply` \| `task validate` |

---

## Architectural Pillars

* **Idempotency**: The RAG corpus is **not** a Terraform resource - the
  `google_vertex_ai_rag_corpus` resource does not exist in google provider 6.x.
  The corpus is auto-provisioned by the `setup_corpus` step in
  `workflows/rag_pipeline.yaml`: it lists corpora, matches by display name, and
  creates one if none is found. Every pipeline run is self-healing.
* **Semantic Chunking**: `process_docs.py` splits docs at `##`/`###` heading
  boundaries before upload. Vertex AI RAG Engine then applies
  `fixed_length_chunking` (500 tokens, 100 overlap) during import - the
  pre-splitting ensures chunk boundaries land on structural boundaries rather
  than mid-sentence.
* **Metadata Engine**: `generate_metadata.py` produces a `metadata.jsonl` map
  for precision filtering. Supports `product_family` and `source_type` facets.
* **Spanner Graph (opt-in)**: `terraform plan` over workspace repos is parsed
  by rover; nodes and edges are upserted into a Spanner property graph via the
  google-cloud-spanner client. Auth is ADC - no manual signing.
* **Parallel Validation**: Multi-product test queries run simultaneously to
  verify retrieval quality across the stack.

---

## Project State & Resources

| Resource | Value / Path |
| :--- | :--- |
| **Project ID** | _set in `terraform/terraform.tfvars`_ |
| **Region** | `us-central1` (default; configurable via `region` var) |
| **Corpus** | Auto-provisioned by `workflows/rag_pipeline.yaml` |
| **Spanner instance** | `hashicorp-rag-graph` (only when `create_graph_store = true`) |
| **Workflow (docs)** | `workflows/rag_pipeline.yaml` |
| **Workflow (graph)** | `workflows/graph_pipeline.yaml` (TBD) |
| **Metadata** | `cloudbuild/scripts/generate_metadata.py` |
| **MCP server** | `mcp/server.py` |

---

## Critical Constraints

* **Region**: Verify Vertex AI RAG Engine availability in your chosen region
  before changing the default. `us-central1` is the supported default for this
  repo.
* **IAM**: The pipeline service account requires `roles/aiplatform.admin`,
  `roles/storage.objectAdmin`, `roles/cloudbuild.builds.editor`,
  `roles/workflows.invoker`, `roles/documentai.editor`, and (when graph store
  is enabled) `roles/spanner.databaseUser`.
* **Filtering syntax**: Use **CEL** for retrieval filters:
  `metadata.product_family == "nomad"`.
* **Chunking**: The workflow uses `fixed_length_chunking` (500 tokens, 100
  overlap) in `workflows/rag_pipeline.yaml`. Pre-split content alignment is
  handled by `process_docs.py` and `fetch_blogs.py`.

---

## Maintenance Workflow

1. **Modify logic**: Update this file or `CLAUDE.md` to refine standards.
2. **Apply infra**: `task apply` syncs Terraform.
3. **Sync knowledge**: `task docs:run` re-ingests docs; `task graph:populate`
   re-ingests the dependency graph.
4. **Validate**: `task docs:test && task graph:test && task mcp:test`.
