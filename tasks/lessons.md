# Lessons — gcp-hashi-knowledge-base

Append entries here whenever a hard-failure mode is discovered. Each entry
becomes a candidate for the **GCP Constraints (Hard Failures)** block in
`CLAUDE.md`.

## Format

```
### YYYY-MM-DD — Short title

**Symptom:** What went wrong (error message, behaviour).

**Root cause:** Why it happened.

**Fix:** What you changed to make it work.

**Prevention:** What rule belongs in CLAUDE.md so it doesn't happen again.
```

---

### 2026-04-13 — Spanner GRAPH feature requires ENTERPRISE edition

**Symptom:** `terraform apply` fails with `Error 400: Feature GRAPH is not available to Instance … in Edition STANDARD. The minimum required Edition for this feature is ENTERPRISE.`

**Root cause:** `google_spanner_instance` defaulted to STANDARD edition. Spanner property graph (`CREATE PROPERTY GRAPH`) is gated behind ENTERPRISE.

**Fix:** Added `edition = "ENTERPRISE"` to the `google_spanner_instance` resource in `terraform/modules/terraform-graph-store/spanner.tf`.

**Prevention:** Added to CLAUDE.md GCP Constraints: the Spanner instance for the graph store must set `edition = "ENTERPRISE"`. STANDARD edition cannot use the GRAPH feature.

---

### 2026-04-13 — Cloud Workflows expressions reject inline map literals

**Symptom:** `terraform apply` fails with `parse error: extraneous input '{' expecting …` and `token recognition error at ':'` on lines in `graph_pipeline.yaml` containing `list.concat(results, [{"key": value, …}])`.

**Root cause:** Cloud Workflows expression syntax (`${...}`) does not support inline map/dict literals using `{}`. The `{` is parsed as a syntax error.

**Fix:** Replaced the single `assign` steps with a two-step pattern: first an `assign` step that builds the map as a YAML object (no quotes, no `${}`), then a second `assign` step that calls `list.concat` referencing the named variable.

**Prevention:** Added to CLAUDE.md GCP Constraints: never use `{"key": value}` inside a Workflows `${...}` expression. Construct maps via named `assign`-step YAML objects and reference the variable name in the expression.

---

### 2026-04-13 — Spanner database-level IAM blocked by doormat org policy

**Symptom:** `terraform apply` fails with `Error 403: Caller is missing IAM permission spanner.databases.setIamPolicy`. Granting `roles/spanner.admin` to the user is silently dropped by the org policy.

**Root cause:** The HashiCorp doormat org policy strips `spanner.databases.setIamPolicy` from all principals. `google_spanner_database_iam_member` requires this permission.

**Fix:** Moved `roles/spanner.databaseUser` from a database-scoped `google_spanner_database_iam_member` to the project-level `google_project_iam_member` set in `locals.tf`.

**Prevention:** Added to CLAUDE.md: don't use `google_spanner_database_iam_member`; use project-level IAM bindings instead.
