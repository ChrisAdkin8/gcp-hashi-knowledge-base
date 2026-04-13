# CLAUDE.md - Project Instructions for Claude Code

## Build & Test Commands

```bash
task ci                # all CI checks: fmt:check + validate + shellcheck + tests
task plan              # terraform plan
task apply             # terraform apply (interactive confirm)
task docs:test         # validate Vertex AI RAG retrieval
task graph:test        # validate Spanner Graph has nodes/edges (when enabled)
task test              # Python unit tests (pytest)
task shellcheck        # lint all shell scripts
task fmt               # format Terraform files
task fmt:check         # check Terraform formatting (no writes)
```

## Code Conventions

- **Python**: type hints required, `ruff check` clean, `logging` module (not `print`), docstrings on public functions
- **Bash**: all scripts must pass `shellcheck`
- **Terraform**: `terraform fmt` + `terraform validate` must pass; no hardcoded project IDs, bucket names, or corpus IDs
- **No secrets**: no credentials or tokens in code, logs, or committed files

## GCP Constraints (Hard Failures)

- **Vertex AI RAG corpus**: do not create the corpus in Terraform - the
  `google_vertex_ai_rag_corpus` resource does not exist in google provider 6.x.
  The workflow `setup_corpus` step auto-provisions and self-heals it.
- **Cloud Build substitutions**: must be underscore-prefixed (`_BUCKET_NAME`).
  Unprefixed names are reserved and rejected by the API.
- **Cloud Workflows**: subworkflow args must be JSON-serialisable. Passing a
  raw `${dict}` to `http.post` body fails with a type error - wrap in `json.encode`.
- **Service account self-impersonation**: `roles/iam.serviceAccountUser` must
  be granted on the SA to itself when Cloud Build runs as that SA, or builds
  fail with `iam.serviceaccounts.actAs` denial.
- **Cloud Scheduler -> Workflows**: must use `oauth_token` (not `oidc_token`)
  when targeting `workflowexecutions.googleapis.com`.
- **Spanner Graph DDL**: `CREATE PROPERTY GRAPH` must reference tables that
  already exist in the same DDL batch - split into two `update_ddl` calls and
  it fails with `Table not found`.
- **Spanner auth**: use the google-cloud-spanner client with ADC. Do not write
  manual signing code.

## Architecture (Key Facts)

- Two pipelines: **Docs** (Vertex AI RAG Engine) and **Graph** (Spanner Graph,
  opt-in via `create_graph_store = true`)
- Orchestration: Cloud Scheduler -> Cloud Workflows -> Cloud Build -> GCS / RAG / Spanner
- MCP server (`mcp/server.py`) exposes both backends to Claude Code
- Blog content comes from RSS/Atom feed inline tags, NOT scraped URLs
- CDKTF content is excluded at every ingestion stage

## Terraform Module Layout

| Module | Path | Opt-in |
|---|---|---|
| hashicorp-docs-pipeline | `terraform/modules/hashicorp-docs-pipeline/` | Always |
| terraform-graph-store | `terraform/modules/terraform-graph-store/` | `create_graph_store = true` (TBD) |
| state-backend | `terraform/modules/state-backend/` | Always (via bootstrap) |

## Don't

- Don't try to manage the RAG corpus in Terraform - the workflow handles it
- Don't use unprefixed Cloud Build substitution variable names
- Don't pass raw maps to `http.post` body in Cloud Workflows - JSON-encode first
- Don't hand-roll Spanner auth - use the official client with ADC
- Don't add `.terraform/`, `__pycache__`, `*.tfstate`, `node_modules`, `.git/`, or logs to version control
