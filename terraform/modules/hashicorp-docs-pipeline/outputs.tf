output "gcs_bucket_name" {
  description = "Name of the GCS bucket used for staging processed documentation."
  value       = google_storage_bucket.rag_docs.name
}

output "gcs_bucket_url" {
  description = "GCS URL of the staging bucket."
  value       = google_storage_bucket.rag_docs.url
}

output "service_account_email" {
  description = "Email of the RAG pipeline service account."
  value       = google_service_account.rag_pipeline.email
}

output "workflow_id" {
  description = "ID of the Cloud Workflows workflow."
  value       = google_workflows_workflow.rag_pipeline.id
}

output "workflow_name" {
  description = "Name of the Cloud Workflows workflow."
  value       = google_workflows_workflow.rag_pipeline.name
}

output "scheduler_job_name" {
  description = "Name of the Cloud Scheduler job."
  value       = google_cloud_scheduler_job.rag_weekly_refresh.name
}

output "document_ai_processor_id" {
  description = "ID of the Document AI layout parser processor."
  value       = google_document_ai_processor.layout_parser.id
}
