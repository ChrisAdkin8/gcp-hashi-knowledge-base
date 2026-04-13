# Runbook — HashiCorp RAG Pipeline

## Initial deployment

The entire pipeline — infrastructure, corpus creation, and first data ingestion — is deployed with a single command:

```bash
task up REPO_URI=https://github.com/my-org/hashicorp-vertex-ai-rag
```

`PROJECT_ID` is auto-detected from `gcloud config`. Override with `PROJECT_ID=<id>` if needed.

`task up` runs preflight checks first, then calls `scripts/deploy.sh`, which runs three idempotent steps:

0. **Preflight** — validates CLI tools (terraform >= 1.5, gcloud, python3 >= 3.11, jq, shellcheck), GCP authentication and project access, Python packages (`google-cloud-aiplatform`, `pyyaml`, `requests`, `pytest`, `beautifulsoup4`), repository file integrity, and Terraform formatting/validation
1. **Bootstrap** — creates the GCS state bucket and initialises Terraform remote backend
2. **Apply** — provisions all GCP resources (service account, IAM, GCS bucket, Cloud Workflows workflow, Cloud Scheduler job, Document AI processor, monitoring)
3. **Pipeline** — triggers the first data ingestion run. The workflow auto-provisions the RAG corpus on this first run if one does not already exist.

The Vertex AI RAG corpus is **not** a Terraform-managed resource (`google_vertex_ai_rag_corpus` is absent from the Google provider 6.x). The corpus is created automatically by the `setup_corpus` step in the Cloud Workflow: it lists corpora in the project, matches by display name (`HashiCorp-Vertex-RAG-Corpus`), and calls the RAG API to create one if none is found. No separate creation step or `corpus.auto.tfvars` file is needed.

Re-running `task up` is safe — each step detects existing state and skips automatically.

### Running preflight checks independently

You can run all preflight checks without deploying:

```bash
task preflight
```

Or run individual check groups:

```bash
task preflight:tools      # CLI tools and version requirements
task preflight:auth       # GCP auth, ADC, project access, API status
task preflight:python     # Python package availability
task preflight:files      # Repository file inventory and permissions
task preflight:terraform  # Terraform fmt and validate
```

### Re-deploying to a second environment

```bash
task up \
  PROJECT_ID=my-project-staging \
  REGION=europe-west2           \
  REPO_URI=https://github.com/my-org/hashicorp-vertex-ai-rag
```

For a different environment, pass `PROJECT_ID` explicitly to override the auto-detected value.

Each environment gets its own corpus via a separate Terraform workspace or working directory.

---

## Monitoring

### Cloud Console Links

Replace `PROJECT_ID` and `REGION` with your values.

| Resource | URL |
|---|---|
| Cloud Workflows executions | `https://console.cloud.google.com/workflows/workflow/REGION/rag-hashicorp-pipeline/executions?project=PROJECT_ID` |
| Cloud Build history | `https://console.cloud.google.com/cloud-build/builds?project=PROJECT_ID` |
| Cloud Scheduler jobs | `https://console.cloud.google.com/cloudscheduler?project=PROJECT_ID` |
| Cloud Logging | `https://console.cloud.google.com/logs/query?project=PROJECT_ID` |
| Alert policies | `https://console.cloud.google.com/monitoring/alerting?project=PROJECT_ID` |

### Key Log Queries

**Workflow execution failures:**
```
resource.type="workflows.googleapis.com/Workflow"
severity>=ERROR
```

**Cloud Build failures:**
```
resource.type="build"
jsonPayload.status="FAILURE"
```

**Pipeline summary (process_docs output):**
```
resource.type="build"
textPayload=~"files processed"
```

---

## Investigating a Failed Run — troubleshoot

### Step 1 — Identify the failure point

1. Open Cloud Workflows executions in the Console.
2. Find the failed execution.
3. Click it and expand the step tree. The first red step is the failure point.

### Step 2 — Check Cloud Build logs

If the failure is in the `submit_build` or `poll_build` step:

1. Note the build ID from the workflow execution details.
2. Navigate to Cloud Build → History → find the build by ID.
3. Expand the failing step and read the logs.

Common build failures:
- **`clone_repos.sh` timeout** — increase Cloud Build step timeout or reduce the number of repos.
- **`process_docs.py` crash** — a malformed markdown file caused an unhandled exception. Check logs for the filename and fix `process_docs.py`.
- **`gsutil rsync` permission denied** — the service account lacks `storage.objectAdmin` on the bucket. Check IAM bindings.

### Step 3 — Check workflow execution errors

If the failure is in `setup_corpus`, `import_to_rag`, or `validate_retrieval`:

- `setup_corpus` failure: the RAG API call to list or create the corpus failed. Check IAM — the service account needs `roles/aiplatform.admin`. Inspect the HTTP response body in the workflow step details.
- `import_to_rag` failure: the Vertex AI RAG Engine API returned an error. Verify the corpus was created by the `setup_corpus` step earlier in the same execution. Check the workflow step details for the corpus ID that was resolved.
- `validate_retrieval` warning: zero results returned — the corpus may be empty or the import failed silently. Check the import response in the workflow step details.

### Common Errors

| Error | Cause | Fix |
|---|---|---|
| `Permission denied on GCS bucket` | Service account missing `storage.objectAdmin` | Re-run `terraform apply` to reconcile IAM |
| `Corpus not found` | Corpus was deleted out-of-band between pipeline runs | Trigger a new pipeline run — `setup_corpus` will recreate it automatically |
| `Cloud Build timeout` | Too many repos to clone within 7200s | Increase timeout or reduce repo count |
| `Registry API rate limit` | `discover_modules.py` hit the public API rate limit | Add `GITHUB_TOKEN` environment variable to Cloud Build substitutions |
| `Workflow execution quota exceeded` | Too many concurrent executions | Check for stuck executions and cancel them |
| `Preflight: MISSING google-cloud-aiplatform` | Vertex AI SDK not installed | `pip install google-cloud-aiplatform` |
| `Preflight: FAIL Terraform < 1.5` | Terraform version too old | Upgrade to >= 1.5: `brew upgrade terraform` or download from releases |
| `Preflight: FAIL ADC not set` | Application Default Credentials missing | Run `gcloud auth application-default login` |
| `GitHub API rate limit (403)` | `fetch_github_issues.py` exceeded 60 req/hr | Set `GITHUB_TOKEN` env var in Cloud Build for 5000 req/hr |
| `Discourse rate limit (429)` | `fetch_discuss.py` hit discuss.hashicorp.com rate limit | Script retries automatically; increase `REQUEST_DELAY` if persistent |
| `Blog fetch timeout` | `fetch_blogs.py` timed out scraping archive pages | Increase step timeout or reduce `page > 50` safety limit |
| `Medium RSS empty` | `fetch_blogs.py` returned 0 SE posts | Check if `medium.com/feed/hashicorp-engineering` is still active |

---

## How to add a new provider

1. Open `cloudbuild/scripts/clone_repos.sh`.
2. Add an entry to the `PROVIDER_REPOS` associative array:
   ```bash
   ["terraform-provider-<NAME>"]="https://github.com/hashicorp/terraform-provider-<NAME>.git"
   ```
3. Open `cloudbuild/scripts/process_docs.py`.
4. Add an entry to `REPO_CONFIG`:
   ```python
   "terraform-provider-<NAME>": {
       "source_type": "provider",
       "product": "<NAME>",
       "docs_subdir": "website/docs",
   },
   ```
5. Commit, push, and trigger the pipeline.

---

## How to Add a New HashiCorp Product Repo

1. Open `cloudbuild/scripts/clone_repos.sh`.
2. Add to `CORE_REPOS`:
   ```bash
   ["<product>"]="https://github.com/hashicorp/<product>.git"
   ```
3. Add to `REPO_CONFIG` in `process_docs.py` with the correct `docs_subdir`.
4. Trigger the pipeline.

---

## How to Force a Full Re-import

### Option A — Re-upload GCS objects and re-trigger

```bash
gsutil -m rm -r gs://<BUCKET>/
task docs:run
```

### Option B — Delete and recreate the corpus

The corpus is managed by the workflow, not Terraform. Delete it via the gcloud CLI, then trigger a new pipeline run — `setup_corpus` will recreate it automatically:

```bash
# Find the corpus resource name
gcloud ai rag-corpora list --region=REGION --project=PROJECT_ID

# Delete the corpus
gcloud ai rag-corpora delete CORPUS_RESOURCE_NAME --region=REGION --project=PROJECT_ID

# Trigger a fresh run — setup_corpus recreates the corpus
task docs:run
```

---

## How to Tune Chunking

Chunks are defined by `cloudbuild/scripts/process_docs.py` before upload. Vertex AI RAG Engine imports them with `no_chunking` — no secondary splitting occurs.

- **Section boundary**: change `MIN_SECTION_SIZE` (default 200 chars) to merge more or fewer small sections.
- **Large-section split**: change the `max_chars` parameter in `_split_large_section` (default 4000 chars) to control how oversized sections are further split at code-fence boundaries.

After changing either constant, force a full re-import (see above) to apply to the corpus.

---

## How to Update the Embedding Model

1. Edit `terraform/terraform.tfvars`:
   ```hcl
   embedding_model = "publishers/google/models/text-embedding-large-exp-03-07"
   ```
2. Run `terraform apply`.
3. **Note:** Changing the embedding model requires recreating the corpus, because existing embeddings use the old model's vector space. Delete the corpus via gcloud (see Option B above), then trigger a new pipeline run to recreate it and force a full re-import.

---

## Cost Management

### Estimating costs

| Component | Pricing basis | Estimate |
|---|---|---|
| Vertex AI RAG Engine (Spanner) | Per node-hour, continuously | ~$0.90/hour while corpus exists |
| GCS storage | Per GB-month | ~$0.02/GB/month |
| Cloud Build | Per build-minute (E2_HIGHCPU_8) | ~$0.064/min; expect 30–60 min/week |
| Cloud Workflows | Per step execution | Negligible for weekly runs |

### Reducing costs

- **Delete the corpus when not in use.** Spanner billing stops immediately when the corpus is deleted.
- **Reduce clone frequency.** Change `refresh_schedule` from weekly to monthly if docs don't change often.
- **Use a smaller machine type.** Switch `E2_HIGHCPU_8` to `E2_STANDARD_4` in `cloudbuild.yaml` if the build fits within the timeout.
- **Filter repos.** Remove infrequently-updated repos from `clone_repos.sh`.

### Monitoring costs

Set up a budget alert in the GCP Billing console for the project. Filter by service to see Vertex AI, Cloud Build, and GCS costs separately.

---

## Graph pipeline (Spanner Graph)

The graph pipeline is opt-in (`create_graph_store = true` in `terraform.tfvars`) and provisioned by `terraform/modules/terraform-graph-store/`.

### Enabling

1. Edit `terraform/terraform.tfvars`:
   ```hcl
   create_graph_store = true
   graph_repo_uris = [
     "https://github.com/my-org/my-tf-workspace",
     "https://github.com/my-org/another-workspace",
   ]
   ```
2. `task plan && task apply` — provisions the Spanner instance/database, GCS staging bucket, service account, Cloud Workflows workflow, and Cloud Scheduler job.
3. `task graph:populate` — triggers a one-off run rather than waiting for the weekly cron.
4. `task graph:test` — verifies that `Resource` and `DependsOn` are non-empty.

### Daily operations

| Action | Command |
|---|---|
| Trigger an ad-hoc refresh | `task graph:populate` |
| Smoke-test the store | `task graph:test` |
| Inspect last 5 runs | `task graph:status` |
| Inspect counts from MCP | `mcp__hashicorp_rag__get_graph_info` |

### Investigating a failed graph run

1. `task graph:status` to find the failing execution ID.
2. Open the workflow execution in the Cloud Console (URL is printed by `scripts/run_graph_pipeline.sh`).
3. The workflow's `parallel` block records each per-repo build's status. Click into a failed iteration to find the Cloud Build ID.
4. Open Cloud Build → History → that build, and read the four step logs:
   - `install-terraform` failures: usually transient `releases.hashicorp.com` 5xx — re-trigger.
   - `clone-workspace` failures: the workspace repo is private or the URL is wrong. Add a deploy key or fix the URL.
   - `terraform-graph` failures: missing provider plugin or backend block that doesn't strip cleanly. Check the strip-backend regex in `workflows/graph_pipeline.yaml` against the offending `.tf` file.
   - `ingest-graph` failures: usually IAM. The service account needs `roles/spanner.databaseUser` on the database — `terraform apply` should have set this; re-apply to reconcile.

### Common errors

| Error | Cause | Fix |
|---|---|---|
| `graph_repo_uris is empty - nothing to ingest` | The variable is empty | Set `graph_repo_uris` in tfvars and re-apply, or pass it via the workflow execution argument |
| `PermissionDenied: spanner.databases.beginOrRollbackReadWriteTransaction` | Service account missing `roles/spanner.databaseUser` | Re-run `terraform apply` |
| `Table not found: Resource` | Spanner DDL was applied incrementally | All DDL must apply in the same batch — never split CREATE TABLE and CREATE PROPERTY GRAPH across separate `update_ddl` calls |
| `terraform init` fails on backend block | The strip-backend regex did not match a non-trivial backend declaration | Edit the regex in `workflows/graph_pipeline.yaml` step `terraform-graph` |

### Re-ingesting a single repo

The pipeline is authoritative per `repo_uri`: each ingestion run does `DELETE FROM Resource WHERE repo_uri = @repo_uri` (CASCADE removes the matching `DependsOn` rows) before inserting fresh nodes/edges. To force a clean re-ingest of one repo, just trigger the workflow with that repo in `graph_repo_uris`.

### Cost notes

- Spanner is the only continuously-billed resource. The default `regional-us-central1` config at 100 PU is roughly **$65/month**.
- To pause Spanner billing, set `create_graph_store = false` and apply — the database, instance, and bucket are destroyed (subject to `spanner_database_deletion_protection = false`).
- DOT snapshots are stored in the staging bucket with a 30-day lifecycle delete.
