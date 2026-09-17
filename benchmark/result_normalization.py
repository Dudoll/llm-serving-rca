"""Convert one validated raw benchmark result into a canonical row."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .result_contract import (
    BASE_FIELDS,
    finite_request_rate,
    metadata_from,
    result_value,
    validate_result,
)


def summarize_file(path: Path) -> dict[str, Any]:
    """Read, validate and normalize exactly one raw JSON file.

    This function does not inspect manifests and does not write files.  Evidence
    admission belongs to ``evidence_admission.py``; keeping that boundary here
    prevents a parser from silently becoming an experiment policy engine.
    """
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    validate_result(result, path)

    metadata = metadata_from(result)
    row = {field: result.get(field, "") for field in BASE_FIELDS}
    if not row["input_throughput"] and result.get("duration"):
        row["input_throughput"] = (
            result.get("total_input_tokens", 0) / result["duration"]
        )

    row["run_id"] = result_value(result, metadata, "run_id", path.stem)
    for key in (
        "run_namespace",
        "plan_sha256",
        "nominal_arrival_horizon_seconds",
        "ttft_slo_ms",
        "e2e_slo_ms",
        "tpot_slo_ms",
        "phase",
        "input_len",
        "output_len",
        "concurrency",
        "request_rate",
        "loadgen_seed",
        "repetition",
        "num_prompts",
    ):
        row[key] = result_value(result, metadata, key, row.get(key, ""))

    input_lens = result["input_lens"]
    configured_input_len = int(row["input_len"])
    input_drifts = [value - configured_input_len for value in input_lens]
    row["actual_input_len_min"] = min(input_lens)
    row["actual_input_len_max"] = max(input_lens)
    row["actual_input_len_mean"] = sum(input_lens) / len(input_lens)
    row["input_token_abs_drift_total"] = sum(abs(value) for value in input_drifts)
    row["input_token_net_drift_total"] = sum(input_drifts)
    row["input_token_abs_drift_pct"] = (
        row["input_token_abs_drift_total"]
        / (configured_input_len * len(input_lens))
        * 100.0
    )

    request_rate = finite_request_rate(row["request_rate"])
    start_times = result["start_times"]
    if request_rate is not None and len(start_times) >= 2:
        arrival_span = float(start_times[-1]) - float(start_times[0])
        if arrival_span <= 0:
            raise ValueError(f"{path} has a non-positive arrival span")
        row["arrival_span_s"] = arrival_span
        row["realized_send_rate"] = (len(start_times) - 1) / arrival_span
        # vLLM duration includes arrival plus final-response drain.  The
        # benchmark clock starts just before the first generated request, so
        # this is deliberately an approximation.
        row["drain_time_approx_s"] = max(
            0.0, float(row["duration"]) - arrival_span
        )
    return row


def summary_values_equal(actual: object, canonical: object) -> bool:
    """Compare CSV text with a canonical value without accepting NaN/inf."""
    if actual in (None, "") or canonical in (None, ""):
        return actual in (None, "") and canonical in (None, "")
    if str(actual) == str(canonical):
        return True
    try:
        actual_number = float(actual)
        canonical_number = float(canonical)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(actual_number)
        and math.isfinite(canonical_number)
        and actual_number == canonical_number
    )
