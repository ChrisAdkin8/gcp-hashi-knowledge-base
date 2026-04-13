output "bucket_name" {
  description = "State bucket name - pass to main module terraform init via -backend-config=bucket=<name>."
  value       = google_storage_bucket.state.name
}

output "bucket_url" {
  description = "GCS URL of the state bucket."
  value       = google_storage_bucket.state.url
}

output "backend_config" {
  description = "Ready-to-use -backend-config flag for the main module terraform init."
  value       = "bucket=${google_storage_bucket.state.name}"
}
