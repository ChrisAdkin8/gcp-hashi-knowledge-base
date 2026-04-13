#!/usr/bin/env bash
# test_graph.sh — Smoke-test the Spanner graph store.
#
# Issues a few count queries against the Spanner database via gcloud and
# fails if the graph is empty. Used by `task graph:test` after a refresh.
#
# Usage:
#   scripts/test_graph.sh [--project-id my-project]
#
# Project ID is resolved from --project-id > GOOGLE_CLOUD_PROJECT > gcloud
# config. Spanner instance and database are read from terraform outputs.

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"

if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: $0 [--project-id <PROJECT>]" >&2
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

TF_OUTPUT_JSON=$(terraform -chdir="${TF_DIR}" output -json)
SPANNER_INSTANCE=$(echo "${TF_OUTPUT_JSON}" | python3 -c "import sys,json;print(json.load(sys.stdin)['spanner_instance_name']['value'] or '')")
SPANNER_DATABASE=$(echo "${TF_OUTPUT_JSON}" | python3 -c "import sys,json;print(json.load(sys.stdin)['spanner_database_name']['value'] or '')")

if [[ -z "${SPANNER_INSTANCE}" || -z "${SPANNER_DATABASE}" ]]; then
  echo "ERROR: Spanner outputs are null. Set create_graph_store=true and apply." >&2
  exit 1
fi

echo "Smoke-testing Spanner graph: ${SPANNER_INSTANCE}/${SPANNER_DATABASE}"
echo ""

run_query() {
  local label="$1"
  local sql="$2"
  local result
  result=$(gcloud spanner databases execute-sql "${SPANNER_DATABASE}" \
    --instance="${SPANNER_INSTANCE}" \
    --project="${PROJECT_ID}" \
    --sql="${sql}" \
    --format="value(rows[0][0])" 2>/dev/null || echo "ERROR")

  if [[ "${result}" == "ERROR" || -z "${result}" ]]; then
    echo "  ${label}: query failed" >&2
    return 1
  fi
  echo "  ${label}: ${result}"
  echo "${result}"
}

NODE_COUNT=$(run_query "Resource count" "SELECT COUNT(*) FROM Resource" | tail -1)
EDGE_COUNT=$(run_query "DependsOn count" "SELECT COUNT(*) FROM DependsOn" | tail -1)
REPO_COUNT=$(run_query "Distinct repos"  "SELECT COUNT(DISTINCT repo_uri) FROM Resource" | tail -1)

echo ""

if [[ "${NODE_COUNT}" == "0" ]]; then
  echo "FAIL: Resource table is empty. Run scripts/run_graph_pipeline.sh first." >&2
  exit 1
fi

if [[ "${REPO_COUNT}" == "0" ]]; then
  echo "FAIL: no repos ingested." >&2
  exit 1
fi

echo "PASS: graph contains ${NODE_COUNT} resources, ${EDGE_COUNT} edges across ${REPO_COUNT} repo(s)."
