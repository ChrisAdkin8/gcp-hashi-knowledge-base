// Module: state-backend
//
// Provisions the GCS bucket used to hold remote Terraform state for the
// gcp-hashi-knowledge-base root module.
//
// The bucket name is derived from the project ID to keep it globally unique
// without requiring user input. CMEK is opt-in via kms_key_name; when null
// the bucket uses Google-managed encryption.

locals {
  bucket_name = "${var.project_id}-tf-state-${substr(sha256(var.project_id), 0, 8)}"
}

resource "google_storage_bucket" "state" {
  project                     = var.project_id
  name                        = local.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  dynamic "encryption" {
    for_each = var.kms_key_name == null ? [] : [var.kms_key_name]
    content {
      default_kms_key_name = encryption.value
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
