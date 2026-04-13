resource "google_cloud_scheduler_job" "graph_weekly_refresh" {
  project     = var.project_id
  name        = "${var.spanner_instance_name}-graph-refresh"
  description = "Triggers the Terraform graph pipeline on a weekly schedule."
  region      = var.region
  schedule    = var.graph_refresh_schedule
  time_zone   = var.scheduler_timezone

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/workflows/${google_workflows_workflow.graph_pipeline.name}/executions"
    body = base64encode(jsonencode({
      argument = jsonencode({
        graph_repo_uris      = var.graph_repo_uris
        cloudbuild_repo_uri  = var.cloudbuild_repo_uri
        graph_staging_bucket = google_storage_bucket.graph_staging.name
        spanner_instance     = google_spanner_instance.graph.name
        spanner_database     = google_spanner_database.graph.name
        region               = var.region
        service_account      = google_service_account.graph_pipeline.id
        machine_type         = var.cloudbuild_machine_type
        build_timeout        = "${var.build_timeout_seconds}s"
      })
    }))

    # OAuth scope MUST be cloud-platform; the Workflows Executions API does
    # not advertise a narrower scope. Privilege is constrained at the IAM
    # layer instead: graph-pipeline-sa only holds workflows.invoker on this
    # specific workflow (see iam.tf), not project-wide.
    oauth_token {
      service_account_email = google_service_account.graph_pipeline.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_project_service.apis]
}
