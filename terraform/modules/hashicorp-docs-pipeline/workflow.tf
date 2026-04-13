resource "google_workflows_workflow" "rag_pipeline" {
  project             = var.project_id
  name                = "rag-hashicorp-pipeline"
  region              = var.region
  description         = "Orchestrates the HashiCorp RAG documentation ingestion pipeline."
  service_account     = google_service_account.rag_pipeline.id
  deletion_protection = true
  source_contents     = file(var.workflow_source_path)

  depends_on = [google_project_service.apis]
}
