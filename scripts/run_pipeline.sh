#!/usr/bin/env bash
# run_pipeline.sh — Trigger the HashiCorp RAG pipeline Cloud Workflow.
#
# Uses the Workflows REST API to create an execution without blocking, then
# optionally polls until completion.
#
# Usage:
#   scripts/run_pipeline.sh [--project-id my-project] [--region us-central1] [--wait] [--data <JSON>]
#
# Project ID is resolved in order: --project-id flag > GOOGLE_CLOUD_PROJECT env var
# > gcloud config get-value project. Region defaults to us-central1 or GOOGLE_CLOUD_REGION.

set -euo pipefail

WORKFLOW_NAME="rag-hashicorp-pipeline"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
REGION="${GOOGLE_CLOUD_REGION:-us-west1}"
WAIT=false
DATA=""

# Auto-detect project ID from gcloud if not set via env var
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi

# ── Argument parsing ───────────────────────────────────────────────────────────

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
    --data)
      DATA="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 --project-id <PROJECT> --region <REGION> [--wait] [--data <JSON>]" >&2
      exit 1
      ;;
  esac
done

# ── Validation ─────────────────────────────────────────────────────────────────

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: --project-id is required (or set GOOGLE_CLOUD_PROJECT)." >&2
  exit 1
fi

if [[ -z "${REGION}" ]]; then
  echo "ERROR: --region is required (or set GOOGLE_CLOUD_REGION)." >&2
  exit 1
fi

# ── Trigger the workflow via REST API (non-blocking) ───────────────────────────

echo "Triggering workflow: ${WORKFLOW_NAME}"
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo ""

ACCESS_TOKEN=$(gcloud auth print-access-token 2>/dev/null)
EXEC_URL="https://workflowexecutions.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/workflows/${WORKFLOW_NAME}/executions"

# The Workflows API expects `argument` as a JSON-encoded string.
# If DATA is provided, encode it; otherwise omit the argument field.
if [[ -n "${DATA}" ]]; then
  ARGUMENT=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "${DATA}")
  REQUEST_BODY="{\"argument\": ${ARGUMENT}}"
else
  REQUEST_BODY="{}"
fi

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

# Extract just the execution ID from the full resource name
EXECUTION_ID="${EXECUTION_NAME##*/}"

CONSOLE_URL="https://console.cloud.google.com/workflows/workflow/${REGION}/${WORKFLOW_NAME}/execution/${EXECUTION_ID}?project=${PROJECT_ID}"

echo "Workflow execution started."
echo "  Execution ID : ${EXECUTION_ID}"
echo "  Console URL  : ${CONSOLE_URL}"
echo ""

# ── Optionally wait for completion ────────────────────────────────────────────

if [[ "${WAIT}" == "true" ]]; then
  echo "Waiting for execution to complete …"
  STATUS="ACTIVE"
  while [[ "${STATUS}" == "ACTIVE" ]]; do
    sleep 15
    STATUS=$(gcloud workflows executions describe "${EXECUTION_ID}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --workflow="${WORKFLOW_NAME}" \
      --format="value(state)" 2>/dev/null || echo "UNKNOWN")
    echo "  Status: ${STATUS}"
  done

  echo ""
  if [[ "${STATUS}" == "SUCCEEDED" ]]; then
    echo "Pipeline completed successfully."
    exit 0
  else
    echo "Pipeline finished with status: ${STATUS}" >&2
    echo "View details at: ${CONSOLE_URL}" >&2
    exit 1
  fi
fi
