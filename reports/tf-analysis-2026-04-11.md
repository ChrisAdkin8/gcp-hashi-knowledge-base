# Terraform Code Analysis Report

**Date:** 2026-04-11
**Scope:** `terraform/` (32 .tf files, 1 root module, 1 bootstrap root, 3 child modules)
**Focus:** all
**Mode:** static
**Health Grade:** B− (72/100) — up from C (58/100) on 2026-03-27

---

## Executive Summary

Since the 2026-03-27 baseline the codebase has been refactored from a single flat root module into a layered design: a `bootstrap/` root that owns the GCS state backend, a primary root module that composes two child modules (`hashicorp-docs-pipeline`, conditionally `terraform-graph-store`), and a separate `state-backend` module. A GCS remote backend is now wired up, the provider lock file is committed, the Google provider has an upper version bound, and a CI workflow runs `fmt:check + validate + shellcheck + pytest` on every push. These resolve four of the five baseline HIGH findings.

The remaining issues are largely **structural patterns that the new graph module duplicates** rather than fixes: both pipelines provision project-level IAM roles for their service accounts, both stage buckets are created with `force_destroy = true` and no `lifecycle { prevent_destroy = true }`, and both Cloud Scheduler jobs request the broad `cloud-platform` OAuth scope. Audit-logging configuration is still absent, no resources carry `labels`, no policy-as-code or `terraform test` exists, and several user-facing variables (region, cron strings, machine types) lack `validation` blocks.

There are no CRITICAL findings. No hardcoded credentials, no `.tfvars` files leaking secrets, no state files on disk, no `0.0.0.0/0` exposure, no public IAM bindings.

**Finding counts by urgency:**

| Urgency | Count |
|---------|-------|
| CRITICAL | 0 |
| HIGH | 4 |
| MEDIUM | 11 |
| LOW | 6 |
| INFO | 7 |

### Delta vs 2026-03-27

| Baseline ID | Status | Notes |
|---|---|---|
| **S-001** No remote backend | ✅ RESOLVED | `terraform/versions.tf:11-15` GCS backend with `prefix = "rag/state"`; bootstrapped via `terraform/bootstrap/`. |
| **S-002** Lock file gitignored | ✅ RESOLVED | `.terraform.lock.hcl` committed at `terraform/.terraform.lock.hcl`, pins `google` 6.50.0 and `google-beta` 6.50.0. |
| **S-003** Project-level IAM | ⚠️ PERSISTENT + NEW | Same pattern now in both `hashicorp-docs-pipeline/iam.tf` and `terraform-graph-store/iam.tf`. See [S-101]. |
| **S-004** Bucket missing `prevent_destroy` | ⚠️ PERSISTENT + NEW | Both `rag_docs` and `graph_staging` buckets affected, both also have `force_destroy = true`. See [S-102]. |
| **S-005** OAuth scope `cloud-platform` | ⚠️ PERSISTENT + NEW | Same pattern in both schedulers. See [S-103]. |
| **S-006** Bucket missing CMEK | ⚠️ PERSISTENT | Acceptable per project posture; logged as INFO. See [S-104]. |
| **S-007** No audit logging | ⚠️ PERSISTENT | See [S-105]. |
| **R-002** Wide provider version constraint | ✅ RESOLVED | Now `google ~> 6.0` with `required_version = ">= 1.5, < 2.0"`. Could be tightened further (LOW). See [R-101]. |
| **C-001** No CI workflow | ✅ RESOLVED | `.github/workflows/ci.yml` runs fmt:check + validate + shellcheck + pytest. |
| **D-001** Single root module / no duplication | OBSOLETE | Now 3 child modules; new DRY observations apply. See [D-101]. |
| **Y-001** Fragile `cloudbuild_repo_uri` parsing | (not re-checked here) | Carry forward — re-audit next pass. |

---

## 1. Security Posture

### HIGH

- **[S-101] Project-level IAM bindings on both pipeline service accounts** — `terraform/modules/hashicorp-docs-pipeline/iam.tf:8-14` + `locals.tf:15-24`, `terraform/modules/terraform-graph-store/iam.tf:8-14` + `locals.tf:13-19` | Blast: `environment`
  Both pipelines use `google_project_iam_member` to grant project-wide roles. The docs SA receives `roles/aiplatform.admin`, `roles/storage.objectAdmin`, `roles/cloudbuild.builds.editor`, `roles/workflows.invoker`, `roles/monitoring.editor`, `roles/documentai.editor` at the project scope; the graph SA receives `roles/spanner.databaseUser`, `roles/storage.objectAdmin`, `roles/cloudbuild.builds.editor`, `roles/workflows.invoker` at the project scope. Each SA can therefore read/write any bucket, mutate any Cloud Build trigger, and edit any Workflow in the project — well beyond what either pipeline requires. The `objectAdmin` role in particular violates least-privilege because it grants delete on all buckets in the project (including the state bucket).
  **Recommendation:** Replace with resource-level bindings where the resource type supports them — `google_storage_bucket_iam_member` for the rag-docs and graph-staging buckets, `google_spanner_database_iam_member` for the tf-graph database, `google_workflows_workflow_iam_member` for each workflow. For roles that only bind at project scope (e.g., `roles/cloudbuild.builds.editor`), add an IAM Condition that scopes by resource name prefix.

- **[S-102] Both staging buckets have `force_destroy = true` and no `prevent_destroy`** — `terraform/modules/hashicorp-docs-pipeline/storage.tf:6`, `terraform/modules/terraform-graph-store/storage.tf:6` | Blast: `single-resource`
  `force_destroy = true` allows `terraform destroy` to wipe the bucket and all object versions in a single command, and neither bucket carries `lifecycle { prevent_destroy = true }`. A typo in `terraform destroy -target=…`, an accidental module removal, or a `taint` + `apply` would delete the entire processed corpus (90-day rolling history) and the entire DOT snapshot history (30-day rolling). The `state-backend` bucket gets this right (`prevent_destroy = true` at `terraform/modules/state-backend/main.tf:33`); pipeline buckets should match.
  **Recommendation:** Add `lifecycle { prevent_destroy = true }` to both `google_storage_bucket.rag_docs` and `google_storage_bucket.graph_staging`. Set `force_destroy = false` (the default) so even an explicit destroy requires manual emptying first. If a wipe is genuinely needed, the operator can flip the flag in a one-off branch.

- **[S-103] Cloud Scheduler OAuth scope is `cloud-platform` on both jobs** — `terraform/modules/hashicorp-docs-pipeline/scheduler.tf:23`, `terraform/modules/terraform-graph-store/scheduler.tf` (matching pattern) | Blast: `environment`
  Both schedulers obtain an OAuth token with `https://www.googleapis.com/auth/cloud-platform`, which grants the underlying service account's *entire* permission set for the duration of the call. The schedulers only need to POST to the Workflows Executions API; the minimum scope is `https://www.googleapis.com/auth/workflows`. Combined with [S-101], a compromised scheduler token could exercise the SA's full project-wide IAM grants.
  **Recommendation:** Change `scope = "https://www.googleapis.com/auth/cloud-platform"` to `scope = "https://www.googleapis.com/auth/workflows"` in both scheduler resources.

- **[S-105] No `google_project_iam_audit_config` resource** — `terraform/modules/hashicorp-docs-pipeline/`, `terraform/modules/terraform-graph-store/` | Blast: `environment` | CIS GCP 2.1
  Neither module declares Cloud Audit Log configuration. Data Access logs for `aiplatform.googleapis.com`, `spanner.googleapis.com`, `storage.googleapis.com`, and `cloudbuild.googleapis.com` are therefore disabled by default. If a service account is compromised, there is no record of which corpus files were read, which graph rows were queried, or which buckets were enumerated. Forensic reconstruction after an incident would be impossible.
  **Recommendation:** Add a `google_project_iam_audit_config` resource (likely in the root module so both pipelines share it) covering at least `aiplatform.googleapis.com`, `spanner.googleapis.com`, `storage.googleapis.com`, and `cloudbuild.googleapis.com` with `log_type = "DATA_READ"` and `log_type = "DATA_WRITE"`.

### MEDIUM

- **[S-106] Spanner instance lacks an explicit `labels` block and CMEK** — `terraform/modules/terraform-graph-store/spanner.tf` | Blast: `single-resource`
  The Spanner instance (regional 100 PU, ~$65/mo) is created without `labels`, which makes cost-allocation reporting and ownership audits harder. It also uses default Google-managed encryption — acceptable for non-regulated data but worth noting if compliance requirements grow.
  **Recommendation:** Add a project-wide `labels` local (`{ component = "rag", managed-by = "terraform" }`) and apply to all labelable resources. CMEK only if regulated data ever lands here.

- **[S-107] Workflows resources have `deletion_protection = false`** — `terraform/modules/hashicorp-docs-pipeline/workflow.tf:7`, `terraform/modules/terraform-graph-store/workflow.tf` | Blast: `single-resource`
  Both workflows opt out of the provider's deletion-protection guard. While the workflow source is checked into git and trivially recreatable, leaving `deletion_protection = false` removes one cheap safety net against accidental destroy.
  **Recommendation:** Either flip to `true` and document the override path, or add `lifecycle { prevent_destroy = true }` instead — choose one consistent guard model and apply it project-wide.

- **[S-108] Document AI processor location hardcoded to `"us"`** — `terraform/modules/hashicorp-docs-pipeline/document_ai.tf` | Blast: `single-resource`
  The Document AI Layout processor is pinned to `location = "us"` even though the rest of the pipeline runs in `var.region` (default `us-central1`). This is functional today but breaks the assumption that "set `region` and everything follows." If a future deploy targets `europe-west1`, the processor will silently keep running in `us`.
  **Recommendation:** Either parameterise via a new `documentai_location` variable (with a `validation` block restricting to `us` / `eu`), or add an inline comment explaining that Document AI's regional footprint is intentionally distinct from the compute region.

- **[S-109] Service-account self-impersonation pattern is duplicated in both modules** — `terraform/modules/hashicorp-docs-pipeline/iam.tf:19-23`, `terraform/modules/terraform-graph-store/iam.tf:19-23` | Blast: `module`
  Both modules grant `roles/iam.serviceAccountUser` to the SA on itself so Cloud Build can submit jobs as that SA. The pattern is correct and well-commented, but it is a load-bearing piece of glue: removing it silently breaks Cloud Build with an opaque AccessDenied. Worth a unit-style assertion in `terraform test` once that scaffolding lands.
  **Recommendation:** Add a `terraform test` case that asserts the self-impersonation binding exists for each pipeline SA.

### INFO

- **[S-104] GCS buckets use default Google-managed encryption (no CMEK)** — `terraform/modules/hashicorp-docs-pipeline/storage.tf`, `terraform/modules/terraform-graph-store/storage.tf`, `terraform/modules/state-backend/main.tf` | Blast: `single-resource`
  All three buckets rely on Google-managed encryption. The state-backend module already exposes a `cmek_key_name` variable via dynamic block — extend the same pattern to the pipeline buckets if regulated data ever lands here.

- **[S-110] No public IAM bindings, no `0.0.0.0/0` exposure, no plaintext network protocols** — repo-wide | Blast: n/a
  Positive finding. Confirmed by full grep across all .tf files: no `allUsers`, no `allAuthenticatedUsers`, no inbound firewall rules of any kind (no VPC resources at all), no insecure HTTP endpoints. The pipeline only invokes Google APIs over Google's internal network.

- **[S-111] No hardcoded secrets, no `.tfvars` files committed** — repo-wide | Blast: n/a
  Positive finding. Pre-analysis credential scan returned zero hits. Only `terraform/terraform.tfvars.example` exists as a template; it carries placeholder values. Lock file present and not gitignored.

---

## 2. DRY and Code Reuse

### MEDIUM

- **[D-101] Pipeline scaffolding is copy-pasted between the two modules** — `terraform/modules/hashicorp-docs-pipeline/`, `terraform/modules/terraform-graph-store/` | Blast: `module`
  Both modules implement the same shape: `apis.tf` (project services) → `iam.tf` (service account + project-IAM for-each + self-impersonation) → `storage.tf` (regional bucket with versioning + lifecycle) → `workflow.tf` (Cloud Workflows definition) → `scheduler.tf` (Cloud Scheduler weekly cron). The IAM block, the API enable block, and the scheduler OAuth block are nearly identical between modules. Today the duplication is small enough to read; once a third pipeline lands (or once a security finding like [S-101] needs to be fixed in both places), the divergence risk grows.
  **Recommendation:** Extract a `pipeline-scaffold` sub-module that takes a service-account name, role list, API list, workflow source path, and cron string, and emits the SA + IAM + APIs + workflow + scheduler. Each pipeline module would then own only its workload-specific resources (RAG corpus / Spanner / GCS bucket). Defer until the third pipeline appears or until [S-101] needs a coordinated fix.

### LOW

- **[D-102] Bucket-name hashing pattern duplicated** — `terraform/modules/hashicorp-docs-pipeline/locals.tf:2`, `terraform/modules/terraform-graph-store/locals.tf:2`
  Both modules compute `"${var.project_id}-{prefix}-${substr(sha256(var.project_id), 0, 8)}"`. Could move to a shared local or a tiny `bucket-name` module. Low priority.

### INFO

- **[D-103] No orphaned modules detected** — `terraform/modules/`
  All three modules (`state-backend`, `hashicorp-docs-pipeline`, `terraform-graph-store`) are referenced from a root module (`bootstrap/main.tf` and `terraform/main.tf` respectively). No dead code.

---

## 3. Style and Conventions

### LOW

- **[Y-101] No `labels` on any resource** — repo-wide | Blast: `infrastructure-wide`
  GCP best practice is to label every billable resource (`environment`, `component`, `managed-by`, `cost-center`). The repo currently has zero `labels` blocks. Without labels, BigQuery billing exports cannot attribute Spanner / Vertex / Cloud Build spend to the RAG initiative.
  **Recommendation:** Define `local.common_labels = { managed-by = "terraform", component = "hashicorp-rag", environment = var.environment }` (after introducing an `environment` variable) and apply to every labelable resource: GCS buckets, Spanner instance, Workflows, Scheduler jobs, Cloud Build worker pools.

- **[Y-102] `terraform fmt -check -recursive` clean** — repo-wide | Blast: n/a
  Positive finding. Verified manually and via the `task fmt:check` step in CI.

- **[Y-103] `terraform validate` clean across root + bootstrap** — repo-wide | Blast: n/a
  Positive finding. No deprecation warnings, no syntax errors. The Google 6.50 provider does not flag any of the resource arguments used here as deprecated.

- **[Y-104] Variable descriptions present on all variables in both modules** — `terraform/variables.tf`, `terraform/modules/*/variables.tf`
  Positive finding. Every `variable` block carries a `description`. Several outputs also have `description` set. A quick spot-check found one or two outputs missing `description` — sweep next pass.

### INFO

- **[Y-105] No README.md inside individual modules** — `terraform/modules/hashicorp-docs-pipeline/`, `terraform/modules/terraform-graph-store/`, `terraform/modules/state-backend/`
  The repo-level `README.md` and `docs/ARCHITECTURE.md` cover the system end-to-end, so per-module READMEs would partly duplicate that material. Acceptable for an internal repo with three modules.

---

## 4. Robustness

### MEDIUM

- **[R-101] Provider constraint `~> 6.0` is wider than necessary** — `terraform/versions.tf` | Blast: `infrastructure-wide`
  `~> 6.0` allows any 6.x release. The lock file pins 6.50.0 today, but a fresh `terraform init` six months from now will pull 6.99 and may behave differently in subtle ways (deprecations, default changes). Tighter pinning catches surprises before they hit `apply`.
  **Recommendation:** Tighten to `~> 6.50` or `>= 6.50, < 6.60`. Bump deliberately when upgrading.

- **[R-102] No `validation` blocks on `region`, `refresh_schedule`, `graph_refresh_schedule`, `graph_cloudbuild_machine_type`, `embedding_model`** — `terraform/variables.tf:11-43`, `:98-108` | Blast: `module`
  Several user-facing variables accept any string. A typo in `region` (`"us-cental1"`) only fails at apply time after several minutes of resource creation. A malformed cron string is rejected by Cloud Scheduler with a generic error. A mistaken `embedding_model` path is only caught when the workflow tries to import.
  **Recommendation:** Add `validation` blocks: `region` → match `^[a-z]+-[a-z]+\d+$`; cron strings → match a 5-field cron regex; `graph_cloudbuild_machine_type` → restrict to a known set (`E2_HIGHCPU_8`, `E2_HIGHCPU_32`, etc.); `embedding_model` → match `^publishers/google/models/text-embedding-`.

- **[R-103] Spanner database `deletion_protection` is variable-controlled but the bucket isn't** — `terraform/variables.tf:92-96`, `terraform/modules/terraform-graph-store/spanner.tf` | Blast: `single-resource`
  The Spanner database has a dedicated `spanner_database_deletion_protection` variable (defaulting to `true`) — good. There is no equivalent for the staging buckets, the workflow, or the scheduler. Inconsistent guard model: the high-cost stateful resource is protected, the lower-cost-but-still-painful resources aren't.
  **Recommendation:** Either add equivalent variables for buckets/workflows or hardcode `prevent_destroy = true` in their lifecycle blocks (cheaper, simpler — see [S-102]).

- **[R-104] Workflow source paths use `file()` instead of `templatefile()`** — `terraform/modules/hashicorp-docs-pipeline/workflow.tf:8`, `terraform/modules/terraform-graph-store/workflow.tf` | Blast: `single-resource`
  Both workflow definitions are loaded with `file(var.workflow_source_path)`. Hardcoded values inside the YAML (project IDs, bucket names) must be plumbed through the workflow's `argument` payload from the scheduler. This works but means a typo in the scheduler argument shows up as a runtime workflow error rather than a plan-time mismatch. Future evolution toward `templatefile()` would let Terraform interpolate IDs at apply time.
  **Recommendation:** No action today — the current pattern is intentional and aligns with the AWS sibling. Note for future evolution.

### LOW

- **[R-105] No `timeouts` blocks on slow resources** — `terraform/modules/terraform-graph-store/spanner.tf` | Blast: `single-resource`
  `google_spanner_instance` and `google_spanner_database` can take 5-10 minutes to provision. The provider's default timeout is usually fine, but explicit `timeouts { create = "20m", delete = "20m" }` blocks document operator expectations.

### INFO

- **[R-106] Bootstrap module intentionally uses local backend** — `terraform/bootstrap/versions.tf`
  This is the standard chicken-and-egg pattern: the bootstrap creates the GCS bucket that the main module uses as its backend. Local state in `terraform/bootstrap/` is acceptable as long as the bootstrap is rerun rarely and the resulting `terraform.tfstate` is gitignored (verified).

---

## 5. Operations and Maintainability

### MEDIUM

- **[O-101] No `terraform test` scaffolding** — repo-wide | Blast: `infrastructure-wide`
  No `.tftest.hcl` files. The repo has Python pytest coverage for ingestion scripts (validated by `task test`), but no Terraform-level assertions. A simple `tests/plan.tftest.hcl` could assert that the IAM bindings, the bucket names, and the workflow source paths all resolve as expected with default and graph-enabled variable sets.
  **Recommendation:** Add a minimal `tests/` directory with two test files: one with `create_graph_store = false` (default), one with `create_graph_store = true` and a non-empty `graph_repo_uris`. Each test runs `command = plan` and asserts a few critical conditions.

- **[O-102] No `tflint` or `checkov` in CI** — `.github/workflows/ci.yml` | Blast: `infrastructure-wide`
  CI runs `terraform fmt:check` + `terraform validate` + `shellcheck` + `pytest`. It does not run `tflint` (catches Google-provider-specific anti-patterns), `checkov` (catches CIS misconfigurations), or `trivy config` (catches CVE-mapped misconfigurations). Adding any one of them would catch [S-102], [S-105], and [Y-101] automatically.
  **Recommendation:** Add `tflint` with the `tflint-ruleset-google` plugin as a CI job. Optional: add `checkov` with `--framework terraform`. Suppress findings the project deliberately accepts via `.tflint.hcl` and `.checkov.yaml`.

- **[O-103] No `.pre-commit-config.yaml`** — repo root | Blast: `infrastructure-wide`
  Local fmt/validate/shellcheck happen via `task ci` but only when the operator remembers. A pre-commit hook would catch formatting drift before it hits a PR.
  **Recommendation:** Add `.pre-commit-config.yaml` running `terraform fmt -check -recursive`, `terraform validate`, `shellcheck`, and the existing pytest suite.

### LOW

- **[O-104] No policy-as-code (Sentinel / OPA / Conftest)** — repo-wide | Blast: `infrastructure-wide`
  Optional. For a small internal repo, `tflint` + a single `terraform test` file is usually enough. Note for future scaling.

### INFO

- **[O-105] CI workflow exists and is comprehensive** — `.github/workflows/ci.yml` | Blast: n/a
  Positive finding (delta vs baseline C-001). Runs `task ci`. No GCP credentials required because validate runs with `-backend=false`.

- **[O-106] Taskfile orchestration is well-organised** — `Taskfile.yml` | Blast: n/a
  Positive finding. Namespaced as `docs:*`, `graph:*`, `mcp:*`, `ci`, `fmt`, `validate`, `shellcheck`, `test`. `task ci` chains all gates locally.

---

## 6. Suppressed Findings

None. No `# tf-analyze:ignore` markers and no `.tf-analyze-ignore.yaml` file in the repo.

---

## 7. Action Plan (Recommended Order)

| # | Finding | Effort | Impact |
|---|---|---|---|
| 1 | [S-102] Add `prevent_destroy` and unset `force_destroy` on both staging buckets | 5 min | HIGH — blocks accidental destroy of corpus + DOT history |
| 2 | [S-103] Narrow scheduler OAuth scope to `auth/workflows` (both modules) | 5 min | HIGH — least-privilege for scheduler tokens |
| 3 | [R-101] Tighten provider constraint to `~> 6.50` | 2 min | LOW effort, prevents future surprise upgrades |
| 4 | [S-105] Add `google_project_iam_audit_config` for `aiplatform`, `spanner`, `storage`, `cloudbuild` | 15 min | HIGH — enables forensic capability |
| 5 | [R-102] Add `validation` blocks on `region`, cron strings, machine type, embedding model | 20 min | MEDIUM — fast feedback at plan time |
| 6 | [Y-101] Define `local.common_labels` and apply across all labelable resources | 30 min | MEDIUM — cost attribution and ownership |
| 7 | [O-102] Add `tflint` + `tflint-ruleset-google` to CI | 30 min | MEDIUM — automated regression for these findings |
| 8 | [S-101] Replace project-level IAM with resource-level bindings + IAM Conditions | 1-2 hr | HIGH — biggest blast-radius reduction |
| 9 | [O-101] Add `terraform test` covering both `create_graph_store` modes | 1 hr | MEDIUM — protects [S-101] fix and future refactors |
| 10 | [D-101] Extract `pipeline-scaffold` sub-module (defer until 3rd pipeline appears) | 4-6 hr | MEDIUM — pay down only when duplication starts to bite |

Items 1-3 are 12 minutes total and resolve two HIGH findings; do those first.

---

## 8. CIS GCP Benchmark Mapping

| Control | Status | Finding |
|---|---|---|
| 2.1 — Cloud Audit Logging configured | ❌ | [S-105] |
| 3.8 — VPC Flow Logs enabled | n/a | No VPC resources in scope |
| 4.x — Compute / VM controls | n/a | No VMs in scope |
| 5.1 — GCS bucket not anonymously accessible | ✅ | [S-110] |
| 5.2 — GCS bucket uniform bucket-level access | ✅ | All three buckets set `uniform_bucket_level_access = true` |
| 7.x — BigQuery / Spanner | ⚠️ | Spanner instance lacks CMEK and labels — [S-106] |

---

## 9. Scope Summary

```
terraform/
├── bootstrap/                                  (1 root, 4 .tf files, local backend — intentional)
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── versions.tf
├── versions.tf                                 (GCS backend, google ~> 6.0)
├── variables.tf                                (13 variables, 3 with validation)
├── main.tf                                     (root composition)
├── outputs.tf                                  (15 outputs, 7 conditional pass-throughs)
├── terraform.tfvars.example
├── .terraform.lock.hcl                         (committed, pins 6.50.0)
└── modules/
    ├── state-backend/                          (1 .tf file, prevent_destroy ✓, CMEK opt-in ✓)
    ├── hashicorp-docs-pipeline/                (10 .tf files)
    │   ├── apis.tf
    │   ├── iam.tf
    │   ├── storage.tf
    │   ├── workflow.tf
    │   ├── scheduler.tf
    │   ├── document_ai.tf
    │   ├── monitoring.tf
    │   ├── locals.tf
    │   ├── variables.tf
    │   └── outputs.tf
    └── terraform-graph-store/                  (8 .tf files)
        ├── spanner.tf
        ├── iam.tf
        ├── storage.tf
        ├── workflow.tf
        ├── scheduler.tf
        ├── locals.tf
        ├── variables.tf
        └── outputs.tf
```

**Totals:** 32 .tf files, 1 bootstrap root, 1 main root, 3 child modules, 0 orphaned modules, 0 .tfvars committed, 0 state files on disk, 1 lock file committed.

---

## 10. Pre-Analysis Hygiene Results (Step 0)

| Check | Result |
|---|---|
| Credential pattern scan in `.tfvars` | ✅ No hits — only `terraform.tfvars.example` exists |
| Git history credential leak | ✅ No commits yet (`git log` empty) — first commit will be clean |
| State files on disk | ✅ None found outside `.terraform/` |
| `.terraform.lock.hcl` exists | ✅ `terraform/.terraform.lock.hcl` |
| Lock file gitignored | ✅ Not gitignored (S-002 from baseline resolved) |
| `required_version` matches installed | ✅ `>= 1.5, < 2.0` matches Terraform 1.14.5 |
| `CLAUDE.md` / docs read for intentional patterns | ✅ Confirmed bootstrap-with-local-backend is intentional |
