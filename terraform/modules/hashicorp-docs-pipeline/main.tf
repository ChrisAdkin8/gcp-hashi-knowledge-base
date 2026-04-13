// Module: hashicorp-docs-pipeline
//
// Provisions the GCP infrastructure for the HashiCorp documentation
// ingestion pipeline. Resources are split across topical files:
//
//   apis.tf          - google_project_service activations
//   iam.tf           - service account + project IAM bindings
//   storage.tf       - GCS staging bucket
//   workflow.tf      - Cloud Workflows definition
//   scheduler.tf     - Cloud Scheduler weekly refresh job
//   document_ai.tf   - Layout parser processor
//   monitoring.tf    - Optional alert policies + notification channel
//   locals.tf        - Local values shared across the module
//   variables.tf     - Module inputs
//   outputs.tf       - Module outputs
