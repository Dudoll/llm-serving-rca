#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

require_command docker
require_command nvidia-smi

output_dir="${PROJECT_ROOT}/artifacts/env"
mkdir -p "${output_dir}"

run_timestamp="$(timestamp_utc)"
output_file="${output_dir}/environment-${run_timestamp}.txt"

{
    echo "experiment_timestamp_utc=${run_timestamp}"
    echo "project_root=${PROJECT_ROOT}"
    echo

    echo "== Git =="
    git -C "${PROJECT_ROOT}" rev-parse HEAD 2>/dev/null || echo "uncommitted"
    git -C "${PROJECT_ROOT}" status --short 2>/dev/null || true
    echo

    echo "== Server configuration =="
    sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "${SERVER_CONFIG}"
    echo

    echo "== Kernel and WSL =="
    uname -a
    cat /proc/version
    echo

    echo "== OS =="
    cat /etc/os-release
    echo

    echo "== CPU =="
    lscpu
    echo

    echo "== Memory =="
    free -h
    cat /proc/meminfo
    echo

    echo "== Filesystem =="
    df -h "${PROJECT_ROOT}" /
    stat -f -c 'filesystem_type=%T' "${PROJECT_ROOT}"
    echo

    echo "== NVIDIA =="
    nvidia-smi
    nvidia-smi --query-gpu=name,uuid,driver_version,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu --format=csv
    echo

    echo "== Docker version =="
    docker version
    echo

    echo "== Docker runtime =="
    docker info | grep -E 'Server Version|Storage Driver|Cgroup Driver|Cgroup Version|Runtimes|Default Runtime|Kernel Version|Operating System|CPUs|Total Memory|HTTP Proxy|HTTPS Proxy|No Proxy' || true
    echo

    echo "== Image identity =="
    docker image inspect "${VLLM_IMAGE}" --format 'id={{.Id}} repo_digests={{json .RepoDigests}} created={{.Created}}' 2>&1 || true
    echo

    echo "== Model snapshot identity =="
    model_cache_key="models--${MODEL//\//--}"
    model_snapshot_host="${HF_CACHE_DIR}/hub/${model_cache_key}/snapshots/${MODEL_REVISION}"
    echo "model_revision=${MODEL_REVISION}"
    echo "model_snapshot_host=${model_snapshot_host}"
    if [[ -d "${model_snapshot_host}" ]]; then
        find "${model_snapshot_host}" -maxdepth 1 -type l -printf '%f -> %l\n' | sort
    else
        echo "snapshot_not_cached"
    fi
    echo

    echo "== WSL user configuration =="
    if [[ -r /mnt/c/Users/Administrator/.wslconfig ]]; then
        cat /mnt/c/Users/Administrator/.wslconfig
    else
        echo "unavailable"
    fi
} >"${output_file}" 2>&1

echo "Environment snapshot saved: ${output_file}"
