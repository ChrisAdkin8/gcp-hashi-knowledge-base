#!/usr/bin/env bash
# preflight.sh — Run all preflight checks before deploying.
#
# Validates the local environment so `task up` is unlikely to fail at apply
# time. Uses gcloud and checks for the GCP-specific files.
#
# Usage:
#   scripts/preflight.sh [REGION] [PYTHON] [TF_DIR]
#
# All arguments are optional. Defaults: REGION=us-central1,
# PYTHON=.venv/bin/python3, TF_DIR=terraform.

set -euo pipefail

REGION="${1:-us-central1}"
PYTHON="${2:-.venv/bin/python3}"
TF_DIR="${3:-terraform}"

fail=0
section() { echo ""; echo "── $1 ──"; }

# ── CLI tools ────────────────────────────────────────────────────────────────

section "CLI tools"
for cmd in terraform gcloud python3 pip3 git bash shellcheck jq; do
  if command -v "$cmd" &>/dev/null; then
    echo "OK: $cmd — $("$cmd" --version 2>&1 | head -1)"
  else
    echo "MISSING: $cmd"; fail=1
  fi
done
[ "$fail" -ne 0 ] && { echo "Install missing tools and re-run."; exit 1; }

# Terraform >= 1.5
tf_ver=$(terraform version -json 2>/dev/null | jq -r '.terraform_version // empty')
[ -z "$tf_ver" ] && tf_ver=$(terraform version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')
major=$(echo "$tf_ver" | cut -d. -f1); minor=$(echo "$tf_ver" | cut -d. -f2)
if [ "$major" -ge 2 ] || { [ "$major" -eq 1 ] && [ "$minor" -ge 5 ]; }; then
  echo "OK: Terraform $tf_ver (>= 1.5)"
else
  echo "FAIL: Terraform $tf_ver — need >= 1.5"; exit 1
fi

# Python >= 3.11
py_ver=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null \
  || python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
py_major=$(echo "$py_ver" | cut -d. -f1); py_minor=$(echo "$py_ver" | cut -d. -f2)
if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 11 ]; then
  echo "OK: Python $py_ver (>= 3.11)"
else
  echo "FAIL: Python $py_ver — need >= 3.11"; exit 1
fi

# ── GCP authentication ───────────────────────────────────────────────────────

section "GCP authentication"
acct=$(gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>/dev/null | head -1)
if echo "$acct" | grep -q '@'; then
  echo "OK: gcloud authenticated as $acct"
else
  echo "FAIL: No active gcloud account. Run 'gcloud auth login'."; exit 1
fi

if gcloud auth application-default print-access-token &>/dev/null; then
  echo "OK: Application Default Credentials configured"
else
  echo "FAIL: ADC not set. Run 'gcloud auth application-default login'."; exit 1
fi

PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
if [ -z "$PROJECT_ID" ]; then
  echo "FAIL: No active gcloud project. Run 'gcloud config set project <id>'."; exit 1
fi
if gcloud projects describe "$PROJECT_ID" &>/dev/null; then
  echo "OK: Project $PROJECT_ID accessible"
else
  echo "FAIL: Cannot access project $PROJECT_ID."; exit 1
fi
echo "OK: Region $REGION"

# ── Python packages ──────────────────────────────────────────────────────────

section "Python packages"
pkg_fail=0
for pkg in yaml requests pytest bs4; do
  if "$PYTHON" -c "import $pkg" 2>/dev/null || python3 -c "import $pkg" 2>/dev/null; then
    echo "OK: $pkg"
  else
    echo "MISSING: $pkg"; pkg_fail=1
  fi
done
if "$PYTHON" -c "from vertexai import rag" 2>/dev/null || python3 -c "from vertexai import rag" 2>/dev/null; then
  echo "OK: google-cloud-aiplatform (vertexai.rag)"
else
  echo "MISSING: google-cloud-aiplatform"; pkg_fail=1
fi
[ "$pkg_fail" -ne 0 ] && {
  echo "pip install google-cloud-aiplatform google-cloud-spanner pyyaml requests pytest beautifulsoup4"
  exit 1
}

# ── Repository files ─────────────────────────────────────────────────────────

section "Repository files"
file_fail=0
files=(
  terraform/versions.tf
  terraform/variables.tf
  terraform/main.tf
  terraform/outputs.tf
  terraform/bootstrap/main.tf
  workflows/rag_pipeline.yaml
  workflows/graph_pipeline.yaml
  cloudbuild/cloudbuild.yaml
  cloudbuild/scripts/clone_repos.sh
  cloudbuild/scripts/discover_modules.py
  cloudbuild/scripts/process_docs.py
  cloudbuild/scripts/requirements.txt
  cloudbuild/scripts/ingest_graph.py
  cloudbuild/scripts/requirements_graph.txt
  scripts/test_retrieval.py
  scripts/test_graph.sh
  scripts/run_pipeline.sh
  scripts/run_graph_pipeline.sh
  scripts/deploy.sh
  scripts/bootstrap_state.sh
  scripts/preflight.sh
)
for f in "${files[@]}"; do
  if [ -f "$f" ]; then
    echo "OK: $f"
  else
    echo "MISSING: $f"; file_fail=1
  fi
  if [[ "$f" == *.sh ]] && [ -f "$f" ] && [ ! -x "$f" ]; then echo "WARN: $f not executable"; fi
done
[ "$file_fail" -ne 0 ] && { echo "Repository incomplete."; exit 1; }

# ── Terraform syntax ─────────────────────────────────────────────────────────

section "Terraform"
if terraform -chdir="$TF_DIR" fmt -check -recursive 2>/dev/null; then
  echo "OK: Formatting"
else
  echo "FAIL: Run 'task fmt'"; exit 1
fi
if terraform -chdir="$TF_DIR" init -backend=false -input=false &>/dev/null && \
   terraform -chdir="$TF_DIR" validate &>/dev/null; then
  echo "OK: Validates"
else
  echo "FAIL: Run 'terraform -chdir=$TF_DIR validate'"; exit 1
fi

echo ""
echo "All preflight checks passed."
