#!/usr/bin/env bash
# deploy.sh — End-to-end provisioning of the HashiCorp RAG pipeline.
#
# Runs four steps in sequence, each idempotent:
#   1. Bootstrap GCS state bucket (skipped if already exists)
#   2. Create (or find) the Vertex AI RAG corpus → write corpus.auto.tfvars
#   3. Terraform apply (service account, IAM, GCS bucket, workflow, scheduler, Document AI)
#   4. Trigger first pipeline run with the corpus ID
#
# The RAG corpus is NOT a Terraform resource.  It is created once by
# scripts/create_corpus.py (get-or-create) and its ID is passed to Terraform
# via corpus.auto.tfvars so the scheduler always includes it in workflow args.
#
# The state bucket name is derived automatically from the project ID:
#   <PROJECT_ID>-tf-state-<8-char hash>
#
# Usage:
#   scripts/deploy.sh \
#     --project-id   my-project       \
#     --region       us-central1      \
#     --repo-uri     https://github.com/org/repo
#
# Optional:
#   --skip-pipeline    Skip step 4 (useful for infra-only re-runs).
#
# Variables can also be supplied via env:
#   GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GOOGLE_CLOUD_REGION:-us-west1}"
REPO_URI=""
SKIP_PIPELINE=false

# ── Argument parsing ───────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)    PROJECT_ID="$2";    shift 2 ;;
    --region)        REGION="$2";        shift 2 ;;
    --repo-uri)      REPO_URI="$2";      shift 2 ;;
    --skip-pipeline) SKIP_PIPELINE=true; shift   ;;
    *) echo "ERROR: Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Auto-detect project ID from gcloud if not provided.
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [[ -z "${PROJECT_ID}" ]]; then echo "ERROR: No project ID. Pass --project-id, set GOOGLE_CLOUD_PROJECT, or run 'gcloud config set project <id>'." >&2; exit 1; fi
if [[ -z "${REPO_URI}" ]];   then echo "ERROR: --repo-uri is required."                                 >&2; exit 1; fi

# Derive bucket names deterministically from project_id (mirror the Terraform locals).
STATE_BUCKET="${PROJECT_ID}-tf-state-$(echo -n "${PROJECT_ID}" | sha256sum | cut -c1-8)"
RAG_BUCKET="${PROJECT_ID}-rag-docs-$(echo -n "${PROJECT_ID}" | sha256sum | cut -c1-8)"

# Ensure Terraform's GCS backend uses the correct project for API billing.
# Without this, ADC's quota_project_id may point to a different project,
# causing "bucket doesn't exist" errors even when the bucket is accessible.
export GOOGLE_CLOUD_QUOTA_PROJECT="${PROJECT_ID}"

# ── Step 1: Bootstrap state bucket ────────────────────────────────────────────

echo ""
echo "=== [1/4] Bootstrap state bucket ==="
"${REPO_ROOT}/scripts/bootstrap_state.sh" \
  --project-id "${PROJECT_ID}" \
  --region     "${REGION}"

# ── Step 2: Create (or find) the RAG corpus ──────────────────────────────────

echo ""
echo "=== [2/4] Ensure RAG corpus exists ==="

CORPUS_ID=$("${REPO_ROOT}/.venv/bin/python3" "${REPO_ROOT}/scripts/create_corpus.py" \
  --project-id "${PROJECT_ID}" \
  --region     "${REGION}" \
  --output-id-only)

if [[ -z "${CORPUS_ID}" ]]; then
  echo "ERROR: Failed to create or find RAG corpus." >&2
  exit 1
fi
echo "  Corpus ID: ${CORPUS_ID}"

# Persist the corpus ID so Terraform (and the scheduler) always have it.
CORPUS_TFVARS="${TF_DIR}/corpus.auto.tfvars"
echo "corpus_id = \"${CORPUS_ID}\"" > "${CORPUS_TFVARS}"
echo "  Wrote ${CORPUS_TFVARS}"

# ── Step 3: Terraform apply (infrastructure) ─────────────────────────────────

echo ""
echo "=== [3/4] Terraform apply (infrastructure) ==="

terraform -chdir="${TF_DIR}" init \
  -backend-config="bucket=${STATE_BUCKET}" \
  -input=false \
  -reconfigure

# Auto-generate terraform.tfvars if the user hasn't created one yet.
TFVARS="${TF_DIR}/terraform.tfvars"
if [[ ! -f "${TFVARS}" ]]; then
  echo "  No terraform.tfvars found — generating from arguments."
  cat > "${TFVARS}" <<TFVARS_CONTENT
project_id          = "${PROJECT_ID}"
region              = "${REGION}"
cloudbuild_repo_uri = "${REPO_URI}"
TFVARS_CONTENT
  echo "  Wrote ${TFVARS}"
fi

terraform -chdir="${TF_DIR}" apply -input=false -auto-approve

# IAM bindings can take up to 60-120 s to propagate globally.
# Without this wait the first pipeline run races the policy and gets a 403.
echo ""
echo "Waiting 90 s for IAM propagation before triggering the pipeline …"
sleep 90

# ── Step 4: Trigger first pipeline run ────────────────────────────────────────

echo ""
if [[ "${SKIP_PIPELINE}" == "true" ]]; then
  echo "=== [4/4] Pipeline trigger skipped (--skip-pipeline) ==="
else
  echo "=== [4/4] Trigger first pipeline run ==="

  SERVICE_ACCOUNT="projects/${PROJECT_ID}/serviceAccounts/rag-pipeline-sa@${PROJECT_ID}.iam.gserviceaccount.com"
  # Read repo URI from tfvars (source of truth Terraform just applied) rather
  # than the CLI arg, so the manual trigger always matches the deployed scheduler.
  APPLIED_REPO_URI=$(grep '^cloudbuild_repo_uri' "${TFVARS}" | sed 's/.*= *"\(.*\)"/\1/')
  if [[ -z "${APPLIED_REPO_URI}" ]]; then
    echo "WARNING: Could not read cloudbuild_repo_uri from ${TFVARS}, falling back to --repo-uri" >&2
    APPLIED_REPO_URI="${REPO_URI}"
  fi
  WORKFLOW_DATA=$("${REPO_ROOT}/.venv/bin/python3" -c "
import json
print(json.dumps({
  'corpus_id':       '${CORPUS_ID}',
  'bucket_name':     '${RAG_BUCKET}',
  'region':          '${REGION}',
  'repo_url':        '${APPLIED_REPO_URI}',
  'service_account': '${SERVICE_ACCOUNT}',
}))
")

  "${REPO_ROOT}/scripts/run_pipeline.sh" \
    --project-id "${PROJECT_ID}" \
    --region     "${REGION}" \
    --data       "${WORKFLOW_DATA}" \
    --wait
fi

# ── Summary ────────────────────────────────────────────────────────────────────

echo ""
echo "══════════════════════════════════════════════"
echo "  RAG pipeline deployed successfully."
echo "  RAG bucket   : gs://${RAG_BUCKET}"
echo "  State bucket : gs://${STATE_BUCKET}"
echo "  Corpus ID    : ${CORPUS_ID}"
echo ""
echo "  To destroy all infrastructure:"
echo "    task destroy"
echo "══════════════════════════════════════════════"
