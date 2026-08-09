#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib.sh"

PD_CONFIG="${PROJECT_ROOT}/configs/prefill_decode.env"
if [[ ! -f "${PD_CONFIG}" ]]; then
    echo "Missing prefill/decode configuration: ${PD_CONFIG}" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "${PD_CONFIG}"

# Allow explicit environment overrides for smoke and targeted experiments.
NUM_PROMPTS="${NUM_PROMPTS_OVERRIDE:-${NUM_PROMPTS}}"
NUM_WARMUPS="${NUM_WARMUPS_OVERRIDE:-${NUM_WARMUPS}}"
REPETITIONS="${REPETITIONS_OVERRIDE:-${REPETITIONS}}"
CONCURRENCY="${CONCURRENCY_OVERRIDE:-${CONCURRENCIES}}"
BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS_OVERRIDE:-${BETWEEN_RUN_SECONDS}}"

if ! container_running; then
    echo "Container ${CONTAINER_NAME} is not running." >&2
    exit 1
fi

while read -r case_id input_len output_len; do
    [[ -z "${case_id}" || "${case_id}" =~ ^# ]] && continue

    for repetition in $(seq 1 "${REPETITIONS}"); do
        RUN_ID="pd-${case_id}-in${input_len}-out${output_len}-c${CONCURRENCY}-r${repetition}" \
        PHASE=prefill_decode \
        CASE="${case_id}" \
        INPUT_LEN="${input_len}" \
        OUTPUT_LEN="${output_len}" \
        CONCURRENCY="${CONCURRENCY}" \
        REPETITION="${repetition}" \
        NUM_PROMPTS="${NUM_PROMPTS}" \
        NUM_WARMUPS="${NUM_WARMUPS}" \
        REQUEST_RATE="${REQUEST_RATE}" \
        RANDOM_RANGE_RATIO="${RANDOM_RANGE_RATIO}" \
        BETWEEN_RUN_SECONDS="${BETWEEN_RUN_SECONDS}" \
            "${SCRIPT_DIR}/run_one_bench.sh"
    done
done <<'EOF'
A 128 32
B 1536 32
C 128 256
D 1536 256
EOF

echo "Prefill/decode runs completed."
echo "Raw results: ${PROJECT_ROOT}/results/raw"
