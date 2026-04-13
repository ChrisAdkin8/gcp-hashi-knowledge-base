variable "project_id" {
  type        = string
  description = "GCP project ID."
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

variable "cloudbuild_repo_uri" {
  type        = string
  description = "GitHub HTTPS URL of this repository. Cloud Build clones it to access pipeline scripts."
}

variable "refresh_schedule" {
  type        = string
  description = "Cron schedule for the weekly docs pipeline refresh (Cloud Scheduler format)."
  default     = "0 2 * * 0"
}

variable "scheduler_timezone" {
  type        = string
  description = "Timezone for the Cloud Scheduler job."
  default     = "Europe/London"
}

variable "embedding_model" {
  type        = string
  description = "Vertex AI embedding model resource path."
  default     = "publishers/google/models/text-embedding-005"
}

variable "notification_email" {
  type        = string
  description = "Email address for monitoring alerts. Set to the literal string \"none\" to explicitly opt out of alerting."
  default     = "none"

  validation {
    condition     = var.notification_email == "none" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "notification_email must be a valid email address, or the literal string \"none\" to acknowledge that alerting is disabled."
  }
}

variable "workflow_source_path" {
  type        = string
  description = "Filesystem path to the Cloud Workflows source YAML for the docs pipeline."
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

variable "documentai_location" {
  type        = string
  description = "Document AI processor location. Document AI is only available in 'us' or 'eu'."
  default     = "us"
  validation {
    condition     = contains(["us", "eu"], var.documentai_location)
    error_message = "documentai_location must be 'us' or 'eu' (the only Document AI regional footprints)."
  }
}
