// TFLint configuration for gcp-hashi-knowledge-base.
//
// Runs in CI via .github/workflows/ci.yml. To run locally:
//   tflint --init
//   tflint --chdir=terraform --recursive

config {
  format     = "compact"
  call_module_type = "all"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "google" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-google"
}

# Resource-name spell-check is too noisy for the schema-driven names we use
# (e.g. "rag_pipeline", "graph_pipeline"). Re-enable if naming guidelines tighten.
rule "terraform_naming_convention" {
  enabled = false
}
