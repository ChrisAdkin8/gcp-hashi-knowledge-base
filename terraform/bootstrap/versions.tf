terraform {
  // Bootstrap uses a local backend - it manages only the state bucket,
  // which must exist before the main root module can initialise its GCS backend.
  // State for this module is stored locally in terraform/bootstrap/terraform.tfstate
  // and should be kept safe. The bucket is idempotent and prevent_destroy guards
  // against accidental deletion.
  required_version = ">= 1.5, < 2.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
