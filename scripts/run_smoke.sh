#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

mkdir -p "${PROJECT_ROOT}/results/raw"
run_timestamp="$(timestamp_utc)"
output_file="${PROJECT_ROOT}/results/raw/smoke-${run_timestamp}.json"

payload="$(jq -n \
    --arg model "${MODEL}" \
    '{model: $model, prompt: "Explain virtual memory in one sentence.", max_tokens: 32, temperature: 0}')"

curl_local --silent --show-error --fail --max-time 90 \
    "${LOCAL_API_URL}/v1/completions" \
    --header 'Content-Type: application/json' \
    --data-binary "${payload}" \
    >"${output_file}"

jq '{id, model, finish_reason: .choices[0].finish_reason, usage}' "${output_file}"
echo "Smoke result saved: ${output_file}"

