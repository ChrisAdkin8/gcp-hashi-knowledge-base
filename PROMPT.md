# PROMPT.md — HashiCorp RAG Pipeline Infrastructure

> **Note:** This file documents what was actually built. It reflects the real
> implementation including all fixes applied during initial deployment. Use it
> as a reference for understanding the codebase or rebuilding from scratch.

---

## Project Overview

A production-grade repository that provisions and operates a RAG system on
Google Cloud. Ingests HashiCorp documentation from GitHub repos and the
Terraform Registry API into a Vertex AI RAG Engine corpus, kept current via
automated weekly refresh.

Clone, set variables, run `task up REPO_URI=<url>` — fully operational pipeline.

---

## Architecture

See `docs/ARCHITECTURE.md` for full component tables, data flow diagrams, and
IAM design. See `docs/diagrams/architecture.svg` and
`docs/diagrams/ingestion_pipeline.svg` for visual diagrams.

```
Cloud Scheduler (weekly cron)
        │
        ▼
Cloud Workflows (orchestrator)
        │
        ├──► Cloud Build (clone repos → process markdown → upload to GCS)
        ├──► Vertex AI RAG Engine (import files from GCS into corpus)
        └──► Validation (retrieval queries to confirm corpus health)
```

**Key design decisions:**
- Workflow submits Cloud Build jobs via REST API — no `google_cloudbuild_trigger`
  resource, no GitHub App installation required for public repos.
- Two parallel tracks inside Cloud Build: **git clone** (9 core + 14 providers +
  modules + sentinel) and **API fetch** (issues, discuss, blogs). Both converge
  at a single `gsutil rsync` upload step.
- The RAG corpus is **not** a Terraform resource (`google_vertex_ai_rag_corpus`
  does not exist in google provider 6.x). Created once by
  `scripts/create_corpus.py`; ID persisted in `terraform/corpus.auto.tfvars`.

### Chunking Strategy

Two-stage pipeline:
1. **Semantic pre-splitting** (`process_docs.py`): split at `##`/`###` headings.
   Sections < 200 chars merged with previous; sections > 2000 chars split at
   code-fence boundaries. Code blocks compressed (comments stripped, blanks
   collapsed).
2. **Fixed-length chunking** (RAG Engine): 1024 tokens, 20-token overlap.
   Matches pre-split section sizes closely — rarely introduces additional splits.

### Cross-Source Deduplication

`deduplicate.py` removes near-duplicates by SHA-256 of normalised body content
before upload. Files < 100 chars excluded. Sorted path order for determinism.

---

## Terraform Implementation

### Provider & Backend

- `google` + `google-beta` providers `~> 6.0`. Required version `>= 1.5, < 2.0`.
- GCS backend — bucket supplied at init via `-backend-config="bucket=<NAME>"`.

### Key Variables

| Variable | Default | Description |
|---|---|---|
| `project_id` | (required) | GCP project ID |
| `region` | `"us-central1"` | GCP region |
| `corpus_id` | (required) | Created by `scripts/create_corpus.py`, stored in `corpus.auto.tfvars` |
| `cloudbuild_repo_uri` | (required) | GitHub HTTPS URL of this repo |
| `refresh_schedule` | `"0 2 * * 0"` | Cron schedule |
| `embedding_model` | `"publishers/google/models/text-embedding-005"` | Vertex AI embedding model |

`rag_bucket_name` is a **local**, not a variable:
`"${var.project_id}-rag-docs-${substr(sha256(var.project_id), 0, 8)}"`.

### Key Resources

- **Service Account** (`rag-pipeline-sa`) with 7 IAM roles including
  `aiplatform.admin`, `storage.objectAdmin`, `cloudbuild.builds.editor`.
- **Self-impersonation IAM** — required because Cloud Build validates `actAs`
  even when the SA matches the caller.
- **GCS Bucket** — versioning, 90-day lifecycle, `force_destroy = true`.
- **Cloud Workflows** — loads `../workflows/rag_pipeline.yaml` via `file()`.
- **Cloud Scheduler** — HTTP target posting to Workflows API. Body uses flat
  dict (no "args" wrapper), double-JSON-encoded `argument` field.
- **Document AI** — `LAYOUT_PARSER_PROCESSOR` via `google-beta`, must use `us`
  multi-region (not the deployment region).
- **Monitoring** — conditional on `notification_email != ""`.

---

## Cloud Workflows — workflows/rag_pipeline.yaml

Eight steps:

| # | Step | Action |
|---|---|---|
| 1 | `init` | Resolve params from flat `args` dict |
| 2 | `validate_corpus_id` | Fail fast if `corpus_id` is empty |
| 3 | `submit_build` | POST to Cloud Build REST API (no trigger resource) |
| 4 | `poll_build` | Adaptive backoff (10s → ×1.5 → 60s cap) |
| 5 | `import_to_rag` | POST to `ragFiles:import` with `fixed_length_chunking` (1024/20) |
| 6 | `validate_retrieval` | 6 parallel retrieval queries across product families |
| 7 | `warm_cache` | Single retrieval query to warm the index |
| 8 | `finish_pipeline` | Log pass count |

---

## Cloud Build Pipeline

Inline build spec in `workflows/rag_pipeline.yaml`. Key steps:

| Step | waitFor | Purpose |
|---|---|---|
| `clone-repos` | — | Shallow-clone HashiCorp repos |
| `setup-venv` | clone-repos | Install Python deps |
| `discover-modules` | setup-venv | Query Terraform Registry |
| `clone-modules` | discover-modules | Clone module repos |
| `process-docs` | clone-repos, clone-modules | Semantic section splitting |
| `fetch-github-issues` | setup-venv | GitHub Issues (parallel) |
| `fetch-discuss` | setup-venv | Discourse threads (parallel) |
| `fetch-blogs` | setup-venv | Blog posts (parallel) |
| `deduplicate` | process-docs, fetch-* | Cross-source dedup |
| `generate-metadata` | deduplicate | `metadata.jsonl` sidecars |
| `upload-to-gcs` | generate-metadata | `gsutil -m rsync` |

---

## Data Sources

### Git-Cloned

| Source type | Repos | Notes |
|---|---|---|
| `documentation` | 9 repos via `web-unified-docs` + standalone | `repo_dir` override for multi-product shared repos |
| `provider` | 14 Terraform provider repos | `clone_repo_optional()` (soft-fail) |
| `module` | Terraform Registry (dynamic discovery) | `discover_modules.py` → `clone_modules.sh` |
| `sentinel` | 4 policy library repos | `clone_repo_optional()` |

### API-Fetched

| Source type | Source | Key parameters |
|---|---|---|
| `issue` | GitHub REST API | 8 priority repos (22 with token), 30-day lookback, quality filters |
| `discuss` | Discourse JSON API | 9 categories, 180-day lookback, min 1 reply, accepted-answer promotion |
| `blog` | Atom/RSS feeds | **Inline content** (NOT scraped URLs — Cloudflare blocks scraping) |

### Document Processing

- **Semantic splitting** at `##`/`###` headings with code block compression.
- **Attribution prefix**: `[source_type:product] Title — Section` (~15 tokens
  vs ~100 for verbose YAML headers).
- **CDKTF exclusion**: layered across `process_docs.py`, `fetch_blogs.py`,
  `fetch_discuss.py`, `fetch_github_issues.py`.
- **web-unified-docs**: authoritative source for Vault/Consul/Nomad/TFE/HCP
  Terraform. Uses `repo_dir` override so multiple products share one clone.

---

## Scripts

| Script | Purpose |
|---|---|
| `deploy.sh` | End-to-end orchestrator: bootstrap → corpus → apply → pipeline |
| `create_corpus.py` | Get-or-create RAG corpus; writes ID to `corpus.auto.tfvars` |
| `run_pipeline.sh` | Trigger workflow via REST API (not `gcloud workflows run`) |
| `bootstrap_state.sh` | Create GCS state bucket (idempotent) |
| `test_retrieval.py` | 6 retrieval queries with distance threshold validation |
| `test_token_efficiency.py` | Compare RAG/graph/combined vs raw sources (4 modes) |
| `setup_claude_vertex.sh` | Configure Claude Code for Vertex AI backend |
| `setup_mcp.sh` | Register MCP server in `.claude/settings.local.json` |

See `docs/MCP_SERVER.md` for MCP server tool reference and configuration.

---

## Known Gotchas

| Issue | Fix |
|---|---|
| BSD sed `\s` not supported | Use `[ ]*` instead |
| RAG Engine region restriction (new projects) | Use `us-west1` |
| Workflow flat args required | Pass `{"corpus_id": "..."}` not `{"args": {...}}` |
| Inline builds reject unused substitutions | Remove `substitutions:` block |
| Self-impersonation IAM | Add `roles/iam.serviceAccountUser` to SA on itself |
| `gcloud workflows run --async` doesn't exist | Use REST API via `run_pipeline.sh` |
| `EmbeddingModelConfig` renamed in SDK v1.143 | Use `RagEmbeddingModelConfig` + `RagVectorDbConfig` |
| GitHub API rate limit (60 req/hr) | Set `GITHUB_TOKEN` in Cloud Build secrets |
| RAG import chunking API path moved | Nest under `rag_file_transformation_config.rag_file_chunking_config` |
| Document AI processor location | Must use `us` multi-region, not deployment region |
| Corpus race condition | Fixed — corpus created once by `create_corpus.py`, ID passed explicitly |

---

## CI/CD

`.github/workflows/terraform.yml` — runs on push/PR via Workload Identity
Federation (no SA keys): `terraform fmt -check`, `terraform validate`, Trivy scan.

`task ci` runs locally via parallel deps: `fmt:check`, `validate`, `shellcheck`, `test`.

---

## Costs

| Service | Notes |
|---|---|
| Vertex AI RAG Engine (Spanner) | ~$0.90/hr continuously while corpus exists |
| GCS | ~$0.02/GB/month |
| Cloud Build (E2_HIGHCPU_8) | ~$0.064/min; 30–60 min/week |
| Cloud Workflows | Negligible |

Vertex AI RAG Engine is the dominant cost. Delete the corpus to stop Spanner billing.
