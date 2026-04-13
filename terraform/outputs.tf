output "gcs_bucket_name" {
  description = "Name of the GCS bucket used for staging processed documentation."
  value       = module.hashicorp_docs_pipeline.gcs_bucket_name
}

output "gcs_bucket_url" {
  description = "GCS URL of the staging bucket."
  value       = module.hashicorp_docs_pipeline.gcs_bucket_url
}

output "service_account_email" {
  description = "Email of the RAG pipeline service account."
  value       = module.hashicorp_docs_pipeline.service_account_email
}

output "workflow_id" {
  description = "ID of the Cloud Workflows workflow."
  value       = module.hashicorp_docs_pipeline.workflow_id
}

output "workflow_name" {
  description = "Name of the Cloud Workflows workflow."
  value       = module.hashicorp_docs_pipeline.workflow_name
}

output "scheduler_job_name" {
  description = "Name of the Cloud Scheduler job."
  value       = module.hashicorp_docs_pipeline.scheduler_job_name
}

output "document_ai_processor_id" {
  description = "ID of the Document AI layout parser processor."
  value       = module.hashicorp_docs_pipeline.document_ai_processor_id
}

# ---------------------------------------------------------------------------
# Graph pipeline outputs (null when create_graph_store = false).
# ---------------------------------------------------------------------------

output "spanner_instance_name" {
  description = "Spanner instance hosting the graph database (null if disabled)."
  value       = try(module.terraform_graph_store[0].spanner_instance_name, null)
}

output "spanner_database_name" {
  description = "Spanner database name for the property graph (null if disabled)."
  value       = try(module.terraform_graph_store[0].spanner_database_name, null)
}

output "spanner_database_id" {
  description = "Fully-qualified Spanner database ID (null if disabled)."
  value       = try(module.terraform_graph_store[0].spanner_database_id, null)
}

output "graph_staging_bucket_name" {
  description = "GCS bucket for graph DOT snapshots (null if disabled)."
  value       = try(module.terraform_graph_store[0].graph_staging_bucket_name, null)
}

output "graph_pipeline_service_account" {
  description = "Email of the graph pipeline service account (null if disabled)."
  value       = try(module.terraform_graph_store[0].graph_pipeline_service_account, null)
}

output "graph_workflow_name" {
  description = "Name of the Cloud Workflows graph pipeline (null if disabled)."
  value       = try(module.terraform_graph_store[0].graph_workflow_name, null)
}

output "graph_scheduler_job_name" {
  description = "Name of the Cloud Scheduler job for the graph pipeline (null if disabled)."
  value       = try(module.terraform_graph_store[0].graph_scheduler_job_name, null)
}
