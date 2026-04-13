# Build sequence — gcp-hashi-knowledge-base

## Phase 0 — scaffold

- [x] Copy `hashicorp-vertex-ai-rag` tree, strip ephemeral state, init fresh git repo
- [x] Refactor flat `terraform/` into `modules/hashicorp-docs-pipeline/`
- [x] Add `modules/state-backend/` + `terraform/bootstrap/` for GCS state
- [x] `terraform fmt -recursive` + `terraform validate` green at root and bootstrap
- [x] Seed `CLAUDE.md` with GCP hard-fail list; refresh `AGENTS.md` for dual pipeline

## Phase 1 — graph module

- [x] Author `terraform/modules/terraform-graph-store/`
  - [x] `spanner.tf` — `google_spanner_instance`, `google_spanner_database`
        with property-graph DDL (Resource node table + DependsOn edge table)
  - [x] `iam.tf` — `graph-pipeline-sa` with `roles/spanner.databaseUser`,
        `roles/storage.objectAdmin`, `roles/logging.logWriter`,
        `roles/cloudbuild.builds.editor`
  - [x] `storage.tf` — graph staging GCS bucket
  - [x] `workflow.tf` — `workflows/graph_pipeline.yaml`
  - [x] `scheduler.tf` — `0 3 * * 0` weekly cron
  - [x] `variables.tf` / `outputs.tf` / `locals.tf`
- [x] `cloudbuild/scripts/ingest_graph.py` — DOT parser → batch upsert
      to Spanner via `google-cloud-spanner` client (ADC, no manual signing)
- [x] `workflows/graph_pipeline.yaml` — fan-out per-repo Cloud Build executions
- [x] `scripts/run_graph_pipeline.sh` — trigger workflow + poll until done
- [x] `scripts/test_graph.sh` — Spanner smoke test (`SELECT COUNT(*) FROM Resource`)

## Phase 2 — MCP + tasks

- [x] Extend `mcp/server.py` with three Spanner Graph tools:
      `get_resource_dependencies`, `find_resources_by_type`, `get_graph_info`
- [x] Add `mcp/test_server.py` cases for the new tools
- [x] Rename `pipeline:*` Taskfile namespace to `docs:*`
- [x] Add `graph:*` Taskfile namespace

## Phase 3 — docs + CI

- [x] Full README rewrite for dual pipeline
- [x] `docs/ARCHITECTURE.md` — components, data flow, IAM design
- [x] `docs/RUNBOOK.md` — operational runbook
- [x] `scripts/preflight.sh` — gcloud-based preflight
- [x] `.github/workflows/ci.yml` — fmt:check, validate, shellcheck, pytest
- [x] `task ci` green locally

## Phase 4 — validation

- [x] `task ci` green (fmt:check + validate + shellcheck + 79 pytest pass)
- [ ] **Cloud-dependent — left for the user to run against a sandbox project:**
  - [ ] `task preflight` against a real project
  - [ ] Clean `task up` from a fresh sandbox
  - [ ] `task docs:test` retrieval baseline + `reports/` snapshot
  - [ ] `task graph:populate` + `task graph:test`
  - [ ] `task mcp:test` (5-tool MCP smoke — docs + graph)
  - [ ] `task docs:token-efficiency MODE=all`

## Working notes

- Region: `us-central1`
- Graph backend: Spanner Graph
- Repo location: `~/Projects/gcp-hashi-knowledge-base`
- Repo is uncommitted — first commit is up to the user
