# Architecture

## Overview

This repo provisions two complementary knowledge stores on Google Cloud and exposes both through a single MCP server:

1. **Docs pipeline (Vertex AI RAG Engine)** — ingests HashiCorp's public documentation, GitHub issues, Discourse threads, and blog posts into a Vertex AI RAG corpus for semantic search and grounded generation.
2. **Graph store (Spanner Graph)** — runs `terraform graph` against your Terraform workspaces and loads the resource dependency graph into a Spanner property graph (`tf_graph`). Opt-in via `create_graph_store = true`.

Both pipelines share the same project, service-account model, Cloud Workflows orchestrator, and Cloud Scheduler cadence so the operational story is uniform.

---

## Architecture Diagram

```
Cloud Scheduler (weekly cron)
        │
        ▼
Cloud Workflows (orchestrator)
        │
        ├──► Cloud Build (clone repos → process markdown → upload to GCS)
        │
        ├──► Vertex AI RAG Engine (import files from GCS into corpus)
        │
        └──► Validation (retrieval query to confirm corpus health)
```

---

## Components

### Cloud Scheduler

Triggers the pipeline on a configurable cron schedule (default: weekly, Sundays at 02:00 UTC). Chosen because it is a fully managed, serverless cron service with no infrastructure to maintain — no VMs, no containers, no agents. It integrates natively with Cloud Workflows via an HTTP target with OAuth2 authentication.

### Cloud Workflows

Orchestrates the end-to-end pipeline. Chosen over alternatives (Cloud Functions, Airflow, Step Functions) because:
- **Serverless** — no cluster to provision or maintain.
- **Native GCP integration** — built-in connectors for Cloud Build, Vertex AI, and GCS APIs with automatic OAuth2 auth.
- **Durable execution** — state is persisted between steps; the workflow survives transient failures.
- **Built-in polling** — the `sys.sleep` primitive makes it easy to poll the Cloud Build API for completion.

### Cloud Build

Runs the data ingestion workload inside managed containers. Chosen because:
- **Git operations require a runtime** — cloning 30+ repos needs an execution environment. Cloud Build provides this without persistent VMs.
- **Parallelism** — Cloud Build steps with explicit `waitFor` dependencies can run in parallel (though this pipeline runs largely sequentially to respect memory limits).
- **Managed** — no container registry management required; Cloud Build pulls public images directly.

The pipeline runs on an `E2_HIGHCPU_8` machine type to handle the volume of git clones and markdown processing within the 2-hour timeout.

### GCS (Google Cloud Storage)

Acts as the staging area between Cloud Build and Vertex AI RAG Engine. Processed markdown files are written here by Cloud Build and then imported from here into the RAG corpus. The bucket has:
- **Uniform bucket-level access** — no per-object ACLs.
- **Versioning** — retains previous versions for debugging.
- **90-day lifecycle rule** — automatically deletes objects older than 90 days to prevent unbounded growth.

### Vertex AI RAG Engine

Manages the vector corpus. It handles chunking, embedding, and vector storage internally. The managed infrastructure includes a Spanner instance (billed continuously) and embedding infrastructure. The embedding model is configurable; the default (`text-embedding-005`) provides strong semantic performance for technical documentation.

The RAG corpus is **not** a Terraform-managed resource — `google_vertex_ai_rag_corpus` does not exist in the Google provider 6.x. Instead, the corpus is created once by `scripts/create_corpus.py` (which lists existing corpora first and only creates if no match is found). The corpus ID is persisted in `terraform/corpus.auto.tfvars` and passed through Terraform to the Cloud Scheduler, which includes it in every workflow invocation. The workflow validates that `corpus_id` is present and fails fast if it is missing — it never creates a corpus itself.

---

## Data Flow

The pipeline ingests content from two parallel tracks that converge at the GCS upload step:

```
                    ┌── Git clone pipeline ──────────────────────────────┐
                    │                                                     │
                    │  GitHub repos                                       │
                    │      │  git clone --depth 1                         │
                    │      ▼                                              │
                    │  clone_repos.sh                                     │
                    │      │  Terraform Registry API                      │
                    │      ▼                                              │
                    │  discover_modules.py → clone modules                │
                    │      │                                              │
                    │      ▼                                              │
                    │  process_docs.py                                    │
                    │      │  Extract front matter, add metadata headers  │
                    │      ▼                                              │
                    │  /workspace/cleaned/documentation/                  │
                    │                     /provider/                      │
                    │                     /module/                        │──┐
                    │                     /sentinel/                      │  │
                    └────────────────────────────────────────────────────┘  │
                                                                            │
                    ┌── API fetch pipeline (parallel) ───────────────────┐  │
                    │                                                     │  │
                    │  fetch_github_issues.py → GitHub REST API           │  │
                    │      ▼  /workspace/cleaned/issues/                  │  │
                    │                                                     │  ├──► GCS bucket
                    │  fetch_discuss.py → Discourse JSON API              │  │        │
                    │      ▼  /workspace/cleaned/discuss/                 │  │        ▼
                    │                                                     │──┘  Vertex AI RAG
                    │  fetch_blogs.py → Atom/RSS feeds + HTML scraping   │     Engine corpus
                    │      ▼  /workspace/cleaned/blog/                   │         │
                    │                                                     │         ▼
                    └────────────────────────────────────────────────────┘   Gemini (grounded
                                                                              generation)
```

The API fetch scripts run in parallel with the git clone pipeline. All output is written to `/workspace/cleaned/` and uploaded to GCS in a single `gsutil rsync` step.

---

## Chunking Strategy

Documents are semantically pre-split before upload. Vertex AI RAG Engine applies `fixed_length_chunking` at 1024 tokens with 20-token overlap during import. The larger chunk size matches the pre-split section sizes more closely, reducing unnecessary mid-section splits. The minimal overlap avoids redundant token waste across chunk boundaries.

**Pre-splitting logic (`process_docs.py` — docs, providers, modules, sentinel):**
- Documents are split at `##` and `###` Markdown heading boundaries
- Sections < 200 characters are merged into the preceding section
- Sections > 2,000 characters are further split at code-fence boundaries to stay within the 1024-token chunk window
- Code blocks are compressed (comments stripped, blank lines collapsed) before splitting
- Each output file carries a `section_title` metadata field

**Pre-splitting logic (`fetch_blogs.py` — blog posts):**
- Blog bodies are split at `##` and `###` heading boundaries using the same algorithm
- Sections < 200 characters are merged with the preceding section
- Long-form posts are written as multiple `{slug}_s{i}.md` files rather than one monolithic file

This ensures that content units like "Argument Reference", "Example Usage", and "Import" each become natural chunk boundaries, aligned with how technical documentation is structured.

To adjust chunk boundaries, modify the `MIN_SECTION_SIZE` constant and `_split_large_section` threshold in `cloudbuild/scripts/process_docs.py`. To adjust the RAG Engine chunk size, edit `chunkSize` in `workflows/rag_pipeline.yaml` (currently 1024 tokens, 20-token overlap).

---

## Metadata Schema

Each document body begins with a compact single-line attribution prefix written by the processing scripts. This replaces the previous verbose multi-line YAML header, reducing metadata overhead in retrieved chunks from ~100 tokens to ~15 tokens.

```
[provider:aws] aws_instance — Argument Reference

[discuss:terraform] How do I manage multiple workspaces?

[issue:vault] #1234 (closed): Dynamic secrets not rotating

[blog:terraform] Running Terraform in CI — Setting Up Remote State
```

The full metadata (`product`, `product_family`, `source_type`, `file_name`) is stored separately in `metadata.jsonl` by `generate_metadata.py` and registered with the RAG Engine at import time. The in-body prefix exists solely to orient the LLM about the source of a retrieved chunk. At retrieval time, the MCP server strips these prefixes from returned chunks (the source URI already conveys this information), further reducing per-result token overhead.

### Source-specific notes

- **Issues** (`source_type: issue`): Include the issue body plus up to 10 comments separated by `---`. Pull requests are excluded. Only issues updated in the last 12 months are fetched.
- **Discuss** (`source_type: discuss`): Include the original post plus up to 5 replies (accepted answers prioritised, promoted above other replies). Only topics with at least 1 reply are fetched.
- **Blog** (`source_type: blog`): HashiCorp official blog (Atom feed + archive scraping) and Solutions Engineering blog from Medium (RSS feed). HTML is converted to markdown with link URLs stripped (link text preserved). Heading-split into section files for long posts.

---

## Graph pipeline (Spanner Graph)

The graph pipeline is opt-in (`create_graph_store = true`) and lives in `terraform/modules/terraform-graph-store/`. It mirrors the docs pipeline's orchestration model — Cloud Scheduler → Cloud Workflows → Cloud Build — but the workload and storage target are different.

### Components

| Component | Purpose |
|---|---|
| **Spanner instance** (`hashicorp-rag-graph` by default) | Hosts the property graph database. Regional `regional-us-central1` config, 100 PU minimum (~$65/mo). Must use `edition = "ENTERPRISE"` — the GRAPH feature is not available in STANDARD edition. |
| **Spanner database** (`tf-graph`) | Holds two tables and one property graph: `Resource` (nodes), `DependsOn` (edges, interleaved in `Resource` with `ON DELETE CASCADE`), and `tf_graph` — a `CREATE PROPERTY GRAPH` over both, queryable via GoogleSQL graph syntax. |
| **GCS staging bucket** (`<project>-graph-staging-<hash>`) | Stores raw DOT snapshots from each `terraform graph` run for offline debugging. 30-day lifecycle delete. |
| **Cloud Workflows** (`workflows/graph_pipeline.yaml`) | Fans out per-repo Cloud Build executions in parallel (concurrency 3) and reports per-repo status. |
| **Cloud Build** | Runs four inline steps per workspace repo: install Terraform, clone the workspace repo, run `terraform graph`, then ingest the DOT into Spanner via `cloudbuild/scripts/ingest_graph.py`. |
| **Cloud Scheduler** | Weekly cron (default `0 3 * * 0`) that posts an execution to the workflow. |
| **Service account** (`graph-pipeline-sa`) | Holds `roles/spanner.databaseUser`, `roles/storage.objectAdmin`, `roles/cloudbuild.builds.editor`, `roles/workflows.invoker`, `roles/logging.logWriter`, and self-impersonation for Cloud Build. |

### Data flow

```
Cloud Scheduler (weekly)
    │
    ▼
Cloud Workflows (workflows/graph_pipeline.yaml)
    │  parallel for each workspace_repo_uri (concurrency_limit=3)
    ▼
Cloud Build  ──► clone gcp-hashi-knowledge-base (gitSource for ingest_graph.py)
    step 1: install terraform 1.10.5
    step 2: git clone <workspace_repo> /workspace/ws_repo
    step 3: detect TF root, strip backend blocks, terraform init + graph > graph.dot
    step 4: pip install requirements_graph.txt
            python3 ingest_graph.py --dot-path graph.dot
                                    --repo-uri <workspace_repo>
                                    --instance hashicorp-rag-graph
                                    --database tf-graph
                                    --bucket <staging>
                                    --snapshot-key snapshots/<repo>/<ts>.dot
    │
    ▼
Spanner Graph: insert_or_update Resource + DependsOn rows
    (existing rows for repo_uri are deleted first; the interleaved
     DependsOn table CASCADEs)
    │
    ▼
Workflow smoke query (Spanner REST API): SELECT COUNT(*) FROM Resource
```

### Why Spanner Graph (and not Bigtable / BigQuery / Neo4j)

- **Native property graph in a managed RDBMS.** Spanner ships GA property graphs since 2024 — same instance, same SQL surface, no second store to operate.
- **GoogleSQL recursive CTEs** (`WITH RECURSIVE`) cover the dependency-walk queries the MCP layer needs without forcing GQL/openCypher.
- **Interleaved tables + cascade delete** mean re-ingesting a repo is a single `DELETE FROM Resource WHERE repo_uri = @uri` plus a `batch insert_or_update` — no manual edge cleanup.
- **ADC end-to-end.** No SigV4-equivalent signing dance — `google.cloud.spanner.Client()` uses ADC inside Cloud Build and from the operator's laptop.

### DOT parsing notes

`ingest_graph.py` parses `terraform graph` DOT output via two regexes (`_NODE_RE`, `_EDGE_RE`), strips `[root]` / `[module.x]` prefixes and `(expand)` suffixes, drops meta-nodes (`provider`, `var.*`, `local.*`, `output.*`, `data.*`), and only emits resources whose addresses match a known Terraform provider prefix (`google_`, `aws_`, `azurerm_`, `vault_`, `consul_`, `nomad_`, `hcp_`, `kubernetes_`, `helm_`).

### MCP surface

The graph store is exposed by three tools in `mcp/server.py`:

| Tool | Backed by |
|---|---|
| `get_resource_dependencies(type, name, direction, max_depth, repo_uri)` | A `WITH RECURSIVE` walk over `DependsOn`, joined back to `Resource` for each hit. |
| `find_resources_by_type(resource_type, repo_uri, limit)` | A simple `SELECT … FROM Resource WHERE type = @type` with optional repo filter. |
| `get_graph_info()` | Three count queries (`Resource`, `DependsOn`, distinct `repo_uri`). |
