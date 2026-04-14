locals {
  staging_bucket_name = "${var.project_id}-graph-staging-${substr(sha256(var.project_id), 0, 8)}"

  common_labels = {
    component   = "hashicorp-rag-graph"
    managed-by  = "terraform"
    environment = var.environment
  }

  required_apis = toset([
    "spanner.googleapis.com",
    "cloudbuild.googleapis.com",
    "workflows.googleapis.com",
    "cloudscheduler.googleapis.com",
    "storage.googleapis.com",
    "logging.googleapis.com",
  ])

  // Roles that have no resource-level equivalent and must be granted at the
  // project scope. Bucket-scoped bindings are declared separately in iam.tf.
  // spanner.databaseUser is project-level because the doormat org policy
  // blocks spanner.databases.setIamPolicy. workflows.invoker stays
  // project-level because the provider has no workflow-level IAM resource.
  service_account_project_roles = toset([
    "roles/cloudbuild.builds.editor",
    "roles/logging.logWriter",
    "roles/spanner.databaseUser",
    "roles/workflows.invoker",
  ])

  // Spanner Graph DDL.
  //
  // Resource is the node table; DependsOn is interleaved as a child so the
  // FK relationship is enforced and per-repo cleanup cascades.
  // CREATE PROPERTY GRAPH must be in the SAME ddl batch as the tables it
  // references - splitting into two update_ddl calls fails with
  // "Table not found".
  ddl_statements = [
    <<-SQL
      CREATE TABLE Resource (
        repo_uri    STRING(MAX) NOT NULL,
        resource_id STRING(MAX) NOT NULL,
        type        STRING(MAX),
        name        STRING(MAX),
        updated_at  TIMESTAMP OPTIONS (allow_commit_timestamp = true),
      ) PRIMARY KEY (repo_uri, resource_id)
    SQL
    ,
    <<-SQL
      CREATE TABLE DependsOn (
        repo_uri    STRING(MAX) NOT NULL,
        resource_id STRING(MAX) NOT NULL,
        dst_id      STRING(MAX) NOT NULL,
        updated_at  TIMESTAMP OPTIONS (allow_commit_timestamp = true),
      ) PRIMARY KEY (repo_uri, resource_id, dst_id),
        INTERLEAVE IN PARENT Resource ON DELETE CASCADE
    SQL
    ,
    <<-SQL
      CREATE PROPERTY GRAPH tf_graph
        NODE TABLES (
          Resource KEY (repo_uri, resource_id) LABEL Resource
        )
        EDGE TABLES (
          DependsOn
            SOURCE      KEY (repo_uri, resource_id) REFERENCES Resource (repo_uri, resource_id)
            DESTINATION KEY (repo_uri, dst_id)      REFERENCES Resource (repo_uri, resource_id)
            LABEL DEPENDS_ON
        )
    SQL
  ]
}
