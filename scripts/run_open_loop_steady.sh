#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

STEADY_CONFIG="${PROJECT_ROOT}/configs/open_loop_steady.env"
if [[ ! -f "${STEADY_CONFIG}" ]]; then
    echo "Missing steady open-loop configuration: ${STEADY_CONFIG}" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${STEADY_CONFIG}"

# Explicit overrides keep short validation runs possible without changing the
# checked-in experiment definition.
INPUT_LEN="${INPUT_LEN_OVERRIDE:-${INPUT_LEN}}"
OUTPUT_LEN="${OUTPUT_LEN_OVERRIDE:-${OUTPUT_LEN}}"
RUN_NAMESPACE="${RUN_NAMESPACE_OVERRIDE:-${RUN_NAMESPACE}}"
RUN_ATTEMPT="${RUN_ATTEMPT_OVERRIDE:-${RUN_ATTEMPT}}"
ARRIVAL_WINDOW_SECONDS="${ARRIVAL_WINDOW_SECONDS_OVERRIDE:-${ARRIVAL_WINDOW_SECONDS}}"
NUM_WARMUPS="${NUM_WARMUPS_OVERRIDE:-${NUM_WARMUPS}}"
REPETITIONS="${REPETITIONS_OVERRIDE:-${REPETITIONS}}"
MAX_CONCURRENCY="${MAX_CONCURRENCY_OVERRIDE:-${MAX_CONCURRENCY}}"
REQUEST_RATES="${REQUEST_RATES_OVERRIDE:-${REQUEST_RATES}}"
LOADGEN_SEEDS="${LOADGEN_SEEDS_OVERRIDE:-${LOADGEN_SEEDS}}"
PLAN_SHUFFLE_SEED="${PLAN_SHUFFLE_SEED_OVERRIDE:-${PLAN_SHUFFLE_SEED}}"
TTFT_SLO_MS="${TTFT_SLO_MS_OVERRIDE:-${TTFT_SLO_MS}}"
E2E_SLO_MS="${E2E_SLO_MS_OVERRIDE:-${E2E_SLO_MS}}"
TPOT_SLO_MS="${TPOT_SLO_MS_OVERRIDE:-${TPOT_SLO_MS}}"
RANDOM_RANGE_RATIO="${RANDOM_RANGE_RATIO_OVERRIDE:-${RANDOM_RANGE_RATIO}}"
BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS_OVERRIDE:-${BETWEEN_RUN_SECONDS}}"
ONLY_BLOCK="${ONLY_BLOCK_OVERRIDE:-}"
ONLY_RATE="${ONLY_RATE_OVERRIDE:-}"

if [[ ! "${RUN_NAMESPACE}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "RUN_NAMESPACE must contain only letters, digits, dot, underscore or dash." >&2
    exit 1
fi

# Phase 3b v3 has a different experiment contract: strict wall-clock dispatch,
# window-aligned completion/queue analysis and explicit schedule-lag evidence.
# Never let the legacy num_prompts=rate*duration path create v3-labelled data.
if [[ "${RUN_NAMESPACE}" =~ ^phase3b-v3([._-].*)?$ ]]; then
    echo "Refusing to run ${RUN_NAMESPACE} through the legacy finite Phase 3b runner." >&2
    echo "Implement/use the strict fixed-window v3 runner defined in docs/phase3b-v3.md." >&2
    exit 2
fi

if [[ ! "${RUN_ATTEMPT}" =~ ^[1-9][0-9]*$ ]]; then
    echo "RUN_ATTEMPT must be a positive integer." >&2
    exit 1
fi
if [[ -z "${TTFT_SLO_MS}" || -z "${E2E_SLO_MS}" ]]; then
    echo "Declare TTFT_SLO_MS and E2E_SLO_MS in ${STEADY_CONFIG} before running." >&2
    exit 1
fi

require_command sha256sum

read -r -a request_rates <<<"${REQUEST_RATES}"
read -r -a loadgen_seeds <<<"${LOADGEN_SEEDS}"
if [[ "${#loadgen_seeds[@]}" -ne "${REPETITIONS}" ]]; then
    echo "REPETITIONS=${REPETITIONS}, but ${#loadgen_seeds[@]} LOADGEN_SEEDS were configured." >&2
    exit 1
fi

plan_rows="$(
    python3 "${PROJECT_ROOT}/benchmark/plan_open_loop.py" \
        --rates "${request_rates[@]}" \
        --seeds "${loadgen_seeds[@]}" \
        --arrival-window-seconds "${ARRIVAL_WINDOW_SECONDS}" \
        --shuffle-seed "${PLAN_SHUFFLE_SEED}" \
        --run-namespace "${RUN_NAMESPACE}" \
        --input-len "${INPUT_LEN}" \
        --output-len "${OUTPUT_LEN}" \
        --max-concurrency "${MAX_CONCURRENCY}" \
        --num-warmups "${NUM_WARMUPS}" \
        --random-range-ratio "${RANDOM_RANGE_RATIO}" \
        --ttft-slo-ms "${TTFT_SLO_MS}" \
        --e2e-slo-ms "${E2E_SLO_MS}" \
        --tpot-slo-ms "${TPOT_SLO_MS}" \
        --format tsv
)"

PLAN_RELATIVE_PATH="results/plans/${RUN_NAMESPACE}-open-loop-steady-plan.tsv"
PLAN_FILE="${PROJECT_ROOT}/${PLAN_RELATIVE_PATH}"
mkdir -p "$(dirname "${PLAN_FILE}")"
if [[ -e "${PLAN_FILE}" && ! -f "${PLAN_FILE}" ]]; then
    echo "Plan path exists but is not a regular file: ${PLAN_FILE}" >&2
    exit 1
fi
if [[ -f "${PLAN_FILE}" ]]; then
    existing_plan="$(<"${PLAN_FILE}")"
    if [[ "${existing_plan}" != "${plan_rows}" ]]; then
        echo "Refusing to overwrite a different steady plan: ${PLAN_FILE}" >&2
        echo "Archive the existing plan or restore the matching configuration first." >&2
        exit 1
    fi
else
    printf '%s\n' "${plan_rows}" >"${PLAN_FILE}"
fi
plan_sha256="$(sha256sum "${PLAN_FILE}")"
plan_sha256="${plan_sha256%% *}"

ACCEPTED_ATTEMPTS_RELATIVE_PATH="results/plans/${RUN_NAMESPACE}-accepted-attempts.tsv"
ACCEPTED_ATTEMPTS_FILE="${PROJECT_ROOT}/${ACCEPTED_ATTEMPTS_RELATIVE_PATH}"
write_accepted_attempt_ledger() {
    python3 "${PROJECT_ROOT}/benchmark/accepted_attempts.py" \
        --plan "${PLAN_FILE}" \
        --manifest-dir "${PROJECT_ROOT}/results/manifests" \
        --output "${ACCEPTED_ATTEMPTS_FILE}" \
        --allow-incomplete
}

# Validate and persist the complete ExperimentSpec before touching the runtime.
if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

while IFS=$'\t' read -r \
    block order_in_block repetition loadgen_seed request_rate num_prompts arrival_window_seconds \
    planned_shuffle_seed planned_namespace planned_input_len planned_output_len \
    planned_max_concurrency planned_num_warmups planned_random_range_ratio \
    planned_ttft_slo_ms planned_e2e_slo_ms planned_tpot_slo_ms; do
    if [[ -z "${block}" || "${block}" == "block" ]]; then
        continue
    fi
    if [[ -n "${ONLY_BLOCK}" && "${block}" != "${ONLY_BLOCK}" ]]; then
        continue
    fi
    if [[ -n "${ONLY_RATE}" && "${request_rate}" != "${ONLY_RATE}" ]]; then
        continue
    fi
    if [[ \
        "${planned_shuffle_seed}" != "${PLAN_SHUFFLE_SEED}" \
        || "${planned_namespace}" != "${RUN_NAMESPACE}" \
        || "${planned_input_len}" != "${INPUT_LEN}" \
        || "${planned_output_len}" != "${OUTPUT_LEN}" \
        || "${planned_max_concurrency}" != "${MAX_CONCURRENCY}" \
        || "${planned_num_warmups}" != "${NUM_WARMUPS}" \
        || "${planned_random_range_ratio}" != "${RANDOM_RANGE_RATIO}" \
        || "${planned_ttft_slo_ms}" != "${TTFT_SLO_MS}" \
        || "${planned_e2e_slo_ms}" != "${E2E_SLO_MS}" \
        || "${planned_tpot_slo_ms}" != "${TPOT_SLO_MS}" \
    ]]; then
        echo "Plan row does not match the frozen Phase 3b ExperimentSpec." >&2
        exit 1
    fi
    echo "Steady block ${block}, order ${order_in_block}: rate=${request_rate}, seed=${loadgen_seed}, prompts=${num_prompts}, nominal_window=${arrival_window_seconds}s"
    if RUN_ID="open_loop_steady-${RUN_NAMESPACE}-in${INPUT_LEN}-out${OUTPUT_LEN}-l${request_rate}-s${loadgen_seed}-b${block}-a${RUN_ATTEMPT}" \
    PHASE=open_loop_steady \
    RUN_NAMESPACE="${RUN_NAMESPACE}" \
    RUN_ATTEMPT="${RUN_ATTEMPT}" \
    PLAN_BLOCK="${block}" \
    PLAN_ORDER="${order_in_block}" \
    PLAN_SHUFFLE_SEED="${PLAN_SHUFFLE_SEED}" \
    PLAN_PATH="${PLAN_RELATIVE_PATH}" \
    PLAN_SHA256="${plan_sha256}" \
    ARRIVAL_HORIZON_SECONDS="${arrival_window_seconds}" \
    TTFT_SLO_MS="${TTFT_SLO_MS}" \
    E2E_SLO_MS="${E2E_SLO_MS}" \
    TPOT_SLO_MS="${TPOT_SLO_MS}" \
    INPUT_LEN="${INPUT_LEN}" \
    OUTPUT_LEN="${OUTPUT_LEN}" \
    CONCURRENCY="${MAX_CONCURRENCY}" \
    REPETITION="${repetition}" \
    NUM_PROMPTS="${num_prompts}" \
    NUM_WARMUPS="${NUM_WARMUPS}" \
    REQUEST_RATE="${request_rate}" \
    LOADGEN_SEED="${loadgen_seed}" \
    RANDOM_RANGE_RATIO="${RANDOM_RANGE_RATIO}" \
    BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS}" \
        "${SCRIPT_DIR}/run_one_bench.sh"; then
        :
    else
        run_status=$?
        if ! write_accepted_attempt_ledger; then
            echo "Failed to refresh accepted-attempt ledger after run failure." >&2
        fi
        exit "${run_status}"
    fi
done <"${PLAN_FILE}"

write_accepted_attempt_ledger

echo "Steady open-loop runs completed."
echo "Raw results: ${PROJECT_ROOT}/results/raw"
echo "Telemetry: ${PROJECT_ROOT}/results/telemetry"
echo "Execution plan: ${PLAN_FILE}"
echo "Accepted attempts: ${ACCEPTED_ATTEMPTS_FILE}"
echo "Retry one failed cell with a new immutable ID using, for example:"
echo "RUN_ATTEMPT_OVERRIDE=2 ONLY_BLOCK_OVERRIDE=3 ONLY_RATE_OVERRIDE=13 $0"
