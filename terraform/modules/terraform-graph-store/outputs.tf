output "spanner_instance_name" {
  description = "Spanner instance name hosting the graph database."
  value       = google_spanner_instance.graph.name
}

output "spanner_database_name" {
  description = "Spanner database name for the property graph."
  value       = google_spanner_database.graph.name
}

output "spanner_database_id" {
  description = "Fully-qualified Spanner database ID (projects/.../instances/.../databases/...)."
  value       = "projects/${var.project_id}/instances/${google_spanner_instance.graph.name}/databases/${google_spanner_database.graph.name}"
}

output "graph_staging_bucket_name" {
  description = "GCS bucket name for graph DOT snapshots."
  value       = google_storage_bucket.graph_staging.name
}

output "graph_pipeline_service_account" {
  description = "Email of the graph pipeline service account."
  value       = google_service_account.graph_pipeline.email
}

output "graph_workflow_name" {
  description = "Name of the Cloud Workflows graph pipeline."
  value       = google_workflows_workflow.graph_pipeline.name
}

output "graph_scheduler_job_name" {
  description = "Name of the Cloud Scheduler job for the graph pipeline."
  value       = google_cloud_scheduler_job.graph_weekly_refresh.name
}
