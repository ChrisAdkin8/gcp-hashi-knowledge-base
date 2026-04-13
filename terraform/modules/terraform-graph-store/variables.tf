variable "project_id" {
  type        = string
  description = "GCP project ID."
}

variable "region" {
  type        = string
  description = "GCP region for regional resources (Workflow, Scheduler, GCS bucket)."
  default     = "us-central1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must look like 'us-central1' or 'europe-west2'."
  }
}

variable "cloudbuild_repo_uri" {
  type        = string
  description = "GitHub HTTPS URL of THIS repository. Cloud Build clones it so the per-repo graph builds can access cloudbuild/scripts/ingest_graph.py."
}

variable "graph_repo_uris" {
  type        = list(string)
  description = "GitHub HTTPS URLs of Terraform workspace repositories to plan and ingest into Spanner."
  default     = []
}

variable "spanner_instance_name" {
  type        = string
  description = "Spanner instance display/identifier name for the graph store."
  default     = "hashicorp-rag-graph"
}

variable "spanner_instance_config" {
  type        = string
  description = "Spanner instance configuration (region or multi-region)."
  default     = "regional-us-central1"
}

variable "spanner_processing_units" {
  type        = number
  description = "Spanner instance processing units. 100 = minimum (~$65/mo)."
  default     = 100
  validation {
    condition     = var.spanner_processing_units >= 100 && var.spanner_processing_units % 100 == 0
    error_message = "spanner_processing_units must be a positive multiple of 100."
  }
}

variable "spanner_database_name" {
  type        = string
  description = "Spanner database name for the property graph."
  default     = "tf-graph"
}

variable "spanner_database_deletion_protection" {
  type        = bool
  description = "Prevent the Spanner database from being destroyed by Terraform."
  default     = true
}

variable "graph_refresh_schedule" {
  type        = string
  description = "Cron schedule for the graph pipeline refresh (Cloud Scheduler format)."
  default     = "0 3 * * 0"
}

variable "scheduler_timezone" {
  type        = string
  description = "Timezone for the Cloud Scheduler job."
  default     = "Europe/London"
}

variable "cloudbuild_machine_type" {
  type        = string
  description = "Cloud Build machine type for graph ingestion builds."
  default     = "E2_HIGHCPU_8"
}

variable "build_timeout_seconds" {
  type        = number
  description = "Per-repo Cloud Build timeout (seconds)."
  default     = 1800
}

variable "workflow_source_path" {
  type        = string
  description = "Filesystem path to the Cloud Workflows source YAML for the graph pipeline."
}

variable "environment" {
  type        = string
  description = "Deployment environment label (e.g. dev, staging, prod). Used for resource labels and cost attribution."
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}
