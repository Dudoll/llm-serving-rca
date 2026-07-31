#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

require_command docker

if container_exists; then
    docker stop --timeout 30 "${CONTAINER_NAME}"
    for _ in $(seq 1 30); do
        if ! container_exists; then
            echo "Stopped ${CONTAINER_NAME}. The --rm container has been removed."
            exit 0
        fi
        sleep 1
    done

    echo "Timed out waiting for ${CONTAINER_NAME} to be removed." >&2
    exit 1
else
    echo "Container ${CONTAINER_NAME} does not exist."
fi
