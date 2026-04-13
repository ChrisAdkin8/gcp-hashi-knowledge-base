locals {
  rag_bucket_name = "${var.project_id}-rag-docs-${substr(sha256(var.project_id), 0, 8)}"

  common_labels = {
    component   = "hashicorp-rag-docs"
    managed-by  = "terraform"
    environment = var.environment
  }

  required_apis = toset([
    "serviceusage.googleapis.com",
    "aiplatform.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
    "monitoring.googleapis.com",
    "documentai.googleapis.com",
  ])

  // Roles that have no resource-level equivalent and must be granted at the
  // project scope. Resource-level bindings (bucket, workflow, processor) are
  // declared separately in iam.tf.
  //
  // - aiplatform.user: Vertex AI RAG Engine corpus management. The RAG corpus
  //   is a Google-managed resource without an IAM resource type, so this
  //   binding cannot be scoped further. (Tightened from aiplatform.admin.)
  // - cloudbuild.builds.editor: required to submit builds; Cloud Build does
  //   not expose per-build or per-trigger IAM.
  // - logging.logWriter: required to emit structured logs.
  // - monitoring.metricWriter: required to write custom metrics.
  //   (Tightened from monitoring.editor.)
  // - documentai.apiUser: required to invoke processors. Tightened from
  //   the previous editor+viewer combination.
  // - workflows.invoker: Cloud Workflows does not expose resource-level IAM
  //   in the Google provider (`google_workflows_workflow_iam_member` does not
  //   exist). Cannot be scoped further.
  service_account_project_roles = toset([
    "roles/aiplatform.user",
    "roles/cloudbuild.builds.editor",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/documentai.apiUser",
    "roles/workflows.invoker",
  ])
}
