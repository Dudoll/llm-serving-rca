#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

require_command docker

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

output_file="${PROJECT_ROOT}/artifacts/logs/server-runtime-$(timestamp_utc).log"
docker logs --timestamps "${CONTAINER_NAME}" >"${output_file}" 2>&1
echo "Server runtime log saved: ${output_file}"
