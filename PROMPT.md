# PROMPT.md — HashiCorp RAG Pipeline Infrastructure

> **Note:** This file documents what was actually built. It reflects the real implementation including all fixes applied during initial deployment. Use it as a reference for understanding the codebase or rebuilding from scratch in a new environment.

---

## Project Overview

A production-grade repository that provisions and operates a Retrieval-Augmented Generation (RAG) system on Google Cloud. The system ingests HashiCorp's public documentation from GitHub repositories and the Terraform Registry API into a Vertex AI RAG Engine corpus, and keeps it current via automated weekly refresh.

A user can clone this repo, set a handful of variables, run `task up REPO_URI=<url>`, and have a fully operational RAG pipeline.

---

## Architecture

See `docs/diagrams/architecture.svg` for the high-level architecture diagram and `docs/diagrams/ingestion_pipeline.svg` for the detailed ingestion pipeline design.

```
Cloud Scheduler (weekly cron)
        │
        ▼
Cloud Workflows (orchestrator)
        │
        ├──► Cloud Build (inline submission via REST API — no trigger resource)
        │         clone repos → discover modules → process markdown → upload to GCS
        │
        ├──► Vertex AI RAG Engine (import files from GCS into corpus)
        │
        └──► Validation (retrieval query to confirm corpus health)
```

All infrastructure is provisioned by Terraform. Data processing runs inside Cloud Build. Cloud Workflows orchestrates the end-to-end pipeline. Cloud Scheduler triggers it on a cron schedule.

**Key design decision:** The workflow submits Cloud Build jobs directly via the Cloud Build REST API (`POST /v1/projects/{project}/locations/{region}/builds`). There is no `google_cloudbuild_trigger` Terraform resource and no GitHub App installation required for public repositories.

### Data Flow — Two Parallel Tracks

The pipeline ingests content from two parallel tracks inside Cloud Build:

1. **Git Clone Track:** Shallow-clones HashiCorp repos (9 core, 14 providers, dynamically-discovered modules, 4 sentinel), runs semantic section splitting via `process_docs.py`, and writes metadata-enriched markdown to `/workspace/cleaned/`.

2. **API Fetch Track:** Runs in parallel with git cloning. Three scripts (`fetch_github_issues.py`, `fetch_discuss.py`, `fetch_blogs.py`) query external APIs and write cleaned output to `/workspace/cleaned/`.

Both tracks converge at a single `gsutil -m rsync` upload step, after which Cloud Workflows calls the Vertex AI RAG Engine import API.

### Chunking Strategy

Documents are processed through a two-stage chunking pipeline:

1. **Semantic pre-splitting** (`process_docs.py`): Documents are split at `##` and `###` heading boundaries before upload. Each section becomes a self-contained file with its own metadata header. Sections smaller than 200 characters are merged with the previous section. Multi-section documents are written as `{stem}_s0.md`, `{stem}_s1.md`, etc.

2. **Fixed-length chunking** (Vertex AI RAG Engine): The RAG Engine's built-in chunker (1024 tokens, 20-token overlap) operates on the pre-split sections. The larger chunk size closely matches the pre-split section sizes, so the chunker rarely introduces additional splits. The minimal overlap reduces redundant token waste across boundaries.

3. **Code block compression** (`process_docs.py`): Before splitting, fenced code blocks are compressed — single-line comments are stripped and runs of blank lines are collapsed. This reduces per-chunk token count without losing semantic value.

4. **Code block integrity** (`process_docs.py`): After semantic splitting, sections exceeding ~2000 characters (approximately 500 tokens) are further split at code block boundaries rather than at arbitrary positions. This ensures fenced code blocks (HCL configurations, CLI examples) are never split mid-block by the downstream fixed-length chunker. Sections without code fences or below the threshold are left intact.

This approach ensures that chunks align with document structure (headings, code blocks, argument tables) rather than arbitrary token boundaries. The 1024-token size closely matches the pre-split section sizes so that the fixed-length chunker rarely introduces additional splits.

### Cross-Source Deduplication

After all data processing scripts complete and before the GCS upload, `deduplicate.py` removes near-duplicate files across sources. It extracts the body text (ignoring metadata headers), normalises whitespace and case, computes a SHA-256 hash, and removes files whose body matches a previously seen file. Files shorter than 100 characters are excluded from dedup (too short to be meaningful duplicates). Files are processed in sorted path order for determinism — the first file encountered wins.

This prevents the same content from entering the corpus through multiple sources (e.g., a Vault feature described in both official docs and a blog post announcement).

---

## Repository Layout

```
.
├── Taskfile.yml                        # Primary entry point (task up / task pipeline:run / etc.)
├── AGENTS.md                           # Operational guide for Claude Code / AI agents
├── PROMPT_VERTEX_AI.md                 # This file — implementation reference
├── README.md
├── .gitignore
├── .github/
│   └── workflows/
│       └── terraform.yml               # CI: fmt check, validate, Trivy scan (WIF auth)
├── docs/
│   ├── ARCHITECTURE.md
│   ├── MCP_SERVER.md
│   ├── RUNBOOK.md
│   └── diagrams/
│       ├── architecture.svg              # High-level architecture diagram
│       └── ingestion_pipeline.svg        # Detailed ingestion pipeline diagram
├── terraform/
│   ├── versions.tf                     # Provider constraints + GCS backend
│   ├── variables.tf                    # Input variables (no rag_bucket_name variable — it's a local)
│   ├── main.tf                         # All resources (see resource inventory below)
│   ├── outputs.tf
│   ├── terraform.tfvars                # gitignored — created by deploy.sh or manually
│   └── terraform.tfvars.example
├── workflows/
│   └── rag_pipeline.yaml               # Cloud Workflows definition (6 steps)
├── cloudbuild/
│   ├── cloudbuild.yaml                 # Cloud Build pipeline definition (inline in workflow)
│   └── scripts/
│       ├── clone_repos.sh              # Clone HashiCorp GitHub repos in parallel
│       ├── discover_modules.py         # Query Terraform Registry for module repos
│       ├── process_docs.py             # Extract and clean markdown from cloned repos
│       ├── fetch_github_issues.py      # Fetch GitHub issues for context
│       ├── fetch_discuss.py            # Fetch HashiCorp Discuss forum posts
│       ├── fetch_blogs.py              # Fetch HashiCorp blog posts
│       ├── deduplicate.py              # Remove near-duplicate files before upload
│       ├── generate_metadata.py        # Generate metadata.jsonl sidecar files for GCS objects
│       ├── requirements.txt            # pyyaml, requests, pytest, beautifulsoup4
│       └── tests/
│           ├── __init__.py
│           ├── test_process_docs.py
│           ├── test_fetch_github_issues.py
│           └── test_deduplicate.py
├── mcp/
│   ├── server.py                       # MCP server — exposes RAG corpus as Claude Code tools
│   ├── test_server.py                  # Smoke tests for MCP server tool functions
│   └── requirements.txt               # mcp, google-cloud-aiplatform
└── scripts/
    ├── deploy.sh                       # End-to-end deploy orchestrator (called by task up)
    ├── bootstrap_state.sh              # Create GCS state bucket (one-time)
    ├── create_corpus.py                # Get-or-create Vertex AI RAG corpus; writes ID to corpus.auto.tfvars
    ├── run_pipeline.sh                 # Trigger workflow via REST API
    ├── setup_claude_vertex.sh          # Configure Claude Code for Vertex AI backend
    ├── setup_mcp.sh                    # Register MCP server with Claude Code settings
    ├── test_retrieval.py               # Validate corpus retrieval quality
    └── test_token_efficiency.py        # Compare token cost: RAG, graph, and combined (RAG+graph) vs raw sources
```

---

## Terraform Implementation

### terraform/versions.tf

```hcl
terraform {
  required_version = ">= 1.5, < 2.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    # Bucket supplied at init time via -backend-config="bucket=<NAME>"
    # Run scripts/bootstrap_state.sh or task bootstrap to create the bucket first.
    prefix = "terraform/state/rag-pipeline"
  }
}
```

### terraform/variables.tf

| Variable | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | (required) | GCP project ID |
| `region` | string | `"us-central1"` | GCP region for all resources |
| `refresh_schedule` | string | `"0 2 * * 0"` | Cron schedule |
| `scheduler_timezone` | string | `"Europe/London"` | Cloud Scheduler timezone |
| `cloudbuild_repo_uri` | string | (required) | GitHub HTTPS URL of this repo |
| `embedding_model` | string | `"publishers/google/models/text-embedding-005"` | Vertex AI embedding model |
| `notification_email` | string | `""` | Email for monitoring alerts |

**`corpus_id`** is a required Terraform variable — created by `scripts/create_corpus.py` and stored in `terraform/corpus.auto.tfvars`. It is passed through to the Cloud Scheduler, which includes it in every workflow invocation. `corpus_display_name`, `chunk_size`, and `chunk_overlap` are not Terraform variables; chunk size (1024 tokens, 20 overlap) is hardcoded in the workflow definition.

**Important:** `rag_bucket_name` is **not** a variable. It is computed in `locals`:
```hcl
locals {
  rag_bucket_name = "${var.project_id}-rag-docs-${substr(sha256(var.project_id), 0, 8)}"
}
```

### terraform/main.tf — Resource Inventory

**APIs** (`google_project_service.apis`): `for_each` over `toset([...])`. Enabled APIs:
- `serviceusage.googleapis.com`
- `aiplatform.googleapis.com`
- `storage.googleapis.com`
- `cloudbuild.googleapis.com`
- `workflows.googleapis.com`
- `cloudscheduler.googleapis.com`
- `monitoring.googleapis.com`

**Service Account** (`google_service_account.rag_pipeline`):
- Account ID: `rag-pipeline-sa`

**Providers:** Both `google` and `google-beta` provider blocks are declared (both target the same project/region). The `google-beta` provider is required for `google_document_ai_processor`.

**IAM roles** (`google_project_iam_member.rag_pipeline_roles`): `for_each` over roles:
- `roles/aiplatform.admin`
- `roles/storage.objectAdmin`
- `roles/cloudbuild.builds.editor`
- `roles/workflows.invoker`
- `roles/logging.logWriter`
- `roles/monitoring.editor`
- `roles/documentai.viewer`
- `roles/documentai.editor`

**Self-impersonation IAM** (`google_service_account_iam_member.rag_pipeline_sa_self_user`):
```hcl
resource "google_service_account_iam_member" "rag_pipeline_sa_self_user" {
  service_account_id = google_service_account.rag_pipeline.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.rag_pipeline.email}"
}
```
This is required because when the workflow (running as `rag-pipeline-sa`) submits a Cloud Build job specifying `serviceAccount: rag-pipeline-sa`, Cloud Build validates `iam.serviceaccounts.actAs` on the target SA — even when it is the same SA as the caller.

**GCS Bucket** (`google_storage_bucket.rag_docs`): Name from `local.rag_bucket_name`. Versioning enabled. 90-day delete lifecycle rule. `force_destroy = true` (allows `task destroy` to clean up buckets with objects).

**Cloud Workflows** (`google_workflows_workflow.rag_pipeline`): Loads `../workflows/rag_pipeline.yaml` via `file()`.

**Cloud Scheduler** (`google_cloud_scheduler_job.rag_weekly_refresh`): HTTP target POSTing to the Workflows executions API. Body uses a **flat dict** for the workflow argument (no "args" wrapper):
```hcl
body = base64encode(jsonencode({
  argument = jsonencode({
    bucket_name     = local.rag_bucket_name
    region          = var.region
    repo_url        = var.cloudbuild_repo_uri
    service_account = google_service_account.rag_pipeline.id
  })
}))
```
The `argument` field value must be a JSON-encoded string (double-encoded). The workflow receives a flat dict and accesses keys via `map.get(args, "key")`. `corpus_id`, `chunk_size`, and `chunk_overlap` are no longer passed from the scheduler — the workflow manages the corpus ID internally, and chunk size is hardcoded in the workflow.

**Document AI** (`google_project_service.documentai_api` + `google_document_ai_processor.layout_parser`): A `LAYOUT_PARSER_PROCESSOR` is provisioned using the `google-beta` provider. The processor must be in the `us` multi-region (Document AI Layout Parser is not available in regional endpoints). This resource enables structured extraction from PDF and HTML source materials.
```hcl
resource "google_document_ai_processor" "layout_parser" {
  provider     = google-beta
  depends_on   = [google_project_service.documentai_api]
  project      = var.project_id
  location     = "us"
  display_name = "rag-layout-parser"
  type         = "LAYOUT_PARSER_PROCESSOR"
}
```

**Monitoring** (conditional on `notification_email != ""`): Email notification channel + alert policies for workflow and Cloud Build failures.

**No Cloud Build trigger resource.** The workflow submits builds inline via the Cloud Build REST API.

---

## Cloud Workflows — workflows/rag_pipeline.yaml

Six steps plus a logging finish step:

1. **init** — Resolve runtime parameters from `args` using `map.get(args, "key")`. All keys are flat (no nesting). `corpus_id` defaults to `""` but must be provided by the caller (scheduler or manual trigger).

2. **validate_corpus_id** — Checks that `corpus_id` is non-empty. If missing, raises an error: `"corpus_id is required. Run scripts/create_corpus.py and pass the ID via corpus.auto.tfvars or --data."` The corpus is created once by `scripts/create_corpus.py` and its ID is baked into the scheduler via Terraform.

3. **submit_build** — POST to `https://cloudbuild.googleapis.com/v1/projects/{project}/locations/{region}/builds` with OAuth2 auth. The build spec is embedded inline. **No `substitutions:` block** — variables are resolved by Workflow expressions before submission. Extracts `build_id` from `build_response.body.metadata.build.id`.

4. **poll_build** — Adaptive backoff loop. GET build status; sleep starts at 10s and grows by ×1.5 up to a 60s cap. Exits on `SUCCESS`, raises on `FAILURE`, `CANCELLED`, or `TIMEOUT`.

5. **import_to_rag** — POST to Vertex AI RAG `ragFiles:import` endpoint. Passes GCS URI and chunking config nested under `rag_file_transformation_config.rag_file_chunking_config.fixed_length_chunking` (1024 tokens, 20 overlap). The larger chunk size closely matches the pre-split section sizes. Documents are pre-split by `process_docs.py`, so the fixed-length chunker rarely introduces additional splits.

6. **validate_retrieval** — Runs 6 retrieval queries in **parallel** (using the Workflows `parallel` keyword with a shared `queries_passed` counter) covering terraform, vault, consul, nomad, packer, and boundary. Each query POSTs to `retrieveContexts` with `vector_distance_threshold: 0.35` and `similarity_top_k: 1`. A query is counted as passing when `distance < 0.3`. Uses `http.default_retry_predicate` with exponential backoff (initial 2s, max 32s, ×2, max 5 retries) to handle transient 503/429 errors from the retrieval API. Zero results for a topic does NOT fail the pipeline.

7. **warm_cache** — Fires a single retrieval query ("HashiCorp product ecosystem overview", `top_k=5`) to warm the Vertex AI index cache after ingestion.

8. **finish_pipeline** — `sys.log` at INFO severity reporting `Queries Passed: N`.

---

## Cloud Build Pipeline — cloudbuild/cloudbuild.yaml

The build spec is embedded inline in `workflows/rag_pipeline.yaml` (not a separate `cloudbuild.yaml` file). Steps:

| Step ID | Image | waitFor | Purpose |
|---|---|---|---|
| `clone-repos` | `gcr.io/cloud-builders/git` | (first step) | Clone HashiCorp repos via `clone_repos.sh` |
| `setup-venv` | `python:3.12-slim` | `clone-repos` | Create `/workspace/.venv` and install `requests beautifulsoup4 google-cloud-storage pyyaml` |
| `discover-modules` | `python:3.12-slim` | `setup-venv` | Query Terraform Registry for module repos |
| `clone-modules` | `gcr.io/cloud-builders/git` | `discover-modules` | Clone module repos from `module_repos.txt` |
| `process-docs` | `python:3.12-slim` | `clone-repos`, `clone-modules`, `setup-venv` | Extract, clean, and split markdown into semantic sections |
| `fetch-github-issues` | `python:3.12-slim` | `setup-venv` | Fetch GitHub issues (parallel) |
| `fetch-discuss` | `python:3.12-slim` | `setup-venv` | Fetch Discuss posts (parallel) |
| `fetch-blogs` | `python:3.12-slim` | `setup-venv` | Fetch blog posts (parallel) |
| `deduplicate` | `python:3.12-slim` | `process-docs`, `fetch-*` | Remove near-duplicate files across sources |
| `generate-metadata` | `python:3.12-slim` | `deduplicate` | Generate `metadata.jsonl` sidecar files mapping GCS URIs to product/family/source_type metadata |
| `upload-to-gcs` | `gcr.io/cloud-builders/gsutil` | `generate-metadata` | `gsutil -m rsync -r -d /workspace/cleaned/ gs://{bucket}/` |

All Python steps use the venv at `/workspace/.venv/bin/python3`. Options: `logging: CLOUD_LOGGING_ONLY`. No `machineType` override.

---

## Scripts

### scripts/deploy.sh

End-to-end deploy orchestrator. Called by `task up`. Steps:
1. Bootstrap GCS state bucket (`scripts/bootstrap_state.sh`)
2. Create (or find) the RAG corpus via `scripts/create_corpus.py --output-id-only` — writes the ID to `terraform/corpus.auto.tfvars`
3. `terraform init` + `terraform apply` — provisions all infrastructure. The scheduler includes `corpus_id` in every workflow invocation.
4. Wait 90 s for IAM propagation, then trigger first pipeline run (`scripts/run_pipeline.sh --wait`) with the corpus ID.

**Workflow data:** Passed as flat JSON dict including `corpus_id`:
```bash
WORKFLOW_DATA=$(python3 -c "import json; print(json.dumps({
  'corpus_id':       '${CORPUS_ID}',
  'bucket_name':     '${RAG_BUCKET}',
  'region':          '${REGION}',
  'repo_url':        '${REPO_URI}',
  'service_account': '${SERVICE_ACCOUNT}',
}))")
```

### scripts/run_pipeline.sh

Triggers the workflow via the Workflows REST API (not `gcloud workflows run` — that command does not support `--async`). Uses `gcloud auth print-access-token` for Bearer auth. Supports `--data <JSON>`, `--wait`, `--project-id`, `--region`.

The `argument` field must be a JSON-encoded string:
```bash
ARGUMENT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "${DATA}")
REQUEST_BODY="{\"argument\": ${ARGUMENT}}"
```

### scripts/create_corpus.py

**Required for deployment.** This script is called by `deploy.sh` to get-or-create the corpus. It lists existing corpora, returns a match by display name, or creates a new one if none is found. The `--output-id-only` flag prints just the numeric ID to stdout (all logging goes to stderr) for machine-readable capture.

Creates a Vertex AI RAG corpus using the `vertexai` SDK. Requires `google-cloud-aiplatform >= 1.143`.

**Breaking API change in v1.143:** `rag.EmbeddingModelConfig` was renamed. Use:
```python
from vertexai import rag

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
```

With `--output-id-only`, prints just the numeric corpus ID to stdout.

### scripts/bootstrap_state.sh

Creates the GCS state bucket if it doesn't exist. Bucket name: `{PROJECT_ID}-tf-state-{sha256(PROJECT_ID)[:8]}`. Sets versioning and uniform bucket-level access.

### scripts/test_retrieval.py

Runs 6 built-in retrieval test queries against the corpus using the Vertex AI SDK. Applies `vector_distance_threshold` (default 0.3) to filter low-relevance results. Exits 0 if all queries return at least 1 result above the threshold. Use `--distance-threshold` to tune the cutoff.

### scripts/setup_claude_vertex.sh

Configures Claude Code to use Vertex AI as its backend. Called by `task claude:setup`.

**What it does:**
1. Authenticates with GCP (skips if already authenticated)
2. Sets environment variables: `CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`, `ANTHROPIC_MODEL`
3. Optionally persists configuration to `~/.bashrc` (with `--persist` flag, idempotent — checks for existing marker before appending)
4. Verifies Vertex AI API is enabled and `claude` CLI is available

**Options:** `--project-id` (required), `--region` (default: `us-east5`), `--model` (default: `claude-sonnet-4-20250514`), `--persist`.

**Taskfile integration:** `task claude:setup` passes `PROJECT_ID` (auto-detected), `CLAUDE_REGION`, `CLAUDE_MODEL`, and `PERSIST` vars through to the script.

### scripts/setup_mcp.sh

Registers the HashiCorp RAG MCP server with Claude Code by writing the `mcpServers` entry into `.claude/settings.local.json`. After running, restart Claude Code to activate the server.

**Arguments:** `--project-id` (required), `--corpus-id` (required), `--region` (default: `us-west1`).

**What it writes:**
```json
{
  "mcpServers": {
    "hashicorp-rag": {
      "command": "/path/to/.venv/bin/python3",
      "args": ["/path/to/mcp/server.py"],
      "env": {
        "VERTEX_PROJECT": "<project-id>",
        "VERTEX_REGION": "<region>",
        "VERTEX_CORPUS_ID": "<corpus-id>"
      }
    }
  }
}
```

**Prerequisites:** Run `task mcp:install` first to create the venv and install the `mcp` and `google-cloud-aiplatform` packages.

### scripts/test_token_efficiency.py

Measures token efficiency of RAG retrieval, Spanner graph queries, and combined (RAG + graph) retrieval versus raw documentation and Terraform source files. Supports four modes:

- **`rag`** — RAG-only queries against the Vertex AI corpus (e.g., "Vault + Terraform provider", "Consul + Nomad scheduling")
- **`graph`** — Graph-only queries against the Spanner graph store (resource lookups by type, graph statistics)
- **`combined`** — Queries that require answers from both backends (e.g., "What IAM roles exist and what does HashiCorp guidance say about least-privilege?"). Each combined query issues a RAG retrieval for documentation context and a graph lookup for structural data, then sums the tokens
- **`all`** — Runs all three sections plus an overall summary

By default, outputs only the summary tables and key findings. Pass `--verbose` to include per-query detail (rows/chunks, token breakdowns, raw source estimates, and combined-query rationale).

### cloudbuild/scripts/generate_metadata.py

Generates `metadata.jsonl` sidecar files alongside processed documents in `/workspace/cleaned/`. Each entry maps a GCS URI to a metadata object containing `product`, `product_family`, and `source_type` fields inferred from the file's path structure. This sidecar format is consumed by the Vertex AI RAG Engine during import to enable metadata-filtered retrieval.

**Product taxonomy (path-based inference):**
- `terraform-provider-{product}/` → `product_family=terraform`, `source_type=provider`
- Core products (`vault`, `consul`, `nomad`, `packer`, `boundary`) → `product_family=<product>`
- `terraform` or `sentinel` path segments → `product_family=terraform`
- Fallback → `product_family=general`

### cloudbuild/scripts/fetch_github_issues.py

Fetches issues from HashiCorp repos via the GitHub REST API. Repos are split into two tiers:

**`REPOS_PRIORITY` (8 repos — always fetched):** Core products and high-traffic providers that generate the bulk of useful RAG content: `terraform`, `vault`, `consul`, `nomad`, `terraform-provider-aws`, `terraform-provider-azurerm`, `terraform-provider-google`, `terraform-provider-kubernetes`.

**`REPOS_EXTENDED` (14 repos — fetched only with `GITHUB_TOKEN`):** Utility providers (`null`, `random`, `tls`, `local`, `http`, etc.) and lower-volume products. These have low issue volume and low RAG relevance; fetching them without a token exhausts the quota before the priority repos are complete.

**Quality filters:**
- Pull requests excluded (GitHub returns PRs via the issues endpoint).
- Issues with body < 100 characters excluded.
- Without `GITHUB_TOKEN`: issues with 0 comments excluded (unresolved one-liners have no resolution content).
- With `GITHUB_TOKEN`: issues with < 2 comments excluded (requires at least one response, ideally a resolution).
- Issues labelled exclusively with low-signal labels (`stale`, `wontfix`, `won't fix`, `duplicate`, `invalid`, `spam`) are excluded. Issues with at least one non-denied label are kept.

**Rate limiting behaviour:**
- Without `GITHUB_TOKEN` (60 req/hr): 1 page per repo max, no comment fetching, fails fast on rate limit (does not wait for reset — a 1-hour wait would exceed the Cloud Build step timeout). Partial data from completed repos is still uploaded.
- With `GITHUB_TOKEN` (5000 req/hr): up to 5 pages per repo, fetches up to 10 comments per issue.

Set `GITHUB_TOKEN` as a Cloud Build secret via Secret Manager for best results. See `AGENTS.md` for setup instructions.

**Metadata fields:** Each issue file includes `source_type`, `product`, `product_family`, `repo`, `title`, `description`, `url` (canonical GitHub issue link), `last_updated` (from `updated_at`), and `resolution_quality` (`high` = closed with maintainer response, `medium` = closed or has maintainer, `low` = open without maintainer).

**Resolution quality scoring:** Each issue is scored based on state and commenter identity. Closed issues with a HashiCorp maintainer response (`author_association: MEMBER/COLLABORATOR/OWNER` or username containing `hashicorp`) are scored `high`. Closed issues without maintainer involvement or open issues with maintainer involvement are scored `medium`. Open issues with only community comments are scored `low`. This metadata enables retrieval-time filtering to prefer authoritative answers.

### cloudbuild/scripts/process_docs.py

Processes cloned repo documentation into cleaned markdown files. Key features:

**Semantic section splitting (stage 1 of the two-stage chunking pipeline):** Documents are split at `##` and `###` heading boundaries. Each section becomes a self-contained file with its own metadata header. Sections smaller than 200 characters are merged with the previous section to avoid tiny fragments. Single-section documents preserve their original path structure; multi-section documents are written as `{stem}_s{N}.md`. This pre-splitting ensures that the Vertex AI fixed-length chunker (stage 2, 1024 tokens) operates on already-coherent content units — keeping HCL code blocks, argument tables, and multi-step examples intact within a single chunk.

**Code block compression:** Before splitting, `_compress_code_blocks()` strips single-line comments and collapses blank lines inside fenced code blocks. This reduces per-chunk token count for the HCL/JSON/YAML examples common in HashiCorp docs.

**Code block integrity:** Oversized sections (>2000 chars) are split between fenced code blocks (` ``` `) rather than at arbitrary positions. Each sub-section retains the original heading for context. This prevents partial HCL configurations from appearing in chunks.

**Enriched metadata header:** Each output file includes:
- `source_type` — documentation, provider, module, or sentinel
- `product` — specific product name (e.g. `aws`, `vault`)
- `product_family` — top-level grouping (e.g. `terraform` for all providers)
- `repo` — source repository name
- `title`, `description` — from YAML front matter
- `url` — canonical GitHub blob URL
- `doc_category` — inferred from path: `resource-reference`, `data-source-reference`, `guide`, `cli-reference`, `api-reference`, `getting-started`, `internals`, `upgrade-guide`, `configuration`, or `documentation`
- `resource_type` — for provider docs under `r/` or `d/` (e.g. `aws_instance`)
- `section_title` — heading text for multi-section documents
- `last_updated` — date of last git commit to the source file (YYYY-MM-DD format); empty if unavailable

**Product taxonomy:** All providers use `product_family: terraform`. Core products use their own name. Sentinel uses `product_family: terraform`. Auto-discovered repos derive the family from their name.

### cloudbuild/scripts/fetch_discuss.py

Fetches Discourse threads from discuss.hashicorp.com. Key features:

**BeautifulSoup HTML conversion:** Uses BeautifulSoup (not regex) to convert Discourse HTML to markdown, preserving code blocks, tables, blockquotes, links, and heading structure.

**Accepted-answer prioritization:** Threads with accepted answers place the accepted answer immediately after the question under a `## Accepted Answer` heading, before other replies. The `has_accepted_answer` metadata field flags these threads.

**365-day lookback window** covers a full year of discussion threads to capture historical resolved content.

**Metadata fields:** `source_type`, `product`, `product_family`, `repo`, `title`, `description`, `url` (canonical Discuss thread link), `last_updated` (from `last_posted_at`), `has_accepted_answer`.

### cloudbuild/scripts/fetch_blogs.py

Fetches blog posts from the HashiCorp blog (Atom feed + archive pages) and Medium SE blog (RSS feed).

**Product family detection:** Scans post title (weighted 3x) and full body for product keywords. Returns the product family with the highest weighted frequency. Keywords include `terraform`, `terraform cloud`, `terraform enterprise`, `hcp terraform`, `vault`, `consul`, `nomad`, `packer`, `boundary`, `waypoint`, `sentinel`, and `vagrant`. Falls back to `hashicorp` if no product keywords are found.

**365-day lookback window** covers a full year of blog posts.

**Metadata fields:** `source_type`, `product`, `product_family`, `repo`, `title`, `description`, `url` (original blog post link), `last_updated` (from publication date).

---

## Deployed State

| Resource | Value |
|---|---|
| GCP project | `hc-e96dc2a274054e128e6309abba6` |
| Region | `us-west1` |
| Corpus display name | `hashicorp-knowledge-base` (created by `scripts/create_corpus.py`) |
| Chunk size | 1024 tokens |
| Chunk overlap | 20 tokens |
| RAG bucket | `hc-e96dc2a274054e128e6309abba6-rag-docs-878aa953` |
| State bucket | `hc-e96dc2a274054e128e6309abba6-tf-state-878aa953` |
| Workflow name | `rag-hashicorp-pipeline` |
| Service account | `rag-pipeline-sa@hc-e96dc2a274054e128e6309abba6.iam.gserviceaccount.com` |
| GitHub repo | `https://github.com/ChrisAdkin8/hashicorp-vertex-ai-rag` |

**Region constraint:** Vertex AI RAG Engine is restricted in `us-central1`, `us-east1`, and `us-east4` for new projects. Use `us-west1` (or `europe-west1`, `asia-northeast1`).

---

## CI/CD — .github/workflows/terraform.yml

Runs on push and PR. Uses Workload Identity Federation (WIF) — no service account keys. Steps:
- `terraform fmt -check -recursive`
- `terraform validate`
- Trivy vulnerability scan

---

## Known Gotchas

| Issue | Root Cause | Fix |
|---|---|---|
| BSD sed `\s` not supported | macOS uses BSD sed; `-E` mode doesn't support `\s` | Use `[ ]*` instead |
| Python venv split (3.13/3.14) | `.venv` has symlinked `python3` (3.13) but `pip` pointed to 3.14 | Use `.venv/bin/python3 -m pip install` |
| RAG Engine region restriction | New projects restricted from `us-central1`, `us-east1`, `us-east4` | Use `us-west1` |
| Workflow flat args | `map.get(args, "key")` requires flat dict — no "args" wrapper | Pass `{"corpus_id": "...", ...}` not `{"args": {...}}` |
| Cloud Build substitution error | Inline builds reject unused substitutions | Remove `substitutions:` block from inline build spec |
| Self-impersonation IAM | Cloud Build validates `actAs` even when SA matches caller | Add `roles/iam.serviceAccountUser` to SA on itself |
| `gcloud workflows run --async` | Flag doesn't exist | Use Workflows REST API with `curl` or `run_pipeline.sh` |
| `vertexai.rag.EmbeddingModelConfig` | Renamed in v1.143 | Use `RagEmbeddingModelConfig` + `RagVectorDbConfig` (relevant only to `create_corpus.py` — not the deploy path) |
| `fetch-github-issues` timeout | Unauthenticated GitHub API waits 1hr on rate limit; exceeds 1800s step timeout | Script fails fast on rate limit; only 8 priority repos fetched without token |
| RAG import chunking API path | `rag_file_chunking_config` was moved; it's no longer a direct child of `import_rag_files_config` | Nest under `rag_file_transformation_config.rag_file_chunking_config.fixed_length_chunking` |
| Document AI processor location | `LAYOUT_PARSER_PROCESSOR` is only available in the `us` multi-region | Set `location = "us"` in `google_document_ai_processor`; do not use the deployment region |
| Corpus race condition (fixed) | Concurrent workflow executions each created a new corpus when none existed | Corpus is now created once by `scripts/create_corpus.py` (get-or-create) and its ID is passed explicitly to every workflow execution |
| Chunk size change | Chunk size is now 1024 tokens with 20 token overlap | Hardcoded in the workflow; no Terraform variable to override |

---

## MCP Server

`mcp/server.py` implements a [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the Vertex AI RAG corpus as two tools callable from Claude Code:

- **`search_hashicorp_docs`** — semantic search with optional `product`, `product_family`, and `source_type` metadata filters
- **`get_corpus_info`** — inspect active project/region/corpus configuration

Once registered, Claude Code calls these tools automatically when answering questions about HashiCorp products — no manual retrieval step required.

**Environment variables:**
- `VERTEX_PROJECT` (or `GOOGLE_CLOUD_PROJECT`) — GCP project ID
- `VERTEX_REGION` — GCP region (default: `us-west1`)
- `VERTEX_CORPUS_ID` — Vertex AI RAG corpus numeric ID

**Authentication:** Google Application Default Credentials (ADC). Run `gcloud auth application-default login` before starting the server.

**Setup:**
```bash
task mcp:install                                               # install mcp + google-cloud-aiplatform into .venv
scripts/setup_mcp.sh --project-id <id> --corpus-id <id>      # write .claude/settings.local.json
# restart Claude Code to activate
```

**Path-based metadata inference:** The server infers `product`, `product_family`, and `source_type` from the GCS object path returned in retrieval results. Filters passed to `search_hashicorp_docs` are applied client-side after retrieval.

**Token efficiency features:**
- **Semantic reranking** — retrieval uses `semantic-ranker-512@latest` to re-score results, improving relevance so a lower `top_k` delivers the same quality
- **Per-document deduplication** — only the highest-scoring chunk per source URI is returned, eliminating redundant context from the same file
- **Compact output format** — results use a single-line header (`[N] path (score)`) with the `gs://bucket/` prefix stripped, reducing framing overhead

---

## Diagrams

Two hand-crafted SVG diagrams are maintained in `docs/diagrams/`:

- **`architecture.svg`** — High-level architecture showing all GCP resources: Cloud Scheduler, Cloud Workflows, Cloud Build (with Git Clone and API Fetch tracks), GCS bucket, Vertex AI RAG Engine (Embedding → Vector Store → Retrieval → Metadata Filter), consumers (Gemini, Claude Code, Claude/OpenAI, MCP Server), Cloud Monitoring, and Service Account. Infrastructure-as-code layer shows Terraform and GitHub Actions CI.

- **`ingestion_pipeline.svg`** — Detailed pipeline design: Scheduler → Workflows (validate corpus_id) → Cloud Build (parallel tracks with all scripts, process-docs section splitting, fetch configurations, generate-metadata) → GCS staging → Vertex AI RAG Engine (chunk, embed, index, validate) → cache warming.

Both use a dark theme (`#0a0a0f` background) with high-contrast colored lines and text. They are linked from the README using centered `<p align="center">` HTML blocks.

---

## Token Efficiency

The RAG corpus provides significant token savings compared to pasting raw documentation into LLM context windows. With `top_k=3` and 1024-token chunks, a typical retrieval returns 900–2,000 tokens of focused, relevant content — compared to 8,000–12,000+ tokens when pasting full documentation pages. At retrieval time, metadata header prefixes are stripped from chunks and near-identical content across different source documents is deduplicated by content fingerprint, further reducing token waste. For cross-product queries, the savings compound: a question spanning three products retrieves ~2,000 tokens from the corpus versus ~25,000 tokens from raw sources.

The Spanner graph store adds a complementary efficiency layer: structured dependency lookups return compact tabular results (resource IDs, names, relationships) instead of requiring users to parse raw `.tf` files or `terraform graph` DOT output.

Combined queries — questions that require both documentation context and infrastructure structure — demonstrate the largest practical benefit. For example, auditing IAM roles against least-privilege guidance requires both the deployed IAM bindings (graph) and HashiCorp best-practice documentation (RAG). The combined retrieval delivers focused answers from both backends while using a fraction of the tokens needed to manually assemble the equivalent raw sources.

This efficiency gain is a direct consequence of the multi-stage optimisation pipeline: semantic pre-splitting with code block compression at ingestion, minimal chunk overlap (20 tokens), larger chunk size (1024 tokens) matching pre-split sections, semantic reranking at retrieval, per-document and cross-document content deduplication, metadata header stripping, and a compact output format that minimises framing overhead.

---

## Code Quality Requirements

- All Python must have type hints on all functions.
- All Python must pass `ruff check` with no errors.
- All bash scripts must pass `shellcheck` with no errors.
- All Terraform must pass `terraform fmt` and `terraform validate`.
- Use `logging` module in Python for operational output (not bare `print()`).
- All functions must have docstrings.
- Cloud Build steps must have explicit `waitFor` dependencies.
- Never hardcode project IDs, bucket names, or corpus IDs in committed files.
- Never include secrets or credentials.
- All GCP API calls in Cloud Workflows must use `auth: type: OAuth2`.
