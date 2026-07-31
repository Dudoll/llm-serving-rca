#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

require_command docker

if container_exists; then
    docker stop --timeout 30 "${CONTAINER_NAME}"
    echo "Stopped ${CONTAINER_NAME}. The container was created with --rm and is removed after stopping."
else
    echo "Container ${CONTAINER_NAME} does not exist."
fi
