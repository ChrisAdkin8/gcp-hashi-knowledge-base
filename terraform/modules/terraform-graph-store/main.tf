// Module: terraform-graph-store
//
// Provisions the GCP infrastructure for the Terraform dependency-graph
// pipeline. Files are split topically to mirror the docs module:
//
//   apis.tf       - google_project_service activations specific to the graph side
//   iam.tf        - graph-pipeline service account + project IAM bindings
//   spanner.tf    - Spanner instance, database, and property-graph DDL
//   storage.tf    - GCS staging bucket for raw terraform graph DOT snapshots
//   workflow.tf   - Cloud Workflows orchestrator
//   scheduler.tf  - Cloud Scheduler weekly refresh job
//   locals.tf     - Local values shared across the module
//   variables.tf  - Module inputs
//   outputs.tf    - Module outputs
//
// The module is opt-in: the root composes it conditionally on
// var.create_graph_store. Auth uses Application Default Credentials
// throughout - no manual SigV4-style signing.
