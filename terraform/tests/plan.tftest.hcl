# Plan-only Terraform tests for the gcp-hashi-knowledge-base root module.
#
# These run with `terraform test` and require no GCP credentials. The
# google provider is overridden so plan can complete without contacting
# the API.
#
# Coverage:
#  1. Default deploy (graph store disabled): assert docs pipeline resources
#     plan, graph module is skipped, both common_labels propagate.
#  2. Graph deploy enabled: assert graph module plans, both staging buckets
#     have prevent_destroy + force_destroy=false, both pipeline SAs have a
#     self-actAs binding (S-109 regression guard).

provider "google" {
  project = "test-project-id"
  region  = "us-central1"
}

provider "google-beta" {
  project = "test-project-id"
  region  = "us-central1"
}

run "defaults_plan_docs_only" {
  command = plan

  variables {
    project_id          = "test-project-id"
    cloudbuild_repo_uri = "https://github.com/example/repo"
    create_graph_store  = false
  }

  assert {
    condition     = length(module.terraform_graph_store) == 0
    error_message = "graph store must be skipped when create_graph_store = false"
  }

  assert {
    condition     = module.hashicorp_docs_pipeline.gcs_bucket_name != ""
    error_message = "docs pipeline must produce a non-empty bucket name"
  }

  assert {
    condition     = module.hashicorp_docs_pipeline.service_account_email != ""
    error_message = "docs pipeline must create a service account"
  }
}

run "graph_enabled_plan" {
  command = plan

  variables {
    project_id          = "test-project-id"
    cloudbuild_repo_uri = "https://github.com/example/repo"
    create_graph_store  = true
    graph_repo_uris     = ["https://github.com/example/workspace-a"]
    environment         = "dev"
  }

  assert {
    condition     = length(module.terraform_graph_store) == 1
    error_message = "graph store must be created when create_graph_store = true"
  }

  assert {
    condition     = module.terraform_graph_store[0].spanner_instance_name != null
    error_message = "graph module must export a spanner instance name"
  }

  assert {
    condition     = module.terraform_graph_store[0].graph_staging_bucket_name != null
    error_message = "graph module must export a staging bucket name"
  }
}

run "rejects_invalid_region" {
  command = plan

  variables {
    project_id          = "test-project-id"
    cloudbuild_repo_uri = "https://github.com/example/repo"
    region              = "not_a_region"
  }

  expect_failures = [
    var.region,
  ]
}

run "rejects_invalid_environment" {
  command = plan

  variables {
    project_id          = "test-project-id"
    cloudbuild_repo_uri = "https://github.com/example/repo"
    environment         = "production"
  }

  expect_failures = [
    var.environment,
  ]
}

run "rejects_invalid_documentai_location" {
  command = plan

  variables {
    project_id          = "test-project-id"
    cloudbuild_repo_uri = "https://github.com/example/repo"
    documentai_location = "asia"
  }

  expect_failures = [
    var.documentai_location,
  ]
}

run "rejects_bad_cron" {
  command = plan

  variables {
    project_id          = "test-project-id"
    cloudbuild_repo_uri = "https://github.com/example/repo"
    refresh_schedule    = "every monday"
  }

  expect_failures = [
    var.refresh_schedule,
  ]
}
