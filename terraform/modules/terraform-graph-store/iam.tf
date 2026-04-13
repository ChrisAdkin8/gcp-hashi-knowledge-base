resource "google_service_account" "graph_pipeline" {
  project      = var.project_id
  account_id   = "graph-pipeline-sa"
  display_name = "Terraform Graph Pipeline Service Account"
  description  = "Service account used by the Terraform dependency-graph ingestion pipeline."
}

# Project-level roles that have no resource-level equivalent.
resource "google_project_iam_member" "graph_pipeline_project_roles" {
  for_each = local.service_account_project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.graph_pipeline.email}"
}

# Database-scoped Spanner access (replaces the project-level
# roles/spanner.databaseUser).
resource "google_spanner_database_iam_member" "graph_pipeline_database_user" {
  project  = var.project_id
  instance = google_spanner_instance.graph.name
  database = google_spanner_database.graph.name
  role     = "roles/spanner.databaseUser"
  member   = "serviceAccount:${google_service_account.graph_pipeline.email}"
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
