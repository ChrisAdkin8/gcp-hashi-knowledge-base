variable "project_id" {
  type        = string
  description = "GCP project ID hosting the state bucket."
}

variable "region" {
  type        = string
  description = "GCS location for the state bucket."
  default     = "us-central1"

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "region must look like 'us-central1' or 'europe-west2'."
  }
}

variable "kms_key_name" {
  type        = string
  description = "Optional CMEK key resource name (projects/.../cryptoKeys/...). Null uses Google-managed encryption."
  default     = null
}
