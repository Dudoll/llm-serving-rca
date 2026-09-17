#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

OPEN_LOOP_CONFIG="${PROJECT_ROOT}/configs/open_loop.env"
if [[ ! -f "${OPEN_LOOP_CONFIG}" ]]; then
    echo "Missing baseline configuration: ${OPEN_LOOP_CONFIG}" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${OPEN_LOOP_CONFIG}"

INPUT_LEN="${INPUT_LEN_OVERRIDE:-${INPUT_LEN}}"
OUTPUT_LEN="${OUTPUT_LEN_OVERRIDE:-${OUTPUT_LEN}}"
NUM_PROMPTS="${NUM_PROMPTS_OVERRIDE:-${NUM_PROMPTS}}"
NUM_WARMUPS="${NUM_WARMUPS_OVERRIDE:-${NUM_WARMUPS}}"
REPETITIONS="${REPETITIONS_OVERRIDE:-${REPETITIONS}}"
MAX_CONCURRENCIES="${CONCURRENCIES_OVERRIDE:-${MAX_CONCURRENCIES}}"
REQUEST_RATE="${REQUEST_RATE_OVERRIDE:-${REQUEST_RATE}}"
BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS_OVERRIDE:-${BETWEEN_RUN_SECONDS}}"

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

for rq in ${REQUEST_RATE}; do
    for repetition in $(seq 1 "${REPETITIONS}"); do
        RUN_ID="open_loop-in${INPUT_LEN}-out${OUTPUT_LEN}-l${rq}-r${repetition}" \
        PHASE=open_loop \
        INPUT_LEN="${INPUT_LEN}" \
        OUTPUT_LEN="${OUTPUT_LEN}" \
        CONCURRENCY="${MAX_CONCURRENCIES}" \
        REPETITION="${repetition}" \
        NUM_PROMPTS="${NUM_PROMPTS}" \
        NUM_WARMUPS="${NUM_WARMUPS}" \
        REQUEST_RATE="${rq}" \
        RANDOM_RANGE_RATIO="${RANDOM_RANGE_RATIO}" \
        BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS}" \
            "${SCRIPT_DIR}/run_one_bench.sh"
    done
done

echo "Baseline runs completed."
echo "Raw results: ${PROJECT_ROOT}/results/raw"
