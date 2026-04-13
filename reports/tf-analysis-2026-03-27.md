# Terraform Code Analysis Report

**Date:** 2026-03-27
**Scope:** `terraform/` (4 .tf files, 1 root module, 0 child modules)
**Focus:** all
**Mode:** static
**Health Grade:** C (58/100)

---

## Executive Summary

The Terraform codebase is well-structured for a single-root-module project with clean formatting, good use of `for_each`, input validation, and comprehensive variable descriptions. However, it has several significant gaps: no remote backend configured (local state only), no CI/CD pipeline, no lock file committed, a wide provider version constraint, and IAM roles granted at project level that could be scoped more tightly. The GCS bucket also lacks `prevent_destroy` protection.

**Finding counts by urgency:**

| Urgency | Count |
|---------|-------|
| CRITICAL | 0 |
| HIGH | 5 |
| MEDIUM | 8 |
| LOW | 4 |
| INFO | 5 |

### Delta

No previous report found. This is the baseline analysis.

---

## 1. Security Posture

### HIGH

- **[S-001] No remote backend configured — local state stores secrets in plaintext** — `terraform/versions.tf:10` | Blast: `infrastructure-wide`
  The backend is commented out. Terraform state contains all resource attributes including service account keys and OAuth tokens. Local state is not locked, not shared, and not recoverable. Any `terraform apply` from a different machine will create duplicate resources.
  **Recommendation:** Uncomment the GCS backend block, create the state bucket, and run `terraform init -migrate-state`.

- **[S-002] Lock file `.terraform.lock.hcl` is gitignored** — `.gitignore:4` | Blast: `infrastructure-wide`
  The lock file pins provider checksums and must be committed to ensure all operators use identical provider binaries. Without it, a supply-chain attack on the provider registry could go undetected.
  **Recommendation:** Remove `.terraform.lock.hcl` from `.gitignore`. Run `terraform init` and commit the generated lock file.

- **[S-003] Project-level IAM roles are broader than necessary** — `terraform/main.tf:14-21` | Blast: `environment`
  `roles/storage.objectAdmin` grants read/write/delete on ALL buckets in the project. `roles/monitoring.editor` and `roles/cloudbuild.builds.editor` similarly grant project-wide access. The service account only needs access to the RAG bucket and its own build trigger.
  **Recommendation:** Replace project-level IAM bindings with resource-level bindings where possible (e.g., `google_storage_bucket_iam_member` for the bucket). For roles that only support project-level binding, consider using IAM Conditions to scope by resource name.

- **[S-004] GCS bucket missing `prevent_destroy` lifecycle** — `terraform/main.tf:60` | Blast: `single-resource`
  The RAG docs bucket contains the processed corpus data. An accidental `terraform destroy` or resource replacement would delete all staged documentation. While `force_destroy = false` prevents deletion of non-empty buckets, `prevent_destroy` adds a Terraform-level guard.
  **Recommendation:** Add `lifecycle { prevent_destroy = true }` to `google_storage_bucket.rag_docs`.

- **[S-005] OAuth scope is `cloud-platform` (full project access)** — `terraform/main.tf:140` | Blast: `environment` | CIS: n/a
  The Cloud Scheduler OAuth token uses `https://www.googleapis.com/auth/cloud-platform` which grants the service account's full permissions. The minimum scope needed is `https://www.googleapis.com/auth/workflows`.
  **Recommendation:** Narrow the OAuth scope to `https://www.googleapis.com/auth/workflows`.

### MEDIUM

- **[S-006] GCS bucket missing CMEK encryption** — `terraform/main.tf:60` | Blast: `single-resource`
  The bucket uses default Google-managed encryption. For compliance-sensitive workloads, customer-managed encryption keys (CMEK) provide additional control over key rotation and access auditing.
  **Recommendation:** If compliance requires it, add an `encryption` block with a Cloud KMS key. Otherwise, acknowledge as acceptable.

- **[S-007] No audit logging configuration** — `terraform/main.tf` | Blast: `environment` | CIS: 2.1
  No `google_project_iam_audit_config` resource exists. Cloud Audit Logs for data access may not be enabled, reducing forensic capability.
  **Recommendation:** Add a `google_project_iam_audit_config` resource for at least `aiplatform.googleapis.com` and `storage.googleapis.com`.

---

## 2. DRY and Code Reuse

### INFO

- **[D-001] Single root module — no duplication** — `terraform/` | Blast: n/a
  The codebase has a single root module with no child modules. There is no cross-module duplication. No DRY issues detected.

---

## 3. Style and Conventions

### MEDIUM

- **[Y-001] `cloudbuild_repo_uri` parsing is fragile** — `terraform/main.tf:92-93` | Blast: `single-resource`
  The `split("/", replace(...))` pattern to extract GitHub owner and repo name will break for URIs with trailing `.git`, different prefixes, or subgroups. This logic is duplicated on two consecutive lines.
  **Recommendation:** Extract to a `locals` block with clear names:
  ```hcl
  locals {
    github_path = replace(var.cloudbuild_repo_uri, "https://github.com/", "")
    github_owner = split("/", local.github_path)[0]
    github_repo  = split("/", local.github_path)[1]
  }
  ```

### LOW

- **[Y-002] Inconsistent label/tag usage** — `terraform/main.tf` | Blast: `module`
  No `labels` are applied to any resource. GCP resources support labels for cost attribution, environment identification, and operational filtering.
  **Recommendation:** Add a `common_labels` local and apply it to all resources that support labels (bucket, service account, workflow, scheduler job, build trigger).

- **[Y-003] Alert policy filter strings use interpolation in heredocs** — `terraform/main.tf:171-175,201-205` | Blast: `single-resource`
  The `<<-EOT` heredocs contain Terraform interpolation for resource names. While functional, this makes the filter strings harder to debug in the Cloud Console because the exact values aren't visible in the code.
  **Recommendation:** No action required — this is a stylistic observation. The current approach is correct.

---

## 4. Robustness

### MEDIUM

- **[R-001] Provider version constraint `~> 5.0` is very wide** — `terraform/versions.tf:5` | Blast: `infrastructure-wide`
  `~> 5.0` allows any version from 5.0 to 5.999. The Google provider frequently introduces breaking changes in minor versions (resource renames, deprecated argument removal, new required fields). This may cause unexpected failures when a new minor version is released.
  **Recommendation:** Pin to the minor version currently in use: `~> 5.45` (or whatever `terraform providers` shows).

- **[R-002] `required_version = ">= 1.5"` has no upper bound** — `terraform/versions.tf:2` | Blast: `infrastructure-wide`
  Terraform 2.0 may introduce breaking changes. An upper bound prevents accidental use of incompatible versions.
  **Recommendation:** Change to `>= 1.5, < 2.0`.

- **[R-003] `region` variable has no validation** — `terraform/variables.tf:11` | Blast: `environment`
  Any string is accepted. An invalid region like `"moon-west-1"` would only fail at apply time.
  **Recommendation:** Add a validation block checking against a known pattern or set of GCP regions.

- **[R-004] `cloudbuild_repo_uri` has no format validation** — `terraform/variables.tf:51` | Blast: `single-resource`
  Only validates non-empty. A malformed URI (missing `https://`, wrong domain) would cause a confusing runtime error during `split()`.
  **Recommendation:** Add a regex validation: `can(regex("^https://github\\.com/.+/.+$", var.cloudbuild_repo_uri))`.

- **[R-005] Missing `timeouts` on slow resources** — `terraform/main.tf` | Blast: `module`
  `google_project_service` (API enablement) can take several minutes. `google_cloudbuild_trigger` depends on GitHub app installation which may time out. Default timeouts may cause intermittent failures.
  **Recommendation:** Add `timeouts { create = "10m" }` to `google_project_service.apis`.

- **[R-006] `embedding_model` variable not referenced in Terraform** — `terraform/variables.tf:89` | Blast: n/a
  The `embedding_model` variable is defined but never referenced in any `.tf` file. It's used by `scripts/create_corpus.py` but has no Terraform consumer. Documented as intentional in CLAUDE.md — the variable exists so the `terraform.tfvars` file serves as a single source of truth for all configuration.
  **Recommendation:** Add a comment noting the variable is consumed by scripts, not Terraform resources.

### LOW

- **[R-007] `corpus_display_name` variable not referenced in Terraform** — `terraform/variables.tf:27` | Blast: n/a
  Same pattern as `embedding_model` — defined for documentation/script consistency but not used in `.tf` files.

---

## 5. Simplicity

### INFO

- **[X-001] Appropriate complexity for scope** — `terraform/` | Blast: n/a
  The codebase is straightforward with no over-engineering. `for_each` is used correctly for APIs and IAM roles. Conditional monitoring uses `count` appropriately. No unnecessary abstractions.

---

## 6. Operational Readiness

### MEDIUM

- **[O-001] No resource labels for cost attribution or filtering** — `terraform/main.tf` | Blast: `module`
  None of the 9 managed resources have `labels`. This makes it difficult to filter costs, set up monitoring dashboards, or identify resources by environment in multi-project setups.
  **Recommendation:** Add a `common_labels` local:
  ```hcl
  locals {
    common_labels = {
      managed_by = "terraform"
      project    = "rag-pipeline"
    }
  }
  ```

### INFO

- **[O-002] Monitoring is conditional and well-structured** — `terraform/main.tf:147-219` | Blast: n/a
  Alert policies for workflow and build failures are correctly gated on `notification_email`. The `condition_matched_log` approach is appropriate for event-driven alerting.

- **[O-003] GCS bucket has versioning and lifecycle rules** — `terraform/main.tf:67-78` | Blast: n/a
  Versioning enables state recovery. The 90-day lifecycle rule prevents unbounded growth. Good operational hygiene.

---

## 7. CI/CD and Testing Maturity

### HIGH

- **[C-001] No CI/CD pipeline detected** — Repo root | Blast: `infrastructure-wide`
  No `.github/workflows/`, `.gitlab-ci.yml`, `Jenkinsfile`, or equivalent found. Terraform changes are applied manually without automated plan/validate/apply gates. There is no automated check that `terraform fmt`, `terraform validate`, or security scans pass before merge.
  **Recommendation:** Add a GitHub Actions workflow that runs `terraform fmt -check`, `terraform validate`, and optionally `tfsec` or `trivy` on pull requests.

### MEDIUM

- **[C-002] No pre-commit framework** — Repo root | Blast: `module`
  No `.pre-commit-config.yaml` found. Developers may push unformatted or invalid Terraform code.
  **Recommendation:** Add pre-commit hooks for `terraform fmt`, `terraform validate`, and `detect-secrets`.

- **[C-003] No TFLint configuration** — Repo root | Blast: `module`
  TFLint catches provider-specific errors (invalid machine types, deprecated arguments) that `terraform validate` misses.
  **Recommendation:** Add `.tflint.hcl` with the Google Cloud ruleset.

- **[C-004] No Terraform tests** — Repo root | Blast: `module`
  No `.tftest.hcl`, `*_test.go`, or `tests/` directory found. Module contracts are not verified.
  **Recommendation:** Add at least one `terraform test` validating the module outputs with mock providers.

- **[C-005] No policy-as-code enforcement** — Repo root | Blast: `infrastructure-wide`
  No Sentinel or OPA/Conftest policies found. Organizational guardrails (mandatory labels, no public access, encryption requirements) are not enforced.
  **Recommendation:** Consider OPA/Conftest for lightweight policy checks in CI.

---

## 8. Cross-Module Contracts

### INFO

- **[M-001] Single root module — no cross-module contracts to verify** — `terraform/` | Blast: n/a
  All resources are in one flat root module. No orphaned modules, no pass-through chains, no contract mismatches.

---

## 9. Stack-Specific Findings

No Vault, Consul, GKE, Helm, or Kubernetes resources detected. No stack-specific checks applicable.

---

## 10. CLAUDE.md Compliance

### Verified

- `for_each` over `toset()` for APIs — **PASS** (main.tf:33-34)
- `for_each` for service account roles — **PASS** (main.tf:50-56)
- Variables match the documented table — **PASS** (12/12 variables present with correct types and defaults)
- Validation blocks on `project_id`, `chunk_size`, `chunk_overlap` — **PASS**
- `disable_on_destroy = false` on APIs — **PASS** (main.tf:38)
- `force_destroy = false` on bucket — **PASS** (main.tf:65)
- Conditional monitoring on `notification_email` — **PASS** (main.tf:150,162,192)
- `corpus_resource_name` output uses the documented formula — **PASS** (outputs.tf:33)

### Deviations

- **[V-001] `embedding_model` and `corpus_display_name` are defined but unused in Terraform** — LOW
  CLAUDE.md specifies them in the variables table. They exist for configuration documentation purposes but have no Terraform consumer. The CLAUDE.md does not specify they must be referenced in resources, so this is informational.

---

## 11. Suppressed Findings

No suppression file (`.tf-analyze-ignore.yaml`) found. No inline suppressions detected. No findings suppressed.

---

## 12. Positive Findings

- **Clean formatting** — `terraform fmt -check` passes with no issues.
- **All variables have descriptions** — 12/12 variables include descriptive `description` fields.
- **All outputs have descriptions** — 7/7 outputs include descriptive `description` fields.
- **Input validation** — Critical variables (`project_id`, `rag_bucket_name`, `cloudbuild_repo_uri`, `chunk_size`, `chunk_overlap`) have validation blocks.
- **`for_each` over `toset()`** — APIs and IAM roles use idiomatic `for_each` instead of `count`.
- **Conditional resources** — Monitoring resources are cleanly gated with `count` on `notification_email`.
- **No hardcoded values** — No project IDs, regions, or credentials in `.tf` files.
- **`disable_on_destroy = false`** — API resources won't be disabled when removed from Terraform, preventing disruption.
- **Versioning enabled on GCS** — Protects against accidental overwrites.
- **Well-documented intent** — CLAUDE.md provides comprehensive architectural context.

---

## 13. Recommended Action Plan

| Priority | Finding | Section | Effort | Blast Radius | Description |
|----------|---------|---------|--------|--------------|-------------|
| 1 | S-001 | Security | Small | infrastructure-wide | Configure remote GCS backend with state locking |
| 2 | S-002 | Security | Small | infrastructure-wide | Commit `.terraform.lock.hcl` (remove from `.gitignore`) |
| 3 | C-001 | CI/CD | Medium | infrastructure-wide | Add GitHub Actions pipeline for fmt/validate/scan |
| 4 | S-003 | Security | Medium | environment | Scope IAM roles to resource-level where possible |
| 5 | R-001 | Robustness | Small | infrastructure-wide | Tighten provider constraint to `~> 5.45` |
| 6 | R-002 | Robustness | Small | infrastructure-wide | Add Terraform version upper bound `< 2.0` |
| 7 | S-004 | Security | Small | single-resource | Add `prevent_destroy` to GCS bucket |
| 8 | S-005 | Security | Small | environment | Narrow OAuth scope on Cloud Scheduler |
| 9 | O-001 | Ops | Small | module | Add `common_labels` to all resources |
| 10 | Y-001 | Style | Small | single-resource | Extract GitHub URI parsing to locals |
| 11 | R-003+R-004 | Robustness | Small | single-resource | Add validation to `region` and `cloudbuild_repo_uri` |
| 12 | C-002+C-003 | CI/CD | Small | module | Add pre-commit hooks and TFLint |
| 13 | S-007 | Security | Medium | environment | Add audit logging configuration |

### Related Findings

- **S-001 + S-002**: Both relate to state and supply chain integrity — address together when setting up the remote backend.
- **C-001 + C-002 + C-003 + C-004 + C-005**: All CI/CD maturity gaps — address in a single PR that adds GitHub Actions with fmt, validate, tflint, and basic policy checks.
- **R-001 + R-002**: Version constraint tightening — can be a single commit.
- **S-003 + S-005**: IAM scoping — both reduce the blast radius of a compromised service account.
