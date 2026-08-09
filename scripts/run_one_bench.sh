#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

require_var() {
    local name="$1"
    if [[ -z "${!name:-}" ]]; then
        echo "Missing required env ${name}" >&2
        exit 1
    fi
}

require_var RUN_ID
require_var PHASE
require_var INPUT_LEN
require_var OUTPUT_LEN
require_var CONCURRENCY
require_var REPETITION
require_var NUM_PROMPTS
require_var NUM_WARMUPS
require_var REQUEST_RATE
require_var RANDOM_RANGE_RATIO
require_var BETWEEN_RUN_SECONDS

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

require_command jq

mkdir -p \
    "${PROJECT_ROOT}/results/raw" \
    "${PROJECT_ROOT}/results/telemetry" \
    "${PROJECT_ROOT}/artifacts/logs"

run_id="${RUN_ID}"
result_file="${PROJECT_ROOT}/results/raw/${run_id}.json"
run_log="${PROJECT_ROOT}/artifacts/logs/${run_id}.log"
gpu_file="${PROJECT_ROOT}/results/telemetry/${run_id}-gpu.csv"
metrics_timeseries="${PROJECT_ROOT}/results/telemetry/${run_id}-metrics.jsonl"
metrics_before="${PROJECT_ROOT}/results/telemetry/${run_id}-metrics-before.txt"
metrics_after="${PROJECT_ROOT}/results/telemetry/${run_id}-metrics-after.txt"
stats_before="${PROJECT_ROOT}/results/telemetry/${run_id}-docker-before.txt"
stats_after="${PROJECT_ROOT}/results/telemetry/${run_id}-docker-after.txt"

if [[ -f "${result_file}" ]]; then
    if jq --exit-status \
        --arg run_id "${run_id}" \
        --arg input_len "${INPUT_LEN}" \
        --arg output_len "${OUTPUT_LEN}" \
        --arg concurrency "${CONCURRENCY}" \
        --arg repetition "${REPETITION}" \
        --argjson expected_prompts "${NUM_PROMPTS}" \
        '(.run_id == $run_id)
         and ((.input_len | tostring) == $input_len)
         and ((.output_len | tostring) == $output_len)
         and ((.concurrency | tostring) == $concurrency)
         and ((.repetition | tostring) == $repetition)
         and (.completed == $expected_prompts)
         and (.failed == 0)' \
        "${result_file}" >/dev/null; then
        echo "Skipping verified completed run: ${run_id}"
        exit 0
    fi

    echo "Refusing to overwrite incomplete or mismatched result: ${result_file}" >&2
    exit 1
fi

for existing_artifact in \
    "${run_log}" \
    "${gpu_file}" \
    "${metrics_timeseries}" \
    "${metrics_before}" \
    "${metrics_after}" \
    "${stats_before}" \
    "${stats_after}"; do
    if [[ -e "${existing_artifact}" ]]; then
        echo "Refusing to overwrite artifact without a verified result: ${existing_artifact}" >&2
        exit 1
    fi
done

echo "Running ${run_id}"

curl_local --silent --fail "${LOCAL_API_URL}/metrics" >"${metrics_before}"
docker stats --no-stream "${CONTAINER_NAME}" >"${stats_before}"

nvidia-smi \
    --query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,memory.free,power.draw,clocks.sm,clocks.mem,temperature.gpu \
    --format=csv \
    --loop=1 \
    >"${gpu_file}" 2>&1 &
monitor_pid=$!

python3 "${PROJECT_ROOT}/benchmark/poll_vllm_metrics.py" \
    --url "${LOCAL_API_URL}/metrics" \
    --output "${metrics_timeseries}" \
    --interval 0.5 &
metrics_monitor_pid=$!

stop_monitor() {
    kill "${monitor_pid}" 2>/dev/null || true
    kill "${metrics_monitor_pid}" 2>/dev/null || true
    wait "${monitor_pid}" 2>/dev/null || true
    wait "${metrics_monitor_pid}" 2>/dev/null || true
}
trap stop_monitor EXIT INT TERM

metadata_args=(
    "phase=${PHASE}"
    "run_id=${run_id}"
    "concurrency=${CONCURRENCY}"
    "repetition=${REPETITION}"
    "input_len=${INPUT_LEN}"
    "output_len=${OUTPUT_LEN}"
)
if [[ -n "${CASE:-}" ]]; then
    metadata_args+=("case=${CASE}")
fi

set +e
docker run --rm \
    --network "container:${CONTAINER_NAME}" \
    --volume "${HF_CACHE_DIR}:/root/.cache/huggingface" \
    --volume "${PROJECT_ROOT}:/workspace" \
    --workdir /workspace \
    --entrypoint vllm \
    --env NO_PROXY=127.0.0.1,localhost \
    "${VLLM_IMAGE}" \
    bench serve \
    --backend vllm \
    --host 127.0.0.1 \
    --port 8000 \
    --endpoint /v1/completions \
    --model "${MODEL}" \
    --tokenizer "${TOKENIZER_SNAPSHOT}" \
    --dataset-name random \
    --input-len "${INPUT_LEN}" \
    --output-len "${OUTPUT_LEN}" \
    --random-range-ratio "${RANDOM_RANGE_RATIO}" \
    --num-prompts "${NUM_PROMPTS}" \
    --num-warmups "${NUM_WARMUPS}" \
    --request-rate "${REQUEST_RATE}" \
    --max-concurrency "${CONCURRENCY}" \
    --ignore-eos \
    --temperature 0 \
    --percentile-metrics ttft,tpot,itl,e2el \
    --metric-percentiles 50,95,99 \
    --save-result \
    --save-detailed \
    --result-dir /workspace/results/raw \
    --result-filename "${run_id}.json" \
    --metadata \
        "${metadata_args[@]}" \
    >"${run_log}" 2>&1
benchmark_status=$?
set -e

stop_monitor
trap - EXIT INT TERM

curl_local --silent --fail "${LOCAL_API_URL}/metrics" >"${metrics_after}"
docker stats --no-stream "${CONTAINER_NAME}" >"${stats_after}"

if [[ "${benchmark_status}" -ne 0 ]]; then
    echo "Benchmark failed: ${run_id}; see ${run_log}" >&2
    tail -n 120 "${run_log}" >&2
    exit "${benchmark_status}"
fi

tail -n 80 "${run_log}"
sleep "${BETWEEN_RUN_SECONDS}"
