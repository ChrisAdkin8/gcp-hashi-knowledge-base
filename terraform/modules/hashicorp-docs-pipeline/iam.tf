resource "google_service_account" "rag_pipeline" {
  project      = var.project_id
  account_id   = "rag-pipeline-sa"
  display_name = "RAG Pipeline Service Account"
  description  = "Service account used by the HashiCorp RAG pipeline."
}

# Project-level roles that have no resource-level equivalent. See
# locals.tf for the rationale on each role.
resource "google_project_iam_member" "rag_pipeline_project_roles" {
  for_each = local.service_account_project_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.rag_pipeline.email}"
}

# Bucket-scoped storage admin (replaces the project-level
# roles/storage.objectAdmin).
resource "google_storage_bucket_iam_member" "rag_pipeline_bucket_admin" {
  bucket = google_storage_bucket.rag_docs.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.rag_pipeline.email}"
}

# Allow rag-pipeline-sa to act as itself when submitting Cloud Build jobs.
# Cloud Build validates that the caller has iam.serviceaccounts.actAs on the
# requested service account, even when it matches the caller.
resource "google_service_account_iam_member" "rag_pipeline_sa_self_user" {
  service_account_id = google_service_account.rag_pipeline.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.rag_pipeline.email}"
}
