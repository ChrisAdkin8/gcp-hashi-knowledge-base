#!/usr/bin/env bash
# setup_claude_vertex.sh — Configure Claude Code to use Vertex AI as its backend.
#
# Sets up authentication, environment variables, and verifies the connection.
# After running this, Claude Code will route all requests through your GCP
# project's Vertex AI endpoint instead of the Anthropic API directly.
#
# Usage:
#   scripts/setup_claude_vertex.sh --project-id my-project --region us-east5
#   task claude:setup                      # uses auto-detected project + default region
#   task claude:setup CLAUDE_REGION=europe-west1

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────

PROJECT_ID=""
REGION="us-east5"
MODEL="claude-sonnet-4-20250514"
PERSIST=false

# ── Argument parsing ─────────────────────────────────────────────────────────

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Configure Claude Code to use Vertex AI as its backend.

Options:
  --project-id ID    GCP project ID (required)
  --region REGION    Vertex AI region (default: us-east5)
  --model MODEL      Claude model ID (default: claude-sonnet-4-20250514)
  --persist          Append exports to ~/.bashrc for future sessions
  -h, --help         Show this help

Examples:
  $(basename "$0") --project-id my-project
  $(basename "$0") --project-id my-project --region europe-west1 --persist
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --region)     REGION="$2"; shift 2 ;;
    --model)      MODEL="$2"; shift 2 ;;
    --persist)    PERSIST=true; shift ;;
    -h|--help)    usage ;;
    *)            echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: --project-id is required." >&2
  exit 1
fi

# ── Authentication ───────────────────────────────────────────────────────────

echo "=== Step 1: Authenticating with Google Cloud ==="

if gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -1 | grep -q '@'; then
  ACCT=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -1)
  echo "Already authenticated as ${ACCT}"
else
  echo "Running gcloud auth login..."
  gcloud auth login
fi

if gcloud auth application-default print-access-token &>/dev/null; then
  echo "Application Default Credentials already configured."
else
  echo "Running gcloud auth application-default login..."
  gcloud auth application-default login
fi

gcloud config set project "${PROJECT_ID}"
echo "Active project: ${PROJECT_ID}"

# ── Environment variables ────────────────────────────────────────────────────

echo ""
echo "=== Step 2: Setting environment variables ==="

export CLAUDE_CODE_USE_VERTEX=1
export ANTHROPIC_VERTEX_PROJECT_ID="${PROJECT_ID}"
export CLOUD_ML_REGION="${REGION}"
export ANTHROPIC_MODEL="${MODEL}"

echo "  CLAUDE_CODE_USE_VERTEX=1"
echo "  ANTHROPIC_VERTEX_PROJECT_ID=${PROJECT_ID}"
echo "  CLOUD_ML_REGION=${REGION}"
echo "  ANTHROPIC_MODEL=${MODEL}"

# ── Optional persistence ────────────────────────────────────────────────────

if [[ "${PERSIST}" == "true" ]]; then
  echo ""
  echo "=== Step 3: Persisting to ~/.bashrc ==="

  MARKER="# Claude Code Vertex AI configuration"
  if grep -q "${MARKER}" ~/.bashrc 2>/dev/null; then
    echo "Configuration already present in ~/.bashrc — skipping."
  else
    {
      echo ""
      echo "${MARKER}"
      echo "export CLAUDE_CODE_USE_VERTEX=1"
      echo "export ANTHROPIC_VERTEX_PROJECT_ID=\"${PROJECT_ID}\""
      echo "export CLOUD_ML_REGION=\"${REGION}\""
      echo "export ANTHROPIC_MODEL=\"${MODEL}\""
    } >> ~/.bashrc
    echo "Appended to ~/.bashrc. Run 'source ~/.bashrc' or start a new shell."
  fi
fi

# ── Verification ─────────────────────────────────────────────────────────────

echo ""
echo "=== Verification ==="

# Check Vertex AI API is enabled.
if gcloud services list --project="${PROJECT_ID}" --enabled --format="value(config.name)" 2>/dev/null | grep -q "^aiplatform.googleapis.com$"; then
  echo "OK: Vertex AI API enabled on ${PROJECT_ID}"
else
  echo "WARN: Vertex AI API not enabled. Run: gcloud services enable aiplatform.googleapis.com --project=${PROJECT_ID}"
fi

# Check claude CLI is available.
if command -v claude &>/dev/null; then
  echo "OK: claude CLI found at $(command -v claude)"
else
  echo "WARN: claude CLI not found in PATH. Install from https://docs.anthropic.com/en/docs/claude-code"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Claude Code is now configured to use Vertex AI."
echo "Start a session with: claude"
echo ""
echo "To revert to the Anthropic API, unset the variables:"
echo "  unset CLAUDE_CODE_USE_VERTEX ANTHROPIC_VERTEX_PROJECT_ID CLOUD_ML_REGION ANTHROPIC_MODEL"
