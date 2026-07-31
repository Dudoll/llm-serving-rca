#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_CONFIG="${PROJECT_ROOT}/configs/server.env"

if [[ ! -f "${SERVER_CONFIG}" ]]; then
    echo "Missing server configuration: ${SERVER_CONFIG}" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${SERVER_CONFIG}"

HF_CACHE_DIR="${HOME}/.cache/huggingface"
VLLM_COMPILE_VOLUME="vllm-cache"
LOCAL_API_URL="http://127.0.0.1:${SERVER_PORT}"

require_command() {
    local command_name="$1"
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        echo "Required command is missing: ${command_name}" >&2
        exit 1
    fi
}

curl_local() {
    curl --noproxy '*' "$@"
}

container_exists() {
    docker ps -a --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"
}

container_running() {
    docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"
}

timestamp_utc() {
    date -u '+%Y%m%dT%H%M%SZ'
}

