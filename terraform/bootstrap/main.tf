module "state_backend" {
  source = "../modules/state-backend"

  project_id   = var.project_id
  region       = var.region
  kms_key_name = var.kms_key_name
}
