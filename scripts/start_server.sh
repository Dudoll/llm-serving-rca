#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

require_command docker
require_command curl

mkdir -p \
    "${HF_CACHE_DIR}" \
    "${PROJECT_ROOT}/artifacts/logs" \
    "${PROJECT_ROOT}/results/raw" \
    "${PROJECT_ROOT}/results/telemetry" \
    "${PROJECT_ROOT}/results/summary"

if container_exists; then
    echo "Container ${CONTAINER_NAME} already exists." >&2
    echo "Run ./scripts/stop_server.sh before starting a new experiment server." >&2
    exit 1
fi

if ! docker image inspect "${VLLM_IMAGE}" >/dev/null 2>&1; then
    echo "Pinned image is not present locally; pulling ${VLLM_IMAGE}"
    docker pull "${VLLM_IMAGE}"
fi

prefix_cache_flag="--no-enable-prefix-caching"
if [[ "${ENABLE_PREFIX_CACHING}" == "true" ]]; then
    prefix_cache_flag="--enable-prefix-caching"
fi

chunked_prefill_flag="--no-enable-chunked-prefill"
if [[ "${ENABLE_CHUNKED_PREFILL}" == "true" ]]; then
    chunked_prefill_flag="--enable-chunked-prefill"
fi

run_timestamp="$(timestamp_utc)"
startup_log="${PROJECT_ROOT}/artifacts/logs/server-startup-${run_timestamp}.log"

docker run --detach --rm \
    --name "${CONTAINER_NAME}" \
    --gpus all \
    --ipc=host \
    --publish "${SERVER_PORT}:8000" \
    --volume "${HF_CACHE_DIR}:/root/.cache/huggingface" \
    --volume "${VLLM_COMPILE_VOLUME}:/root/.cache/vllm" \
    --volume "${PROJECT_ROOT}:/workspace" \
    --workdir /workspace \
    --env VLLM_WSL2_ENABLE_PIN_MEMORY=1 \
    --env NO_PROXY=127.0.0.1,localhost \
    "${VLLM_IMAGE}" \
    "${MODEL}" \
    --revision "${MODEL_REVISION}" \
    --max-model-len "${MAX_MODEL_LEN}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --generation-config vllm \
    --seed "${SEED}" \
    "${prefix_cache_flag}" \
    "${chunked_prefill_flag}"

echo "Waiting for vLLM readiness..."
for _ in $(seq 1 150); do
    if curl_local --silent --fail --max-time 2 "${LOCAL_API_URL}/health" >/dev/null 2>&1; then
        docker logs --timestamps "${CONTAINER_NAME}" >"${startup_log}" 2>&1
        echo "vLLM is ready: ${LOCAL_API_URL}"
        echo "Startup log: ${startup_log}"
        curl_local --silent --fail "${LOCAL_API_URL}/version"
        echo
        exit 0
    fi

    if ! container_running; then
        echo "vLLM container exited during startup." >&2
        docker logs "${CONTAINER_NAME}" 2>&1 | tee "${startup_log}" >&2 || true
        exit 1
    fi

    sleep 2
done

echo "Timed out waiting for vLLM readiness." >&2
docker logs "${CONTAINER_NAME}" 2>&1 | tee "${startup_log}" >&2 || true
exit 1
