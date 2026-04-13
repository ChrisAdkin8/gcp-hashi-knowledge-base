#!/usr/bin/env bash
# setup_mcp.sh — Register the HashiCorp RAG MCP server with Claude Code.
#
# Writes the mcpServers entry into .claude/settings.local.json so that
# Claude Code starts the MCP server automatically in this project.
#
# Usage:
#   scripts/setup_mcp.sh \
#     --project-id <GCP_PROJECT_ID> \
#     --corpus-id  <CORPUS_NUMERIC_ID> \
#     [--region    <REGION>]
#
# Arguments:
#   --project-id  GCP project ID (required)
#   --corpus-id   Vertex AI RAG corpus numeric ID (required)
#   --region      GCP region (default: us-west1)
#
# After running this script, restart Claude Code. The hashicorp-rag MCP
# server will start automatically and expose search_hashicorp_docs and
# get_corpus_info as tools.

set -euo pipefail

PROJECT_ID=""
REGION="us-west1"
CORPUS_ID=""

# ── Argument parsing ────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --region)     REGION="$2";     shift 2 ;;
    --corpus-id)  CORPUS_ID="$2";  shift 2 ;;
    *)
      echo "Unknown argument: $1"
      echo "Usage: $0 --project-id <id> --corpus-id <id> [--region <region>]"
      exit 1
      ;;
  esac
done

# ── Validation ──────────────────────────────────────────────────────────────────
if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: --project-id is required."
  exit 1
fi
if [[ -z "$CORPUS_ID" ]]; then
  echo "ERROR: --corpus-id is required."
  exit 1
fi

# ── Resolve paths ───────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${REPO_ROOT}/.venv/bin/python3"
SERVER_PY="${REPO_ROOT}/mcp/server.py"
SETTINGS="${REPO_ROOT}/.claude/settings.local.json"

if [[ ! -f "$VENV_PYTHON" ]]; then
  echo "ERROR: Python venv not found at ${VENV_PYTHON}."
  echo "       Run 'task mcp:install' first."
  exit 1
fi

if [[ ! -f "$SERVER_PY" ]]; then
  echo "ERROR: MCP server not found at ${SERVER_PY}."
  exit 1
fi

# Verify mcp package is installed in the venv.
if ! "$VENV_PYTHON" -c "import mcp" 2>/dev/null; then
  echo "ERROR: 'mcp' package not installed in .venv."
  echo "       Run 'task mcp:install' first."
  exit 1
fi

# ── Write settings.local.json ───────────────────────────────────────────────────
"$VENV_PYTHON" - \
  "$SETTINGS" \
  "$VENV_PYTHON" \
  "$SERVER_PY" \
  "$PROJECT_ID" \
  "$REGION" \
  "$CORPUS_ID" \
  <<'PYEOF'
import json, sys

settings_file, python_path, server_path, project_id, region, corpus_id = sys.argv[1:]

try:
    with open(settings_file) as fh:
        settings = json.load(fh)
except (FileNotFoundError, json.JSONDecodeError):
    settings = {}

settings.setdefault("mcpServers", {})
settings["mcpServers"]["hashicorp-rag"] = {
    "command": python_path,
    "args": [server_path],
    "env": {
        "VERTEX_PROJECT": project_id,
        "VERTEX_REGION": region,
        "VERTEX_CORPUS_ID": corpus_id,
    },
}

with open(settings_file, "w") as fh:
    json.dump(settings, fh, indent=2)
    fh.write("\n")

print(f"Written: {settings_file}")
PYEOF

echo ""
echo "MCP server registered. Restart Claude Code to apply."
echo ""
echo "Configuration:"
echo "  Server:    ${SERVER_PY}"
echo "  Python:    ${VENV_PYTHON}"
echo "  Project:   ${PROJECT_ID}"
echo "  Region:    ${REGION}"
echo "  Corpus ID: ${CORPUS_ID}"
echo ""
echo "To verify, run:"
echo "  task mcp:test CORPUS_ID=${CORPUS_ID}"
