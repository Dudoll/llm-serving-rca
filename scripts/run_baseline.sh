#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

BASELINE_CONFIG="${PROJECT_ROOT}/configs/baseline.env"
if [[ ! -f "${BASELINE_CONFIG}" ]]; then
    echo "Missing baseline configuration: ${BASELINE_CONFIG}" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${BASELINE_CONFIG}"

# Allow explicit environment overrides for pilot and targeted experiments.
INPUT_LEN="${INPUT_LEN_OVERRIDE:-${INPUT_LEN}}"
OUTPUT_LEN="${OUTPUT_LEN_OVERRIDE:-${OUTPUT_LEN}}"
NUM_PROMPTS="${NUM_PROMPTS_OVERRIDE:-${NUM_PROMPTS}}"
NUM_WARMUPS="${NUM_WARMUPS_OVERRIDE:-${NUM_WARMUPS}}"
REPETITIONS="${REPETITIONS_OVERRIDE:-${REPETITIONS}}"
CONCURRENCIES="${CONCURRENCIES_OVERRIDE:-${CONCURRENCIES}}"
BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS_OVERRIDE:-${BETWEEN_RUN_SECONDS}}"

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

mkdir -p \
    "${PROJECT_ROOT}/results/raw" \
    "${PROJECT_ROOT}/results/telemetry" \
    "${PROJECT_ROOT}/artifacts/logs"

for concurrency in ${CONCURRENCIES}; do
    for repetition in $(seq 1 "${REPETITIONS}"); do
        run_id="baseline-in${INPUT_LEN}-out${OUTPUT_LEN}-c${concurrency}-r${repetition}"
        run_log="${PROJECT_ROOT}/artifacts/logs/${run_id}.log"
        gpu_file="${PROJECT_ROOT}/results/telemetry/${run_id}-gpu.csv"
        metrics_before="${PROJECT_ROOT}/results/telemetry/${run_id}-metrics-before.txt"
        metrics_after="${PROJECT_ROOT}/results/telemetry/${run_id}-metrics-after.txt"
        stats_before="${PROJECT_ROOT}/results/telemetry/${run_id}-docker-before.txt"
        stats_after="${PROJECT_ROOT}/results/telemetry/${run_id}-docker-after.txt"

        echo "Running ${run_id}"

        curl_local --silent --fail "${LOCAL_API_URL}/metrics" >"${metrics_before}"
        docker stats --no-stream "${CONTAINER_NAME}" >"${stats_before}"

        nvidia-smi \
            --query-gpu=timestamp,index,utilization.gpu,utilization.memory,memory.used,memory.free,power.draw,clocks.sm,clocks.mem,temperature.gpu \
            --format=csv \
            --loop=1 \
            >"${gpu_file}" 2>&1 &
        monitor_pid=$!

        stop_monitor() {
            kill "${monitor_pid}" 2>/dev/null || true
            wait "${monitor_pid}" 2>/dev/null || true
        }
        trap stop_monitor EXIT INT TERM

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
            --max-concurrency "${concurrency}" \
            --ignore-eos \
            --temperature 0 \
            --percentile-metrics ttft,tpot,itl,e2el \
            --metric-percentiles 50,95,99 \
            --save-result \
            --save-detailed \
            --result-dir /workspace/results/raw \
            --result-filename "${run_id}.json" \
            --metadata \
                phase=baseline \
                run_id="${run_id}" \
                concurrency="${concurrency}" \
                repetition="${repetition}" \
                input_len="${INPUT_LEN}" \
                output_len="${OUTPUT_LEN}" \
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
    done
done

echo "Baseline runs completed."
echo "Raw results: ${PROJECT_ROOT}/results/raw"
