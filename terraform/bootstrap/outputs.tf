output "bucket_name" {
  description = "State bucket name - pass to main module terraform init."
  value       = module.state_backend.bucket_name
}

output "backend_config" {
  description = "Ready-to-use -backend-config flag for the main module terraform init."
  value       = module.state_backend.backend_config
}
