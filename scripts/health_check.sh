#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

echo "health=ok"
curl_local --silent --fail "${LOCAL_API_URL}/health" >/dev/null

echo -n "version="
curl_local --silent --fail "${LOCAL_API_URL}/version"
echo

echo "models="
curl_local --silent --fail "${LOCAL_API_URL}/v1/models" | jq .

metric_count="$(curl_local --silent --fail "${LOCAL_API_URL}/metrics" | grep -c '^vllm:' || true)"
echo "vllm_metric_samples=${metric_count}"

