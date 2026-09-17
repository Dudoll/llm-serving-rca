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

for concurrency in ${CONCURRENCIES}; do
    for repetition in $(seq 1 "${REPETITIONS}"); do
        RUN_ID="baseline-in${INPUT_LEN}-out${OUTPUT_LEN}-c${concurrency}-r${repetition}" \
        PHASE=baseline \
        INPUT_LEN="${INPUT_LEN}" \
        OUTPUT_LEN="${OUTPUT_LEN}" \
        CONCURRENCY="${concurrency}" \
        REPETITION="${repetition}" \
        NUM_PROMPTS="${NUM_PROMPTS}" \
        NUM_WARMUPS="${NUM_WARMUPS}" \
        REQUEST_RATE="${REQUEST_RATE}" \
        RANDOM_RANGE_RATIO="${RANDOM_RANGE_RATIO}" \
        BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS}" \
            "${SCRIPT_DIR}/run_one_bench.sh"
    done
done

echo "Baseline runs completed."
echo "Raw results: ${PROJECT_ROOT}/results/raw"
