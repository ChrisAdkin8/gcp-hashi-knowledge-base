resource "google_workflows_workflow" "graph_pipeline" {
  project             = var.project_id
  name                = "${var.spanner_instance_name}-graph-pipeline"
  region              = var.region
  description         = "Orchestrates the Terraform dependency-graph ingestion pipeline."
  service_account     = google_service_account.graph_pipeline.id
  deletion_protection = false
  source_contents     = file(var.workflow_source_path)

  depends_on = [google_project_service.apis]
}
