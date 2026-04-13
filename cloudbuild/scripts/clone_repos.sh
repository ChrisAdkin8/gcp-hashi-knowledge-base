#!/usr/bin/env bash
# clone_repos.sh — Clone HashiCorp documentation repositories into /workspace/repos/
#
# Clones run in parallel with a configurable concurrency limit.
# A single clone failure does NOT abort the script — it logs a warning and continues.

set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
REPOS_DIR="${WORKSPACE}/repos"
CONCURRENCY="${CONCURRENCY:-5}"

mkdir -p "${REPOS_DIR}"

# ── Repo definitions ───────────────────────────────────────────────────────────

# shellcheck disable=SC2034  # Used via name-reference in run_parallel
declare -A CORE_REPOS=(
  ["web-unified-docs"]="https://github.com/hashicorp/web-unified-docs.git"
  ["terraform-website"]="https://github.com/hashicorp/terraform-website.git"
  ["terraform"]="https://github.com/hashicorp/terraform.git"
)

# shellcheck disable=SC2034  # Used via name-reference in run_parallel
declare -A PROVIDER_REPOS=(
  ["terraform-provider-aws"]="https://github.com/hashicorp/terraform-provider-aws.git"
  ["terraform-provider-azurerm"]="https://github.com/hashicorp/terraform-provider-azurerm.git"
  ["terraform-provider-google"]="https://github.com/hashicorp/terraform-provider-google.git"
  ["terraform-provider-kubernetes"]="https://github.com/hashicorp/terraform-provider-kubernetes.git"
  ["terraform-provider-helm"]="https://github.com/hashicorp/terraform-provider-helm.git"
  ["terraform-provider-docker"]="https://github.com/hashicorp/terraform-provider-docker.git"
  ["terraform-provider-vault"]="https://github.com/hashicorp/terraform-provider-vault.git"
  ["terraform-provider-consul"]="https://github.com/hashicorp/terraform-provider-consul.git"
  ["terraform-provider-nomad"]="https://github.com/hashicorp/terraform-provider-nomad.git"
  ["terraform-provider-random"]="https://github.com/hashicorp/terraform-provider-random.git"
  ["terraform-provider-null"]="https://github.com/hashicorp/terraform-provider-null.git"
  ["terraform-provider-local"]="https://github.com/hashicorp/terraform-provider-local.git"
  ["terraform-provider-tls"]="https://github.com/hashicorp/terraform-provider-tls.git"
  ["terraform-provider-http"]="https://github.com/hashicorp/terraform-provider-http.git"
)

# shellcheck disable=SC2034  # Used via name-reference in run_parallel
declare -A SENTINEL_REPOS=(
  ["terraform-sentinel-policies"]="https://github.com/hashicorp/terraform-sentinel-policies.git"
  ["policy-library-aws-networking-terraform"]="https://github.com/hashicorp/policy-library-aws-networking-terraform.git"
  ["policy-library-azurerm-networking-terraform"]="https://github.com/hashicorp/policy-library-azurerm-networking-terraform.git"
  ["policy-library-gcp-networking-terraform"]="https://github.com/hashicorp/policy-library-gcp-networking-terraform.git"
)

# ── Clone function ─────────────────────────────────────────────────────────────

# Globals updated inside subshells are not visible to the parent shell;
# success/failure counts are accumulated via temp files instead.

SUCCESS_DIR="$(mktemp -d)"
FAILURE_DIR="$(mktemp -d)"

clone_repo() {
  local name="$1"
  local url="$2"
  local dest="${REPOS_DIR}/${name}"

  if [[ -d "${dest}" ]]; then
    echo "[SKIP]    ${name} — already cloned"
    touch "${SUCCESS_DIR}/${name}"
    return
  fi

  echo "[CLONE]   ${name} <- ${url}"
  if git clone --depth 1 --single-branch "${url}" "${dest}" 2>&1; then
    echo "[OK]      ${name}"
    touch "${SUCCESS_DIR}/${name}"
  else
    echo "[WARNING] ${name} — clone failed (repo may be archived or renamed)" >&2
    touch "${FAILURE_DIR}/${name}"
  fi
}

# ── Parallel clone runner ──────────────────────────────────────────────────────

run_parallel() {
  local -n repos_ref="$1"
  local pids=()

  for name in "${!repos_ref[@]}"; do
    # Enforce concurrency limit
    while [[ ${#pids[@]} -ge "${CONCURRENCY}" ]]; do
      local running=()
      for pid in "${pids[@]}"; do
        if kill -0 "${pid}" 2>/dev/null; then
          running+=("${pid}")
        fi
      done
      pids=("${running[@]+"${running[@]}"}")
      [[ ${#pids[@]} -ge "${CONCURRENCY}" ]] && sleep 1
    done

    clone_repo "${name}" "${repos_ref[$name]}" &
    pids+=("$!")
  done

  # Wait for remaining jobs
  for pid in "${pids[@]+"${pids[@]}"}"; do
    wait "${pid}" || true
  done
}

# ── Main ───────────────────────────────────────────────────────────────────────

echo "=== Cloning core repos ==="
run_parallel CORE_REPOS

echo "=== Cloning provider repos ==="
run_parallel PROVIDER_REPOS

echo "=== Cloning Sentinel repos ==="
run_parallel SENTINEL_REPOS

# ── Summary ────────────────────────────────────────────────────────────────────

succeeded=$(find "${SUCCESS_DIR}" -maxdepth 1 -type f | wc -l | tr -d ' ')
failed=$(find "${FAILURE_DIR}" -maxdepth 1 -type f | wc -l | tr -d ' ')
total=$((succeeded + failed))

rm -rf "${SUCCESS_DIR}" "${FAILURE_DIR}"

echo ""
echo "=== Clone summary ==="
echo "  Total attempted : ${total}"
echo "  Succeeded       : ${succeeded}"
echo "  Failed          : ${failed}"

if [[ "${failed}" -gt 0 ]]; then
  echo "  NOTE: Some repos failed to clone. This is expected for archived or renamed repos."
fi
