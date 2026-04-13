terraform {
  required_version = ">= 1.5, < 2.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.50"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.50"
    }
  }

  backend "gcs" {
    # Bucket is supplied at init time via -backend-config="bucket=<NAME>".
    # Run scripts/bootstrap_state.sh or task bootstrap to create the bucket first.
    prefix = "terraform/state/rag-pipeline"
  }
}
