// Project-wide Cloud Audit Log configuration.
//
// Enables Data Read and Data Write logs for the services that the docs and
// graph pipelines touch. Admin Read logs are always on by default and are
// not declared here. Without these audit configs, forensic reconstruction
// after a service-account compromise would not be possible (CIS GCP 2.1).
//
// One resource per service - the API rejects multiple audit_config blocks
// for the same service in a single resource.

locals {
  audited_services = toset([
    "aiplatform.googleapis.com",
    "spanner.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
    "documentai.googleapis.com",
    "iam.googleapis.com",
  ])
}

resource "google_project_iam_audit_config" "audited" {
  for_each = local.audited_services

  project = var.project_id
  service = each.value

  audit_log_config {
    log_type = "ADMIN_READ"
  }

  audit_log_config {
    log_type = "DATA_READ"
  }

  audit_log_config {
    log_type = "DATA_WRITE"
  }
}
