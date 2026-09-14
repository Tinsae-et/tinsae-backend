#!/usr/bin/env bash
set -euo pipefail

# Runner script for k6 performance testing suite.
# Automatically uses local k6 binary if present; otherwise runs via Docker.

SCENARIO="${1:-scenarios/baseline_catalog.js}"
shift || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Resolve scenario path relative to load-tests/ directory
if [[ ! "$SCENARIO" =~ ^/ ]]; then
  if [[ -f "${SCRIPT_DIR}/${SCENARIO}" ]]; then
    SCENARIO_PATH="${SCRIPT_DIR}/${SCENARIO}"
    DOCKER_SCENARIO_PATH="/load-tests/${SCENARIO}"
  else
    echo "Error: Scenario file ${SCENARIO} not found in ${SCRIPT_DIR}"
    exit 1
  fi
else
  SCENARIO_PATH="$SCENARIO"SCENARIO
  REL_PATH="${SCENARIO#$SCRIPT_DIR/}"
  DOCKER_SCENARIO_PATH="/load-tests/${REL_PATH}"
fi

echo "=========================================================="
echo " Running Performance Test Scenario: $(basename "$SCENARIO_PATH")"
echo " Target: ${BASE_URL:-http://localhost:8000}"
echo "=========================================================="

if command -v k6 &>/dev/null; then
  echo "[k6 runner] Using native host k6 binary..."
  k6 run "$SCENARIO_PATH" "$@"
else
  echo "[k6 runner] Native k6 binary not found. Using Docker (grafana/k6)..."
  docker run -i --rm \
    --network host \
    -e BASE_URL="${BASE_URL:-http://localhost:8000}" \
    -v "${SCRIPT_DIR}:/load-tests" \
    grafana/k6 run "$DOCKER_SCENARIO_PATH" "$@"
fi
