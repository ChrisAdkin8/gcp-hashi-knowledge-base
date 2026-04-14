resource "google_spanner_instance" "graph" {
  project          = var.project_id
  name             = var.spanner_instance_name
  config           = var.spanner_instance_config
  display_name     = var.spanner_instance_name
  processing_units = var.spanner_processing_units
  edition          = "ENTERPRISE"
  force_destroy    = true

  labels = local.common_labels

  timeouts {
    create = "20m"
    update = "20m"
    delete = "20m"
  }

  depends_on = [google_project_service.apis]
}

resource "google_spanner_database" "graph" {
  project                  = var.project_id
  instance                 = google_spanner_instance.graph.name
  name                     = var.spanner_database_name
  database_dialect         = "GOOGLE_STANDARD_SQL"
  version_retention_period = "1h"
  deletion_protection      = var.spanner_database_deletion_protection

  ddl = local.ddl_statements

  timeouts {
    create = "20m"
    update = "20m"
    delete = "20m"
  }
}
