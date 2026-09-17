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

if [[ -n "${LOADGEN_SEED:-}" && ! "${LOADGEN_SEED}" =~ ^-?[0-9]+$ ]]; then
    echo "LOADGEN_SEED must be an integer when set: ${LOADGEN_SEED}" >&2
    exit 1
fi

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

require_command jq
require_command python3

actual_container_image_ref="$(docker inspect --format '{{.Config.Image}}' "${CONTAINER_NAME}")"
actual_container_image_id="$(docker inspect --format '{{.Image}}' "${CONTAINER_NAME}")"
actual_container_cmd_json="$(docker inspect --format '{{json .Config.Cmd}}' "${CONTAINER_NAME}")"
if [[ "${actual_container_image_ref}" != "${VLLM_IMAGE}" ]]; then
    echo "Running container image does not match configs/server.env." >&2
    echo "expected=${VLLM_IMAGE}" >&2
    echo "actual=${actual_container_image_ref}" >&2
    exit 1
fi

prefix_cache_flag="--no-enable-prefix-caching"
if [[ "${ENABLE_PREFIX_CACHING}" == "true" ]]; then
    prefix_cache_flag="--enable-prefix-caching"
fi
chunked_prefill_flag="--no-enable-chunked-prefill"
if [[ "${ENABLE_CHUNKED_PREFILL}" == "true" ]]; then
    chunked_prefill_flag="--enable-chunked-prefill"
fi
if ! jq -e \
    --arg model "${MODEL}" \
    --arg revision "${MODEL_REVISION}" \
    --arg container_server_port "${CONTAINER_SERVER_PORT}" \
    --arg max_model_len "${MAX_MODEL_LEN}" \
    --arg gpu_memory_utilization "${GPU_MEMORY_UTILIZATION}" \
    --arg server_seed "${SEED}" \
    --arg prefix_flag "${prefix_cache_flag}" \
    --arg chunked_flag "${chunked_prefill_flag}" \
    '
      def after($flag):
        . as $cmd
        | ($cmd | index($flag)) as $index
        | if $index == null then null else $cmd[$index + 1] end;
      .[0] == $model
      and after("--port") == $container_server_port
      and after("--revision") == $revision
      and after("--max-model-len") == $max_model_len
      and after("--gpu-memory-utilization") == $gpu_memory_utilization
      and after("--seed") == $server_seed
      and index($prefix_flag) != null
      and index($chunked_flag) != null
    ' <<<"${actual_container_cmd_json}" >/dev/null; then
    echo "Running container command does not match the declared server config." >&2
    echo "actual_cmd=${actual_container_cmd_json}" >&2
    exit 1
fi

served_models_json="$(curl_local --silent --fail "${LOCAL_API_URL}/v1/models")"
if ! jq -e --arg model "${MODEL}" '.data | map(.id) | index($model) != null' \
    <<<"${served_models_json}" >/dev/null; then
    echo "Configured model ${MODEL} is not served by ${CONTAINER_NAME}." >&2
    exit 1
fi
actual_vllm_version_json="$(curl_local --silent --fail "${LOCAL_API_URL}/version" | jq -c .)"

mkdir -p \
    "${PROJECT_ROOT}/results/raw" \
    "${PROJECT_ROOT}/results/telemetry" \
    "${PROJECT_ROOT}/results/manifests" \
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
manifest_file="${PROJECT_ROOT}/results/manifests/${run_id}.json"
manifest_tool="${PROJECT_ROOT}/benchmark/evidence_manifest.py"
validator="${PROJECT_ROOT}/benchmark/validate_evidence.py"

# Existing legacy results may not record the vLLM CLI default, so do not invent
# a seed while validating them. Every newly created run, however, passes and
# records an explicit effective seed even when the caller omitted one.
# When re-entering an existing modern run (skip / revalidate), reuse the seed
# already persisted in the manifest or raw metadata so expected matches recorded.
if [[ -z "${LOADGEN_SEED:-}" ]]; then
    if [[ ! -f "${result_file}" ]]; then
        LOADGEN_SEED=0
        echo "LOADGEN_SEED was not set; explicitly using and recording seed 0."
    else
        recorded_seed=""
        if [[ -f "${manifest_file}" ]]; then
            recorded_seed="$(jq -r '.workload.loadgen_seed // empty' "${manifest_file}")"
        fi
        if [[ -z "${recorded_seed}" || "${recorded_seed}" == "null" ]]; then
            recorded_seed="$(jq -r '.loadgen_seed // empty' "${result_file}")"
        fi
        if [[ -n "${recorded_seed}" && "${recorded_seed}" != "null" ]]; then
            if [[ ! "${recorded_seed}" =~ ^-?[0-9]+$ ]]; then
                echo "Recorded loadgen_seed is not an integer: ${recorded_seed}" >&2
                exit 1
            fi
            LOADGEN_SEED="${recorded_seed}"
            echo "LOADGEN_SEED was not set; reusing recorded seed ${LOADGEN_SEED}."
        fi
    fi
fi

artifact_args=(
    --artifact "raw_result=${result_file}"
    --artifact "benchmark_log=${run_log}"
    --artifact "gpu_telemetry=${gpu_file}"
    --artifact "metrics_timeseries=${metrics_timeseries}"
    --artifact "metrics_before=${metrics_before}"
    --artifact "metrics_after=${metrics_after}"
    --artifact "docker_before=${stats_before}"
    --artifact "docker_after=${stats_after}"
)

manifest_create_args=(
    create
    --project-root "${PROJECT_ROOT}"
    --manifest "${manifest_file}"
    --run-id "${run_id}"
    --phase "${PHASE}"
    --input-len "${INPUT_LEN}"
    --output-len "${OUTPUT_LEN}"
    --concurrency "${CONCURRENCY}"
    --repetition "${REPETITION}"
    --num-prompts "${NUM_PROMPTS}"
    --num-warmups "${NUM_WARMUPS}"
    --request-rate "${REQUEST_RATE}"
    --random-range-ratio "${RANDOM_RANGE_RATIO}"
    --image "${VLLM_IMAGE}"
    --model "${MODEL}"
    --model-revision "${MODEL_REVISION}"
    --tokenizer-snapshot "${TOKENIZER_SNAPSHOT}"
    --server-setting "container_name=${CONTAINER_NAME}"
    --server-setting "host_server_port=${SERVER_PORT}"
    --server-setting "container_server_port=${CONTAINER_SERVER_PORT}"
    --server-setting "max_model_len=${MAX_MODEL_LEN}"
    --server-setting "gpu_memory_utilization=${GPU_MEMORY_UTILIZATION}"
    --server-setting "server_seed=${SEED}"
    --server-setting "enable_prefix_caching=${ENABLE_PREFIX_CACHING}"
    --server-setting "enable_chunked_prefill=${ENABLE_CHUNKED_PREFILL}"
    --server-setting "actual_container_image_ref=${actual_container_image_ref}"
    --server-setting "actual_container_image_id=${actual_container_image_id}"
    --server-setting "actual_container_cmd_json=${actual_container_cmd_json}"
    --server-setting "actual_vllm_version_json=${actual_vllm_version_json}"
    --workload-setting "case=${CASE:-}"
    --workload-setting "between_run_seconds=${BETWEEN_RUN_SECONDS}"
    --workload-setting "run_namespace=${RUN_NAMESPACE:-}"
    --workload-setting "run_attempt=${RUN_ATTEMPT:-}"
    --workload-setting "plan_block=${PLAN_BLOCK:-}"
    --workload-setting "plan_order=${PLAN_ORDER:-}"
    --workload-setting "plan_shuffle_seed=${PLAN_SHUFFLE_SEED:-}"
    --workload-setting "plan_path=${PLAN_PATH:-}"
    --workload-setting "plan_sha256=${PLAN_SHA256:-}"
    --workload-setting "nominal_arrival_horizon_seconds=${ARRIVAL_HORIZON_SECONDS:-}"
    --workload-setting "ttft_slo_ms=${TTFT_SLO_MS:-}"
    --workload-setting "e2e_slo_ms=${E2E_SLO_MS:-}"
    --workload-setting "tpot_slo_ms=${TPOT_SLO_MS:-}"
    "${artifact_args[@]}"
)

validation_args=(
    --project-root "${PROJECT_ROOT}"
    --run-id "${run_id}"
    --phase "${PHASE}"
    --input-len "${INPUT_LEN}"
    --output-len "${OUTPUT_LEN}"
    --concurrency "${CONCURRENCY}"
    --repetition "${REPETITION}"
    --num-prompts "${NUM_PROMPTS}"
    --request-rate "${REQUEST_RATE}"
    --random-range-ratio "${RANDOM_RANGE_RATIO}"
    --image "${VLLM_IMAGE}"
    --model "${MODEL}"
    --model-revision "${MODEL_REVISION}"
)

seed_args=()
if [[ -n "${LOADGEN_SEED:-}" ]]; then
    manifest_create_args+=(--loadgen-seed "${LOADGEN_SEED}")
    validation_args+=(--loadgen-seed "${LOADGEN_SEED}")
    seed_args=(--seed "${LOADGEN_SEED}")
fi

if [[ -f "${result_file}" ]]; then
    if [[ -f "${manifest_file}" ]]; then
        python3 "${validator}" \
            "${validation_args[@]}" \
            --manifest "${manifest_file}" \
            "${artifact_args[@]}" \
            --mark-complete
    else
        # Validate legacy evidence before adding a backfilled provenance record.
        python3 "${validator}" "${validation_args[@]}" "${artifact_args[@]}"
        python3 "${manifest_tool}" \
            "${manifest_create_args[@]}" \
            --provenance-capture backfilled
        python3 "${validator}" \
            "${validation_args[@]}" \
            --manifest "${manifest_file}" \
            "${artifact_args[@]}" \
            --mark-complete
    fi
    echo "Skipping verified completed run: ${run_id}"
    exit 0
fi

if [[ -e "${manifest_file}" ]]; then
    echo "Refusing to overwrite manifest without a verified result: ${manifest_file}" >&2
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

python3 "${manifest_tool}" "${manifest_create_args[@]}"
manifest_initialized=1
run_finished=0
collectors_started=0
benchmark_status=125
gpu_monitor_status=125
metrics_monitor_status=125
gpu_stop_requested_by_runner=0
metrics_stop_requested_by_runner=0
failure_reason="runner exited before evidence completion"

stop_collectors() {
    if [[ "${collectors_started}" -eq 0 ]]; then
        return
    fi

    if kill "${monitor_pid}" 2>/dev/null; then
        gpu_stop_requested_by_runner=1
    fi
    if kill "${metrics_monitor_pid}" 2>/dev/null; then
        metrics_stop_requested_by_runner=1
    fi
    if wait "${monitor_pid}" 2>/dev/null; then
        gpu_monitor_status=0
    else
        gpu_monitor_status=$?
    fi
    if wait "${metrics_monitor_pid}" 2>/dev/null; then
        metrics_monitor_status=0
    else
        metrics_monitor_status=$?
    fi
    collectors_started=0

    echo "Collector exit codes: gpu=${gpu_monitor_status}, metrics=${metrics_monitor_status}, benchmark=${benchmark_status}"
    record_exit_args=()
    if [[ "${gpu_stop_requested_by_runner}" -eq 1 ]]; then
        record_exit_args+=(--gpu-stop-requested-by-runner)
    fi
    if [[ "${metrics_stop_requested_by_runner}" -eq 1 ]]; then
        record_exit_args+=(--metrics-stop-requested-by-runner)
    fi
    python3 "${manifest_tool}" record-exits \
        --project-root "${PROJECT_ROOT}" \
        --manifest "${manifest_file}" \
        --gpu-exit-code "${gpu_monitor_status}" \
        --metrics-exit-code "${metrics_monitor_status}" \
        --benchmark-exit-code "${benchmark_status}" \
        "${record_exit_args[@]}"
}

on_exit() {
    local runner_status=$?
    trap - EXIT
    set +e
    stop_collectors
    if [[ "${manifest_initialized}" -eq 1 && "${run_finished}" -eq 0 ]]; then
        if [[ "${runner_status}" -eq 0 ]]; then
            runner_status=1
        fi
        python3 "${manifest_tool}" mark-failed \
            --project-root "${PROJECT_ROOT}" \
            --manifest "${manifest_file}" \
            --exit-code "${runner_status}" \
            --reason "${failure_reason}"
    fi
    exit "${runner_status}"
}
trap on_exit EXIT

python3 "${manifest_tool}" set-status \
    --project-root "${PROJECT_ROOT}" \
    --manifest "${manifest_file}" \
    --status running \
    --reason "benchmark process starting"

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
collectors_started=1

metadata_args=(
    "phase=${PHASE}"
    "run_id=${run_id}"
    "concurrency=${CONCURRENCY}"
    "repetition=${REPETITION}"
    "input_len=${INPUT_LEN}"
    "output_len=${OUTPUT_LEN}"
)
if [[ -n "${LOADGEN_SEED:-}" ]]; then
    metadata_args+=("loadgen_seed=${LOADGEN_SEED}")
fi
if [[ -n "${CASE:-}" ]]; then
    metadata_args+=("case=${CASE}")
fi
if [[ -n "${RUN_NAMESPACE:-}" ]]; then
    metadata_args+=(
        "run_namespace=${RUN_NAMESPACE}"
        "run_attempt=${RUN_ATTEMPT:-}"
        "plan_block=${PLAN_BLOCK:-}"
        "plan_order=${PLAN_ORDER:-}"
        "plan_shuffle_seed=${PLAN_SHUFFLE_SEED:-}"
        "plan_sha256=${PLAN_SHA256:-}"
        "nominal_arrival_horizon_seconds=${ARRIVAL_HORIZON_SECONDS:-}"
        "ttft_slo_ms=${TTFT_SLO_MS:-}"
        "e2e_slo_ms=${E2E_SLO_MS:-}"
        "tpot_slo_ms=${TPOT_SLO_MS:-}"
    )
fi

set +e
failure_reason="benchmark process failed"
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
    --port "${CONTAINER_SERVER_PORT}" \
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
    "${seed_args[@]}" \
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

failure_reason="collector shutdown or post-run capture failed"
stop_collectors

curl_local --silent --fail "${LOCAL_API_URL}/metrics" >"${metrics_after}"
docker stats --no-stream "${CONTAINER_NAME}" >"${stats_after}"

if [[ "${benchmark_status}" -ne 0 ]]; then
    failure_reason="benchmark process failed"
    echo "Benchmark failed: ${run_id}; see ${run_log}" >&2
    tail -n 120 "${run_log}" >&2
    exit "${benchmark_status}"
fi

if [[ "${gpu_monitor_status}" != 0 && "${gpu_monitor_status}" != 130 && "${gpu_monitor_status}" != 143 ]]; then
    echo "GPU collector failed with exit code ${gpu_monitor_status}" >&2
    exit 1
fi
if [[ "${metrics_monitor_status}" -ne 0 ]]; then
    echo "Metrics collector failed with exit code ${metrics_monitor_status}" >&2
    exit 1
fi

failure_reason="evidence validation failed"
python3 "${validator}" \
    "${validation_args[@]}" \
    --manifest "${manifest_file}" \
    "${artifact_args[@]}" \
    --mark-complete
run_finished=1

tail -n 80 "${run_log}"
sleep "${BETWEEN_RUN_SECONDS}"
