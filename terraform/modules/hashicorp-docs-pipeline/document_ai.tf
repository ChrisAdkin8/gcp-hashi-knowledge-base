resource "google_document_ai_processor" "layout_parser" {
  project      = var.project_id
  location     = var.documentai_location
  display_name = "rag-layout-parser"
  type         = "LAYOUT_PARSER_PROCESSOR"

  depends_on = [google_project_service.apis]
}
