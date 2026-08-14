"""The small, stable contract shared by raw-result readers.

This module deliberately knows nothing about manifests or CSV files.  It only
answers one question: is a raw benchmark JSON internally coherent enough to be
normalised?

Read this file in this order: constants at the top define the result schema,
``finite_nonnegative`` defines the numeric rule, and ``validate_result`` is the
single public gate used before derived metrics are calculated.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


BASE_FIELDS = [
    "evidence_status",
    "experiment_spec_sha256",
    "run_namespace",
    "plan_sha256",
    "nominal_arrival_horizon_seconds",
    "ttft_slo_ms",
    "e2e_slo_ms",
    "tpot_slo_ms",
    "phase",
    "run_id",
    "input_len",
    "output_len",
    "concurrency",
    "request_rate",
    "loadgen_seed",
    "repetition",
    "completed",
    "failed",
    "num_prompts",
    "duration",
    "request_throughput",
    "request_goodput",
    "input_throughput",
    "output_throughput",
    "total_token_throughput",
    "actual_input_len_min",
    "actual_input_len_max",
    "actual_input_len_mean",
    "input_token_abs_drift_total",
    "input_token_net_drift_total",
    "input_token_abs_drift_pct",
    "arrival_span_s",
    "realized_send_rate",
    "drain_time_approx_s",
    "mean_ttft_ms",
    "p50_ttft_ms",
    "p95_ttft_ms",
    "p99_ttft_ms",
    "mean_tpot_ms",
    "p50_tpot_ms",
    "p95_tpot_ms",
    "p99_tpot_ms",
    "mean_itl_ms",
    "p50_itl_ms",
    "p95_itl_ms",
    "p99_itl_ms",
    "mean_e2el_ms",
    "p50_e2el_ms",
    "p95_e2el_ms",
    "p99_e2el_ms",
]

GAIN_METRICS = (
    "request_throughput",
    "output_throughput",
    "p50_ttft_ms",
    "p95_ttft_ms",
    "p99_ttft_ms",
    "p99_tpot_ms",
    "p99_itl_ms",
    "p99_e2el_ms",
)
GAIN_FIELDS = [
    field
    for metric in GAIN_METRICS
    for field in (f"{metric}_gain_vs_c1_pct", f"{metric}_gain_vs_previous_pct")
]
FIELDS = [*BASE_FIELDS, *GAIN_FIELDS]

REQUIRED_RESULT_FIELDS = (
    "run_id",
    "input_len",
    "output_len",
    "concurrency",
    "request_rate",
    "repetition",
    "completed",
    "failed",
    "duration",
    "request_throughput",
    "output_throughput",
    "mean_ttft_ms",
    "p99_ttft_ms",
    "mean_tpot_ms",
    "p99_tpot_ms",
    "mean_e2el_ms",
    "p99_e2el_ms",
)

PER_REQUEST_FIELDS = (
    "input_lens",
    "output_lens",
    "ttfts",
    "itls",
    "start_times",
    "generated_texts",
    "errors",
)

NONNEGATIVE_RESULT_METRICS = (
    "duration",
    "request_throughput",
    "output_throughput",
    "mean_ttft_ms",
    "p99_ttft_ms",
    "mean_tpot_ms",
    "p99_tpot_ms",
    "mean_e2el_ms",
    "p99_e2el_ms",
)


def finite_nonnegative(value: object, context: str) -> float:
    """Return a finite non-negative number or raise a useful error."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{context} must be finite and non-negative")
    return number


def metadata_from(result: dict[str, Any]) -> dict[str, Any]:
    metadata = result.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def result_value(
    result: dict[str, Any], metadata: dict[str, Any], key: str, default: object = ""
) -> object:
    """Read fields written by either the old or current result schema."""
    return metadata.get(key, result.get(key, default))


def finite_request_rate(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and value.strip().lower() in {
        "inf",
        "+inf",
        "infinity",
        "+infinity",
    }:
        return None
    rate = float(value)
    return rate if math.isfinite(rate) else None


def validate_result(result: dict[str, Any], path: Path) -> None:
    """Validate raw JSON before any derived metric is calculated."""
    metadata = metadata_from(result)
    missing = [
        field
        for field in REQUIRED_RESULT_FIELDS
        if result_value(result, metadata, field, None) in (None, "")
    ]
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")

    completed_value = result_value(result, metadata, "completed")
    failed_value = result_value(result, metadata, "failed")
    if (
        isinstance(completed_value, bool)
        or not isinstance(completed_value, int)
        or isinstance(failed_value, bool)
        or not isinstance(failed_value, int)
    ):
        raise ValueError(f"{path} completed and failed must be integers")
    completed = completed_value
    failed = failed_value
    duration = finite_nonnegative(
        result_value(result, metadata, "duration"), f"{path} duration"
    )
    if completed <= 0:
        raise ValueError(f"{path} has no completed requests")
    if failed != 0:
        raise ValueError(f"{path} contains {failed} failed requests")
    if duration <= 0:
        raise ValueError(f"{path} has invalid duration {duration!r}")

    for field in NONNEGATIVE_RESULT_METRICS:
        finite_nonnegative(
            result_value(result, metadata, field), f"{path} field {field!r}"
        )

    num_prompts = result_value(result, metadata, "num_prompts", None)
    if num_prompts not in (None, "") and int(num_prompts) != completed + failed:
        raise ValueError(
            f"{path} completed+failed does not match num_prompts: "
            f"{completed}+{failed}!={num_prompts}"
        )

    for field in PER_REQUEST_FIELDS:
        values = result.get(field)
        if not isinstance(values, list):
            raise ValueError(f"{path} field {field!r} is not a list")
        if len(values) != completed:
            raise ValueError(
                f"{path} field {field!r} has {len(values)} items; expected {completed}"
            )

    for field in ("input_lens", "output_lens"):
        for index, value in enumerate(result[field]):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(
                    f"{path} field {field!r}[{index}] must be a non-negative integer"
                )
    if any(value <= 0 for value in result["input_lens"]):
        raise ValueError(f"{path} input_lens must be positive")

    for field in ("ttfts", "start_times"):
        for index, value in enumerate(result[field]):
            finite_nonnegative(value, f"{path} field {field!r}[{index}]")
    if any(
        current < previous
        for previous, current in zip(result["start_times"], result["start_times"][1:])
    ):
        raise ValueError(f"{path} start_times must be non-decreasing")

    for request_index, request_itls in enumerate(result["itls"]):
        if not isinstance(request_itls, list):
            raise ValueError(f"{path} field 'itls'[{request_index}] must be a list")
        for itl_index, value in enumerate(request_itls):
            finite_nonnegative(
                value,
                f"{path} field 'itls'[{request_index}][{itl_index}]",
            )

    if not all(isinstance(value, str) for value in result["generated_texts"]):
        raise ValueError(f"{path} generated_texts must contain strings")
    if any(value not in (None, "") for value in result["errors"]):
        raise ValueError(f"{path} reports per-request errors despite failed=0")
