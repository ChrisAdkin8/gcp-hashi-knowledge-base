// Root module for gcp-hashi-knowledge-base.
//
// Composes the docs ingestion pipeline (always on) and the optional
// Terraform dependency-graph pipeline (opt-in via create_graph_store).

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

module "hashicorp_docs_pipeline" {
  source = "./modules/hashicorp-docs-pipeline"

  project_id           = var.project_id
  region               = var.region
  environment          = var.environment
  cloudbuild_repo_uri  = var.cloudbuild_repo_uri
  refresh_schedule     = var.refresh_schedule
  scheduler_timezone   = var.scheduler_timezone
  embedding_model      = var.embedding_model
  notification_email   = var.notification_email
  documentai_location  = var.documentai_location
  workflow_source_path = "${path.root}/../workflows/rag_pipeline.yaml"
}

module "terraform_graph_store" {
  source = "./modules/terraform-graph-store"
  count  = var.create_graph_store ? 1 : 0

  project_id                           = var.project_id
  region                               = var.region
  environment                          = var.environment
  cloudbuild_repo_uri                  = var.cloudbuild_repo_uri
  graph_repo_uris                      = var.graph_repo_uris
  spanner_instance_name                = var.spanner_instance_name
  spanner_instance_config              = var.spanner_instance_config
  spanner_processing_units             = var.spanner_processing_units
  spanner_database_name                = var.spanner_database_name
  spanner_database_deletion_protection = var.spanner_database_deletion_protection
  graph_refresh_schedule               = var.graph_refresh_schedule
  scheduler_timezone                   = var.scheduler_timezone
  cloudbuild_machine_type              = var.graph_cloudbuild_machine_type
  build_timeout_seconds                = var.graph_build_timeout_seconds
  workflow_source_path                 = "${path.root}/../workflows/graph_pipeline.yaml"
}
