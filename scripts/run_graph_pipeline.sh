#!/usr/bin/env bash
# run_graph_pipeline.sh — Trigger the Terraform graph pipeline Cloud Workflow.
#
# Reads the workflow argument fields from terraform output (or flags) and
# starts a Cloud Workflows execution against the graph pipeline. Optionally
# polls until completion.
#
# Usage:
#   scripts/run_graph_pipeline.sh \
#     [--project-id my-project] [--region us-central1] [--wait]
#
# Project ID is resolved in order: --project-id flag > GOOGLE_CLOUD_PROJECT
# env var > gcloud config get-value project. Region defaults to us-central1
# or GOOGLE_CLOUD_REGION.
#
# All other workflow argument fields (graph_repo_uris, spanner_instance,
# spanner_database, graph_staging_bucket, service_account, machine_type,
# build_timeout, cloudbuild_repo_uri) are read from `terraform output -json`
# in ./terraform.

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
WAIT=false

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --wait)
      WAIT=true
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--project-id <PROJECT>] [--region <REGION>] [--wait]" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: --project-id is required (or set GOOGLE_CLOUD_PROJECT)." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TF_DIR="${REPO_ROOT}/terraform"

if [[ ! -d "${TF_DIR}/.terraform" ]]; then
  echo "ERROR: ${TF_DIR}/.terraform not found. Run 'terraform init' first." >&2
  exit 1
fi

# Pull the runtime values from terraform outputs.
TF_OUTPUT_JSON=$(terraform -chdir="${TF_DIR}" output -json)

GRAPH_WORKFLOW_NAME=$(echo "${TF_OUTPUT_JSON}" | python3 -c "import sys,json;print(json.load(sys.stdin)['graph_workflow_name']['value'] or '')")
SPANNER_INSTANCE=$(echo "${TF_OUTPUT_JSON}"    | python3 -c "import sys,json;print(json.load(sys.stdin)['spanner_instance_name']['value'] or '')")
SPANNER_DATABASE=$(echo "${TF_OUTPUT_JSON}"    | python3 -c "import sys,json;print(json.load(sys.stdin)['spanner_database_name']['value'] or '')")
STAGING_BUCKET=$(echo "${TF_OUTPUT_JSON}"      | python3 -c "import sys,json;print(json.load(sys.stdin)['graph_staging_bucket_name']['value'] or '')")
PIPELINE_SA=$(echo "${TF_OUTPUT_JSON}"         | python3 -c "import sys,json;print(json.load(sys.stdin)['graph_pipeline_service_account']['value'] or '')")

if [[ -z "${GRAPH_WORKFLOW_NAME}" || -z "${SPANNER_INSTANCE}" || -z "${SPANNER_DATABASE}" ]]; then
  echo "ERROR: graph pipeline outputs are null. Set create_graph_store=true and apply." >&2
  exit 1
fi

# graph_repo_uris and cloudbuild_repo_uri come from terraform.tfvars (not
# outputs); read them with `terraform console` for accuracy.
GRAPH_REPO_URIS=$(terraform -chdir="${TF_DIR}" console <<<'jsonencode(var.graph_repo_uris)' 2>/dev/null | tail -1 | python3 -c "import sys,json;print(json.loads(json.load(sys.stdin)))" 2>/dev/null || echo "[]")
CLOUDBUILD_REPO_URI=$(terraform -chdir="${TF_DIR}" console <<<'var.cloudbuild_repo_uri' 2>/dev/null | tail -1 | tr -d '"' || echo "")
MACHINE_TYPE=$(terraform -chdir="${TF_DIR}" console <<<'var.graph_cloudbuild_machine_type' 2>/dev/null | tail -1 | tr -d '"' || echo "E2_HIGHCPU_8")
BUILD_TIMEOUT_SECONDS=$(terraform -chdir="${TF_DIR}" console <<<'var.graph_build_timeout_seconds' 2>/dev/null | tail -1 || echo "1800")

ARGUMENT_JSON=$(python3 - <<PY
import json
arg = {
    "graph_repo_uris": ${GRAPH_REPO_URIS},
    "cloudbuild_repo_uri": "${CLOUDBUILD_REPO_URI}",
    "graph_staging_bucket": "${STAGING_BUCKET}",
    "spanner_instance": "${SPANNER_INSTANCE}",
    "spanner_database": "${SPANNER_DATABASE}",
    "region": "${REGION}",
    "service_account": "projects/${PROJECT_ID}/serviceAccounts/${PIPELINE_SA}",
    "machine_type": "${MACHINE_TYPE}",
    "build_timeout": "${BUILD_TIMEOUT_SECONDS}s",
}
print(json.dumps(arg))
PY
)

REQUEST_BODY=$(python3 -c "import json,sys;print(json.dumps({'argument': sys.argv[1]}))" "${ARGUMENT_JSON}")

echo "Triggering workflow: ${GRAPH_WORKFLOW_NAME}"
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo "  Repos:   ${GRAPH_REPO_URIS}"
echo ""

ACCESS_TOKEN=$(gcloud auth print-access-token 2>/dev/null)
EXEC_URL="https://workflowexecutions.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/workflows/${GRAPH_WORKFLOW_NAME}/executions"

EXEC_RESPONSE=$(curl -sS -X POST "${EXEC_URL}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "${REQUEST_BODY}")

PY_RC=0
EXECUTION_NAME=$(echo "${EXEC_RESPONSE}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
name = data.get('name', '')
if not name:
    err = data.get('error', {})
    print('ERROR from API: ' + str(err), file=sys.stderr)
    sys.exit(1)
print(name)
" 2>&1) || PY_RC=$?

if [[ ${PY_RC} -ne 0 ]] || [[ -z "${EXECUTION_NAME}" ]] || [[ "${EXECUTION_NAME}" == ERROR* ]]; then
  echo "ERROR: Failed to create workflow execution." >&2
  echo "${EXEC_RESPONSE}" >&2
  exit 1
fi

EXECUTION_ID="${EXECUTION_NAME##*/}"
CONSOLE_URL="https://console.cloud.google.com/workflows/workflow/${REGION}/${GRAPH_WORKFLOW_NAME}/execution/${EXECUTION_ID}?project=${PROJECT_ID}"

echo "Workflow execution started."
echo "  Execution ID : ${EXECUTION_ID}"
echo "  Console URL  : ${CONSOLE_URL}"
echo ""

if [[ "${WAIT}" == "true" ]]; then
  echo "Waiting for execution to complete …"
  STATUS="ACTIVE"
  while [[ "${STATUS}" == "ACTIVE" ]]; do
    sleep 15
    STATUS=$(gcloud workflows executions describe "${EXECUTION_ID}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --workflow="${GRAPH_WORKFLOW_NAME}" \
      --format="value(state)" 2>/dev/null || echo "UNKNOWN")
    echo "  Status: ${STATUS}"
  done

  echo ""
  if [[ "${STATUS}" == "SUCCEEDED" ]]; then
    echo "Graph pipeline completed successfully."
    exit 0
  else
    echo "Graph pipeline finished with status: ${STATUS}" >&2
    echo "View details at: ${CONSOLE_URL}" >&2
    exit 1
  fi
fi
