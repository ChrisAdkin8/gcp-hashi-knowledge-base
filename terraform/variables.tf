variable "project_id" {
  type        = string
  description = "GCP project ID."

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must not be empty."
  }
}

variable "region" {
  type        = string
  description = "GCP region for all resources."
  default     = "us-central1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must look like 'us-central1' or 'europe-west2'."
  }
}

variable "refresh_schedule" {
  type        = string
  description = "Cron schedule for the weekly pipeline refresh (Cloud Scheduler format)."
  default     = "0 2 * * 0"

  validation {
    condition     = can(regex("^([*/0-9,-]+\\s+){4}[*/0-9,-]+$", var.refresh_schedule))
    error_message = "refresh_schedule must be a 5-field cron expression."
  }
}

variable "scheduler_timezone" {
  type        = string
  description = "Timezone for the Cloud Scheduler job (IANA tz database name)."
  default     = "Europe/London"

  validation {
    condition     = can(regex("^[A-Za-z_]+/[A-Za-z_]+(/[A-Za-z_]+)?$|^UTC$", var.scheduler_timezone))
    error_message = "scheduler_timezone must be an IANA timezone (e.g. Europe/London) or UTC."
  }
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

variable "cloudbuild_repo_uri" {
  type        = string
  description = "GitHub HTTPS URL of this repository. Cloud Build clones it to access pipeline scripts — no GitHub App installation or PAT required for public repos."

  validation {
    condition     = length(var.cloudbuild_repo_uri) > 0
    error_message = "cloudbuild_repo_uri must not be empty."
  }
}

variable "embedding_model" {
  type        = string
  description = "Vertex AI embedding model resource path."
  default     = "publishers/google/models/text-embedding-005"

  validation {
    condition     = can(regex("^publishers/google/models/text-embedding-", var.embedding_model))
    error_message = "embedding_model must be a Google text-embedding model path (publishers/google/models/text-embedding-*)."
  }
}

variable "documentai_location" {
  type        = string
  description = "Document AI processor location. Document AI is only available in 'us' or 'eu'."
  default     = "us"

  validation {
    condition     = contains(["us", "eu"], var.documentai_location)
    error_message = "documentai_location must be 'us' or 'eu'."
  }
}

variable "notification_email" {
  type        = string
  description = "Email address for monitoring alerts. Set to the literal string \"none\" to explicitly opt out of alerting (no channel or alert policies are created)."
  default     = "none"

  validation {
    condition     = var.notification_email == "none" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, or the literal string \"none\" to acknowledge that alerting is disabled."
  }
}

# ---------------------------------------------------------------------------
# Optional Terraform dependency-graph pipeline (Spanner Graph backend).
# Set create_graph_store = true and populate graph_repo_uris to enable.
# ---------------------------------------------------------------------------

variable "create_graph_store" {
  type        = bool
  description = "If true, deploy the Spanner-backed Terraform dependency-graph pipeline."
  default     = false
}

variable "graph_repo_uris" {
  type        = list(string)
  description = "GitHub HTTPS URLs of Terraform workspace repositories whose dependency graphs should be ingested into Spanner."
  default     = []
}

variable "spanner_instance_name" {
  type        = string
  description = "Spanner instance name for the graph store."
  default     = "hashicorp-rag-graph"
}

variable "spanner_instance_config" {
  type        = string
  description = "Spanner instance configuration (region or multi-region)."
  default     = "regional-us-central1"
}

variable "spanner_processing_units" {
  type        = number
  description = "Spanner processing units. Must be a positive multiple of 100."
  default     = 100
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

  validation {
    condition     = can(regex("^([*/0-9,-]+\\s+){4}[*/0-9,-]+$", var.graph_refresh_schedule))
    error_message = "graph_refresh_schedule must be a 5-field cron expression."
  }
}

variable "graph_cloudbuild_machine_type" {
  type        = string
  description = "Cloud Build machine type for per-repo graph ingestion builds."
  default     = "E2_HIGHCPU_8"

  validation {
    condition = contains([
      "UNSPECIFIED",
      "N1_HIGHCPU_8",
      "N1_HIGHCPU_32",
      "E2_HIGHCPU_8",
      "E2_HIGHCPU_32",
      "E2_MEDIUM",
    ], var.graph_cloudbuild_machine_type)
    error_message = "graph_cloudbuild_machine_type must be a supported Cloud Build worker pool machine type."
  }
}

variable "graph_build_timeout_seconds" {
  type        = number
  description = "Per-repo Cloud Build timeout (seconds) for graph ingestion."
  default     = 1800

  validation {
    condition     = var.graph_build_timeout_seconds >= 60 && var.graph_build_timeout_seconds <= 86400
    error_message = "graph_build_timeout_seconds must be between 60 (1 minute) and 86400 (24 hours)."
  }
}

