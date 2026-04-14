resource "google_cloud_scheduler_job" "rag_weekly_refresh" {
  project     = var.project_id
  name        = "rag-weekly-refresh"
  description = "Triggers the RAG pipeline on a weekly schedule."
  region      = var.region
  schedule    = var.refresh_schedule
  time_zone   = var.scheduler_timezone

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.rag_pipeline.name}/executions"
    body = base64encode(jsonencode({
      argument = jsonencode({
        corpus_id       = var.corpus_id
        bucket_name     = local.rag_bucket_name
        region          = var.region
        repo_url        = var.cloudbuild_repo_uri
        service_account = google_service_account.rag_pipeline.id
      })
    }))

    # OAuth scope MUST be cloud-platform; the Workflows Executions API does
    # not advertise a narrower scope. workflows.invoker also has to be
    # granted at project scope because the Google provider does not expose
    # workflow-level IAM. The other dangerous roles (storage.objectAdmin,
    # documentai, aiplatform.admin) have been narrowed in iam.tf and
    # locals.tf, so the residual blast radius of this token is "invoke any
    # workflow in the project + write metrics + emit logs".
    oauth_token {
      service_account_email = google_service_account.rag_pipeline.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_service.apis]
}
