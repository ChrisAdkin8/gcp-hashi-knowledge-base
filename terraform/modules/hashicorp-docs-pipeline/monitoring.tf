resource "google_monitoring_notification_channel" "email" {
  count = var.notification_email != "none" ? 1 : 0

  project      = var.project_id
  display_name = "RAG Pipeline Email Alerts"
  type         = "email"

  labels = {
    email_address = var.notification_email
  }
}

resource "google_monitoring_alert_policy" "workflow_failures" {
  count = var.notification_email != "none" ? 1 : 0

  project      = var.project_id
  display_name = "RAG Pipeline - Workflow Execution Failures"
  combiner     = "OR"

  conditions {
    display_name = "Workflow execution failed"
    condition_matched_log {
      filter = <<-EOT
        resource.type="workflows.googleapis.com/Workflow"
        resource.labels.workflow_id="${google_workflows_workflow.rag_pipeline.name}"
        severity>=ERROR
      EOT
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }

  depends_on = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "build_failures" {
  count = var.notification_email != "none" ? 1 : 0

  project      = var.project_id
  display_name = "RAG Pipeline - Cloud Build Failures"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Build step failed"
    condition_matched_log {
      filter = <<-EOT
        resource.type="build"
        jsonPayload.status="FAILURE"
        jsonPayload.substitutions._RAG_BUCKET="${local.rag_bucket_name}"
      EOT
    }
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]

  alert_strategy {
    notification_rate_limit {
      period = "3600s"
    }
    auto_close = "86400s"
  }

  depends_on = [google_project_service.apis]
}
