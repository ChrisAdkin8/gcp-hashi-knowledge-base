#!/usr/bin/env bash
# bootstrap_state.sh — One-time setup for Terraform remote state.
#
# Creates a GCS bucket for state storage, then initialises Terraform with the
# remote backend. Run this ONCE before any other Terraform commands.
#
# The state bucket name is derived automatically from the project ID:
#   <PROJECT_ID>-tf-state-<8-char hash>
#
# Usage:
#   scripts/bootstrap_state.sh --project-id my-project [--region us-central1]

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --region)     REGION="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: --project-id is required (or set GOOGLE_CLOUD_PROJECT)." >&2; exit 1
fi

STATE_BUCKET="${PROJECT_ID}-tf-state-$(echo -n "${PROJECT_ID}" | sha256sum | cut -c1-8)"

# Ensure Terraform's GCS backend uses the correct project for API billing.
# Without this, ADC's quota_project_id may point to a different project,
# causing "bucket doesn't exist" errors even when the bucket is accessible.
export GOOGLE_CLOUD_QUOTA_PROJECT="${PROJECT_ID}"

echo "=== Verifying GCP project ==="
if ! gcloud projects describe "${PROJECT_ID}" &>/dev/null; then
  echo "ERROR: GCP project '${PROJECT_ID}' not found or you don't have access." >&2
  echo "  Verify the project ID with: gcloud projects list" >&2
  echo "  Active account: $(gcloud config get-value account 2>/dev/null)" >&2
  exit 1
fi
echo "  Project ${PROJECT_ID} verified."

echo ""
echo "=== Creating GCS state bucket ==="
if gcloud storage buckets describe "gs://${STATE_BUCKET}" --project="${PROJECT_ID}" &>/dev/null; then
  echo "  Bucket gs://${STATE_BUCKET} already exists — skipping creation."
else
  gcloud storage buckets create "gs://${STATE_BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access \
    --public-access-prevention
  echo "  Created gs://${STATE_BUCKET}"
fi

echo ""
echo "=== Enabling versioning ==="
gcloud storage buckets update "gs://${STATE_BUCKET}" --versioning
echo "  Versioning enabled."

echo ""
echo "=== Checking Application Default Credentials ==="
if ! gcloud auth application-default print-access-token &>/dev/null; then
  echo "  WARNING: Application Default Credentials not configured."
  echo "  Terraform uses ADC (not gcloud credentials) to access GCS."
  echo "  Running: gcloud auth application-default login"
  gcloud auth application-default login
fi
echo "  ADC credentials verified."

echo ""
echo "=== Initialising Terraform with remote backend ==="
echo "  Using bucket: ${STATE_BUCKET}"

# Retry up to 5 times — GCS may need a moment after creation
TF_INIT_OK=false
for attempt in 1 2 3 4 5; do
  if terraform -chdir="$(dirname "$0")/../terraform" init \
       -backend-config="bucket=${STATE_BUCKET}" \
       -input=false \
       -reconfigure; then
    TF_INIT_OK=true
    break
  fi
  echo "  terraform init attempt ${attempt}/5 failed — retrying in 5s…"
  sleep 5
done

if [[ "${TF_INIT_OK}" != "true" ]]; then
  echo "ERROR: terraform init failed after 5 attempts." >&2
  echo "  Bucket name used: ${STATE_BUCKET}" >&2
  echo "  Verify ADC credentials with: gcloud auth application-default print-access-token" >&2
  echo "  Verify bucket exists with: gcloud storage ls gs://${STATE_BUCKET}" >&2
  exit 1
fi

echo ""
echo "=== Bootstrap complete ==="
echo "  State bucket : gs://${STATE_BUCKET}"
echo "  Backend      : GCS (locked)"
echo ""
echo "Add the following to your CI/CD environment or local shell profile:"
echo "  export TF_CLI_ARGS_init=\"-backend-config=bucket=${STATE_BUCKET}\""
