resource "google_service_account" "graph_pipeline" {
  project      = var.project_id
  account_id   = "graph-pipeline-sa"
  display_name = "Terraform Graph Pipeline Service Account"
  description  = "Service account used by the Terraform dependency-graph ingestion pipeline."
}

# Project-level roles.  spanner.databaseUser is here (not database-scoped)
# because the doormat org policy blocks spanner.databases.setIamPolicy.
resource "google_project_iam_member" "graph_pipeline_project_roles" {
  for_each = local.service_account_project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.graph_pipeline.email}"
}

# Bucket-scoped storage admin (replaces the project-level
# roles/storage.objectAdmin).
resource "google_storage_bucket_iam_member" "graph_pipeline_bucket_admin" {
  bucket = google_storage_bucket.graph_staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.graph_pipeline.email}"
}

# Cloud Build runs builds AS this service account; the API requires the
# caller to have iam.serviceAccounts.actAs on the SA, even when the caller
# is the SA itself.
resource "google_service_account_iam_member" "graph_pipeline_sa_self_user" {
  service_account_id = google_service_account.graph_pipeline.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.graph_pipeline.email}"
}
