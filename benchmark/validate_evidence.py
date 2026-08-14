#!/usr/bin/env python3

"""Validate a benchmark evidence bundle before it is considered complete.

The validator is deliberately fail-closed. It is easiest to read from the
bottom up: ``validate_evidence_bundle`` is the public orchestration function;
the helpers above it validate one layer at a time (raw result, metrics stream,
GPU CSV, artifact hashes, manifest/workload binding, then collector exits).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

try:  # Support both direct execution and import as benchmark.validate_evidence.
    from benchmark.evidence_manifest import (
        EXPECTED_ARTIFACT_NAMES,
        ManifestError,
        atomic_write_json,
        load_manifest,
        mark_complete,
        sha256_file,
        validate_artifact_path,
        validate_manifest_path,
    )
except ModuleNotFoundError:  # pragma: no cover - used by direct script execution.
    from evidence_manifest import (  # type: ignore[no-redef]
        EXPECTED_ARTIFACT_NAMES,
        ManifestError,
        atomic_write_json,
        load_manifest,
        mark_complete,
        sha256_file,
        validate_artifact_path,
        validate_manifest_path,
    )


REQUIRED_RAW_FIELDS = {
    "backend",
    "model_id",
    "completed",
    "failed",
    "duration",
    "request_throughput",
    "output_throughput",
    "total_input_tokens",
    "total_output_tokens",
    "num_prompts",
    "max_concurrency",
    "run_id",
    "phase",
    "input_len",
    "output_len",
    "concurrency",
    "repetition",
    "request_rate",
    "input_lens",
    "output_lens",
    "ttfts",
    "itls",
    "start_times",
    "generated_texts",
    "errors",
}
DETAILED_ARRAY_FIELDS = (
    "input_lens",
    "output_lens",
    "ttfts",
    "itls",
    "start_times",
    "generated_texts",
    "errors",
)
NONNEGATIVE_METRIC_FIELDS = (
    "duration",
    "request_throughput",
    "output_throughput",
    "total_input_tokens",
    "total_output_tokens",
)


class EvidenceValidationError(ValueError):
    """Raised when persisted evidence is incomplete, corrupt, or mismatched."""


MONOTONIC_COVERAGE_TOLERANCE_S = 2.0
METRICS_MAX_SAMPLE_GAP_S = 5.0
REQUIRED_METRIC_BASE_NAMES = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:num_requests_waiting_by_reason",
    "vllm:kv_cache_usage_perc",
    "vllm:num_preemptions_total",
    "vllm:request_queue_time_seconds_sum",
}
REQUIRED_GPU_COLUMNS = {
    "timestamp",
    "index",
    "utilization.gpu [%]",
    "utilization.memory [%]",
    "memory.used [MiB]",
    "memory.free [MiB]",
    "power.draw [W]",
    "clocks.current.sm [MHz]",
    "clocks.current.memory [MHz]",
    "temperature.gpu",
}
GPU_COLUMNS_ALLOWING_NA = {"power.draw [W]"}

# vLLM's RandomDataset targets a token length, then decodes and re-encodes the
# generated token IDs. Tokenizer non-bijectivity can leave a tiny residual
# mismatch after its bounded retry loop. Keep that known generator behavior
# bounded at both the individual-request and whole-run levels.
FIXED_INPUT_MAX_REQUEST_DRIFT_RATIO = 0.01
FIXED_INPUT_MAX_TOTAL_ABS_DRIFT_RATIO = 0.0001


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _values_equal(actual: object, expected: object) -> bool:
    if str(actual).lower() == str(expected).lower():
        return True
    actual_number = _as_decimal(actual)
    expected_number = _as_decimal(expected)
    return (
        actual_number is not None
        and expected_number is not None
        and actual_number == expected_number
    )


def _require_equal(payload: Mapping[str, Any], key: str, expected: object) -> None:
    if key not in payload:
        raise EvidenceValidationError(f"raw result is missing expected field {key!r}")
    if not _values_equal(payload[key], expected):
        raise EvidenceValidationError(
            f"raw field {key!r} mismatch: expected {expected!r}, got {payload[key]!r}"
        )


def _finite_nonnegative(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceValidationError(f"{context} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise EvidenceValidationError(f"{context} must be finite and non-negative")
    return number


def _validate_detailed_values(
    payload: Mapping[str, Any], expected: Mapping[str, object]
) -> None:
    for field in ("input_lens", "output_lens"):
        for index, value in enumerate(payload[field]):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvidenceValidationError(
                    f"raw field {field!r}[{index}] must be a non-negative integer"
                )
    if any(value <= 0 for value in payload["input_lens"]):
        raise EvidenceValidationError("raw input_lens must be positive")
    if payload["total_input_tokens"] != sum(payload["input_lens"]):
        raise EvidenceValidationError(
            "raw total_input_tokens does not equal sum(input_lens)"
        )
    if payload["total_output_tokens"] != sum(payload["output_lens"]):
        raise EvidenceValidationError(
            "raw total_output_tokens does not equal sum(output_lens)"
        )
    if _values_equal(expected.get("random_range_ratio"), 0):
        expected_input_len = int(expected["input_len"])
        expected_output_len = int(expected["output_len"])
        input_drifts = [
            value - expected_input_len for value in payload["input_lens"]
        ]
        max_request_drift = max(abs(drift) for drift in input_drifts)
        total_abs_drift = sum(abs(drift) for drift in input_drifts)
        max_request_drift_allowed = max(
            1, math.floor(expected_input_len * FIXED_INPUT_MAX_REQUEST_DRIFT_RATIO)
        )
        total_abs_drift_allowed = max(
            1,
            math.floor(
                expected_input_len
                * len(input_drifts)
                * FIXED_INPUT_MAX_TOTAL_ABS_DRIFT_RATIO
            ),
        )
        if max_request_drift > max_request_drift_allowed:
            raise EvidenceValidationError(
                "raw input_lens exceed bounded fixed-workload per-request drift: "
                f"target={expected_input_len}, observed_max_abs_drift="
                f"{max_request_drift}, allowed={max_request_drift_allowed}"
            )
        if total_abs_drift > total_abs_drift_allowed:
            raise EvidenceValidationError(
                "raw input_lens exceed bounded fixed-workload aggregate drift: "
                f"target={expected_input_len}, observed_total_abs_drift="
                f"{total_abs_drift}, allowed={total_abs_drift_allowed}"
            )
        if any(value != expected_output_len for value in payload["output_lens"]):
            raise EvidenceValidationError(
                "raw output_lens do not match fixed-length workload "
                f"output_len={expected_output_len}"
            )

    for field in ("ttfts", "start_times"):
        for index, value in enumerate(payload[field]):
            _finite_nonnegative(value, f"raw field {field!r}[{index}]")
    if any(
        current < previous
        for previous, current in zip(payload["start_times"], payload["start_times"][1:])
    ):
        raise EvidenceValidationError("raw start_times must be non-decreasing")

    for request_index, request_itls in enumerate(payload["itls"]):
        if not isinstance(request_itls, list):
            raise EvidenceValidationError(
                f"raw field 'itls'[{request_index}] must be an array"
            )
        for itl_index, value in enumerate(request_itls):
            _finite_nonnegative(
                value,
                f"raw field 'itls'[{request_index}][{itl_index}]",
            )

    if not all(isinstance(value, str) for value in payload["generated_texts"]):
        raise EvidenceValidationError("raw generated_texts must contain strings")
    if any(value not in (None, "") for value in payload["errors"]):
        raise EvidenceValidationError(
            "raw result contains per-request errors despite failed=0"
        )


def validate_raw_result(path: Path, expected: Mapping[str, object]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise EvidenceValidationError(
            f"invalid raw result JSON {path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise EvidenceValidationError(f"raw result root must be an object: {path}")

    missing = REQUIRED_RAW_FIELDS - set(payload)
    if missing:
        raise EvidenceValidationError(f"raw result missing fields: {sorted(missing)}")

    for key in (
        "run_id",
        "phase",
        "input_len",
        "output_len",
        "concurrency",
        "repetition",
    ):
        _require_equal(payload, key, expected[key])
    _require_equal(payload, "num_prompts", expected["num_prompts"])
    _require_equal(payload, "max_concurrency", expected["concurrency"])
    _require_equal(payload, "request_rate", expected["request_rate"])
    if expected.get("loadgen_seed") is not None:
        _require_equal(payload, "loadgen_seed", expected["loadgen_seed"])
    if expected.get("model") is not None:
        _require_equal(payload, "model_id", expected["model"])

    completed = payload["completed"]
    failed = payload["failed"]
    if not isinstance(completed, int) or isinstance(completed, bool):
        raise EvidenceValidationError("raw field 'completed' must be an integer")
    if not isinstance(failed, int) or isinstance(failed, bool):
        raise EvidenceValidationError("raw field 'failed' must be an integer")
    if completed != int(expected["num_prompts"]):
        raise EvidenceValidationError(
            f"completed request count mismatch: expected {expected['num_prompts']}, got {completed}"
        )
    if failed != 0:
        raise EvidenceValidationError(f"raw result reports {failed} failed requests")

    for key in DETAILED_ARRAY_FIELDS:
        value = payload[key]
        if not isinstance(value, list):
            raise EvidenceValidationError(f"raw field {key!r} must be an array")
        if len(value) != completed:
            raise EvidenceValidationError(
                f"raw array {key!r} length mismatch: expected {completed}, got {len(value)}"
            )
    _validate_detailed_values(payload, expected)

    for key in NONNEGATIVE_METRIC_FIELDS:
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EvidenceValidationError(f"raw metric {key!r} must be numeric")
        if not math.isfinite(value) or value < 0:
            raise EvidenceValidationError(
                f"raw metric {key!r} must be finite and non-negative"
            )
    if payload["duration"] <= 0:
        raise EvidenceValidationError("raw metric 'duration' must be positive")
    return payload


def validate_metrics_jsonl(path: Path) -> tuple[int, float, float]:
    sample_count = 0
    first_monotonic: float | None = None
    last_monotonic: float | None = None
    with path.open("r", encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                raise EvidenceValidationError(
                    f"blank metrics JSONL record at {path}:{line_number}"
                )
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise EvidenceValidationError(
                    f"invalid metrics JSONL at {path}:{line_number}: {error}"
                ) from error
            if not isinstance(record, dict):
                raise EvidenceValidationError(
                    f"metrics record must be an object at {path}:{line_number}"
                )
            if record.get("error"):
                raise EvidenceValidationError(
                    f"metrics polling error at {path}:{line_number}: {record['error']}"
                )
            metrics = record.get("metrics")
            if not isinstance(metrics, dict):
                raise EvidenceValidationError(
                    f"metrics record lacks a metrics object at {path}:{line_number}"
                )
            base_names = {
                name.split("{", 1)[0] for name in metrics if isinstance(name, str)
            }
            missing_metrics = REQUIRED_METRIC_BASE_NAMES - base_names
            if missing_metrics:
                raise EvidenceValidationError(
                    "metrics record is missing required metric families at "
                    f"{path}:{line_number}: {sorted(missing_metrics)}"
                )
            waiting_reason_names = [
                name
                for name in metrics
                if isinstance(name, str)
                and name.split("{", 1)[0] == "vllm:num_requests_waiting_by_reason"
            ]
            for reason in ("capacity", "deferred"):
                if not any(
                    f'reason="{reason}"' in name for name in waiting_reason_names
                ):
                    raise EvidenceValidationError(
                        "metrics record is missing required waiting reason "
                        f"{reason!r} at {path}:{line_number}"
                    )
            for metric_name, metric_value in metrics.items():
                if not isinstance(metric_name, str):
                    raise EvidenceValidationError(
                        f"metrics record has a non-string key at {path}:{line_number}"
                    )
                if metric_name.split("{", 1)[0] in REQUIRED_METRIC_BASE_NAMES:
                    _finite_nonnegative(
                        metric_value,
                        f"metrics value {metric_name!r} at {path}:{line_number}",
                    )
            if "timestamp_utc" not in record or "monotonic_s" not in record:
                raise EvidenceValidationError(
                    f"metrics record lacks timestamps at {path}:{line_number}"
                )
            try:
                timestamp = datetime.fromisoformat(
                    str(record["timestamp_utc"]).replace("Z", "+00:00")
                )
            except ValueError as error:
                raise EvidenceValidationError(
                    f"metrics timestamp_utc is invalid at {path}:{line_number}"
                ) from error
            if timestamp.tzinfo is None:
                raise EvidenceValidationError(
                    f"metrics timestamp_utc lacks a timezone at {path}:{line_number}"
                )
            monotonic_s = _finite_nonnegative(
                record["monotonic_s"], f"metrics monotonic_s at {path}:{line_number}"
            )
            if last_monotonic is not None and monotonic_s <= last_monotonic:
                raise EvidenceValidationError(
                    f"metrics monotonic_s is not strictly increasing at "
                    f"{path}:{line_number}"
                )
            if (
                last_monotonic is not None
                and monotonic_s - last_monotonic > METRICS_MAX_SAMPLE_GAP_S
            ):
                raise EvidenceValidationError(
                    f"metrics sampling gap exceeds {METRICS_MAX_SAMPLE_GAP_S:.1f}s "
                    f"at {path}:{line_number}"
                )
            if first_monotonic is None:
                first_monotonic = monotonic_s
            last_monotonic = monotonic_s
            sample_count += 1
    if sample_count < 2:
        raise EvidenceValidationError(
            f"metrics JSONL needs at least two samples for deltas: {path}"
        )
    assert first_monotonic is not None and last_monotonic is not None
    return sample_count, first_monotonic, last_monotonic


def validate_gpu_csv(path: Path, benchmark_duration_s: float) -> tuple[int, float]:
    try:
        with path.open(newline="", encoding="utf-8") as input_file:
            rows = list(csv.reader(input_file))
    except csv.Error as error:
        raise EvidenceValidationError(
            f"invalid GPU telemetry CSV {path}: {error}"
        ) from error
    if len(rows) < 3:
        raise EvidenceValidationError(
            f"GPU telemetry needs a header and at least two samples: {path}"
        )
    header = [column.strip() for column in rows[0]]
    if not header or header[0] != "timestamp":
        raise EvidenceValidationError(f"GPU telemetry lacks timestamp header: {path}")
    missing_columns = REQUIRED_GPU_COLUMNS - set(header)
    if missing_columns:
        raise EvidenceValidationError(
            f"GPU telemetry is missing required columns: {sorted(missing_columns)}"
        )
    if len(set(header)) != len(header):
        raise EvidenceValidationError(f"GPU telemetry has duplicate columns: {path}")
    column_indexes = {column: header.index(column) for column in REQUIRED_GPU_COLUMNS}

    timestamps: list[datetime] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(rows[0]):
            raise EvidenceValidationError(
                f"GPU telemetry column mismatch at {path}:{line_number}"
            )
        try:
            timestamps.append(datetime.strptime(row[0].strip(), "%Y/%m/%d %H:%M:%S.%f"))
        except ValueError as error:
            raise EvidenceValidationError(
                f"invalid GPU timestamp at {path}:{line_number}: {row[0]!r}"
            ) from error
        for column, column_index in column_indexes.items():
            if column == "timestamp":
                continue
            raw_value = row[column_index].strip()
            if raw_value == "[N/A]" and column in GPU_COLUMNS_ALLOWING_NA:
                continue
            numeric_text = raw_value.split(maxsplit=1)[0]
            try:
                value = float(numeric_text)
            except ValueError as error:
                raise EvidenceValidationError(
                    f"invalid GPU value for {column!r} at {path}:{line_number}: "
                    f"{raw_value!r}"
                ) from error
            if not math.isfinite(value) or value < 0:
                raise EvidenceValidationError(
                    f"GPU value for {column!r} must be finite and non-negative "
                    f"at {path}:{line_number}"
                )
    if any(
        current <= previous for previous, current in zip(timestamps, timestamps[1:])
    ):
        raise EvidenceValidationError(
            f"GPU telemetry timestamps must be strictly increasing: {path}"
        )
    coverage_s = (timestamps[-1] - timestamps[0]).total_seconds()
    if coverage_s + MONOTONIC_COVERAGE_TOLERANCE_S < benchmark_duration_s:
        raise EvidenceValidationError(
            f"GPU telemetry coverage {coverage_s:.3f}s is shorter than benchmark "
            f"duration {benchmark_duration_s:.3f}s"
        )
    return len(timestamps), coverage_s


def artifact_paths_from_manifest(
    manifest: Mapping[str, Any], project_root: Path
) -> dict[str, Path]:
    records = manifest.get("artifacts")
    if not isinstance(records, list):
        raise EvidenceValidationError("manifest artifacts must be an array")
    artifacts: dict[str, Path] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            raise EvidenceValidationError(
                "manifest contains an invalid artifact record"
            )
        name = record["name"]
        if name in artifacts:
            raise EvidenceValidationError(
                f"manifest contains duplicate artifact {name!r}"
            )
        relative_path = record.get("path")
        if not isinstance(relative_path, str):
            raise EvidenceValidationError(f"manifest artifact {name!r} lacks a path")
        try:
            resolved = validate_artifact_path(
                project_root, name, project_root / relative_path
            )
        except ManifestError as error:
            raise EvidenceValidationError(str(error)) from error
        artifacts[name] = resolved
    return artifacts


def _verify_manifest_hashes(
    manifest: Mapping[str, Any], project_root: Path, artifacts: Mapping[str, Path]
) -> None:
    if manifest.get("status") != "complete":
        return
    records = {record["name"]: record for record in manifest["artifacts"]}
    for name, path in artifacts.items():
        record = records[name]
        expected_size = record.get("size_bytes")
        expected_hash = record.get("sha256")
        actual_size = path.stat().st_size
        if expected_size != actual_size:
            raise EvidenceValidationError(
                f"artifact size changed for {name}: expected {expected_size}, got {actual_size}"
            )
        if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
            raise EvidenceValidationError(
                f"artifact SHA256 mismatch for {name}: {path}"
            )


def _validate_manifest_spec(
    manifest: Mapping[str, Any], expected: Mapping[str, object]
) -> None:
    if manifest.get("run_id") != expected["run_id"]:
        raise EvidenceValidationError("manifest run_id does not match requested run")
    workload = manifest.get("workload")
    if not isinstance(workload, dict):
        raise EvidenceValidationError("manifest workload must be an object")
    capture_mode = manifest.get("provenance_capture")
    provenance = manifest.get("provenance")
    if capture_mode not in {"run_start", "backfilled"} or not isinstance(
        provenance, dict
    ):
        raise EvidenceValidationError("manifest provenance metadata is invalid")
    represents_run_time = capture_mode == "run_start"
    capture_scope = "run_start" if represents_run_time else "manifest_backfill_time"
    if (
        provenance.get("capture_mode") != capture_mode
        or provenance.get("represents_run_time") is not represents_run_time
        or workload.get("capture_scope") != capture_scope
    ):
        raise EvidenceValidationError("manifest provenance scope is inconsistent")
    for key in (
        "phase",
        "input_len",
        "output_len",
        "concurrency",
        "repetition",
        "num_prompts",
        "request_rate",
        "random_range_ratio",
        "loadgen_seed",
    ):
        # Mirror raw validation: an omitted loadgen_seed means "do not invent or
        # require one" (legacy bundles). When provided, it must match exactly.
        if key == "loadgen_seed" and expected.get(key) is None:
            continue
        if not _values_equal(workload.get(key), expected.get(key)):
            raise EvidenceValidationError(
                f"manifest workload {key!r} mismatch: expected {expected.get(key)!r}, "
                f"got {workload.get(key)!r}"
            )
    server = manifest.get("server")
    if not isinstance(server, dict):
        raise EvidenceValidationError("manifest server must be an object")
    git = manifest.get("git")
    if (
        not isinstance(git, dict)
        or git.get("capture_scope") != capture_scope
        or server.get("capture_scope") != capture_scope
    ):
        raise EvidenceValidationError(
            "manifest Git/server capture scope is inconsistent"
        )
    for key in ("image", "model", "model_revision"):
        if expected.get(key) is not None and server.get(key) != expected[key]:
            raise EvidenceValidationError(
                f"manifest server {key!r} mismatch: expected {expected[key]!r}, "
                f"got {server.get(key)!r}"
            )


def _validate_plan_binding(
    manifest: Mapping[str, Any], project_root: Path, raw: Mapping[str, Any]
) -> None:
    workload = manifest.get("workload")
    if not isinstance(workload, dict) or workload.get("phase") != "open_loop_steady":
        return
    config = workload.get("config")
    if not isinstance(config, dict):
        raise EvidenceValidationError(
            "steady manifest workload.config must be an object"
        )

    required_config = (
        "run_namespace",
        "run_attempt",
        "plan_block",
        "plan_order",
        "plan_shuffle_seed",
        "plan_path",
        "plan_sha256",
        "nominal_arrival_horizon_seconds",
        "ttft_slo_ms",
        "e2e_slo_ms",
    )
    missing = [key for key in required_config if config.get(key) in (None, "")]
    if missing:
        raise EvidenceValidationError(
            f"steady manifest is missing ExperimentSpec fields: {missing}"
        )
    for slo_key in ("ttft_slo_ms", "e2e_slo_ms"):
        value = _as_decimal(config[slo_key])
        if value is None or not value.is_finite() or value <= 0:
            raise EvidenceValidationError(
                f"steady ExperimentSpec {slo_key} must be positive and finite"
            )
    if config.get("tpot_slo_ms") not in (None, ""):
        value = _as_decimal(config["tpot_slo_ms"])
        if value is None or not value.is_finite() or value <= 0:
            raise EvidenceValidationError(
                "steady ExperimentSpec tpot_slo_ms must be positive and finite"
            )

    plan_relative = Path(str(config["plan_path"]))
    if plan_relative.is_absolute():
        raise EvidenceValidationError("steady plan_path must be project-relative")
    plans_root = (project_root / "results/plans").resolve()
    plan_path = (project_root / plan_relative).resolve()
    try:
        plan_path.relative_to(plans_root)
    except ValueError as error:
        raise EvidenceValidationError(
            f"steady plan escapes results/plans: {plan_relative}"
        ) from error
    if not plan_path.is_file():
        raise EvidenceValidationError(f"steady plan is missing: {plan_path}")
    actual_plan_hash = sha256_file(plan_path)
    if actual_plan_hash != config["plan_sha256"]:
        raise EvidenceValidationError(
            f"steady plan SHA256 mismatch: expected {config['plan_sha256']}, "
            f"got {actual_plan_hash}"
        )

    with plan_path.open(newline="", encoding="utf-8") as input_file:
        plan_rows = list(csv.DictReader(input_file, delimiter="\t"))
    expected_row = {
        "block": config["plan_block"],
        "order_in_block": config["plan_order"],
        "repetition": workload.get("repetition"),
        "loadgen_seed": workload.get("loadgen_seed"),
        "request_rate": workload.get("request_rate"),
        "num_prompts": workload.get("num_prompts"),
        "arrival_window_seconds": config["nominal_arrival_horizon_seconds"],
        "plan_shuffle_seed": config["plan_shuffle_seed"],
        "run_namespace": config["run_namespace"],
        "input_len": workload.get("input_len"),
        "output_len": workload.get("output_len"),
        "max_concurrency": workload.get("concurrency"),
        "num_warmups": workload.get("num_warmups"),
        "random_range_ratio": workload.get("random_range_ratio"),
        "ttft_slo_ms": config["ttft_slo_ms"],
        "e2e_slo_ms": config["e2e_slo_ms"],
        "tpot_slo_ms": config.get("tpot_slo_ms", ""),
    }
    matches = [
        row
        for row in plan_rows
        if all(
            _values_equal(row.get(key), value) for key, value in expected_row.items()
        )
    ]
    if len(matches) != 1:
        raise EvidenceValidationError(
            "steady manifest does not bind exactly one plan row: "
            f"matches={len(matches)}, expected={expected_row}"
        )

    raw_expected = {
        "run_namespace": config["run_namespace"],
        "run_attempt": config["run_attempt"],
        "plan_block": config["plan_block"],
        "plan_order": config["plan_order"],
        "plan_shuffle_seed": config["plan_shuffle_seed"],
        "plan_sha256": config["plan_sha256"],
        "nominal_arrival_horizon_seconds": config["nominal_arrival_horizon_seconds"],
        "ttft_slo_ms": config["ttft_slo_ms"],
        "e2e_slo_ms": config["e2e_slo_ms"],
        "tpot_slo_ms": config.get("tpot_slo_ms", ""),
    }
    for key, value in raw_expected.items():
        _require_equal(raw, key, value)


def _validate_collector_exits(manifest: Mapping[str, Any]) -> None:
    collectors = manifest.get("collectors")
    if not isinstance(collectors, dict):
        raise EvidenceValidationError("manifest collectors must be an object")
    if manifest.get("provenance_capture") == "backfilled":
        return
    acceptable_exit_codes = {
        "gpu": {0, 130, 143},
        "metrics": {0},
        "benchmark": {0},
    }
    for name in ("gpu", "metrics", "benchmark"):
        record = collectors.get(name)
        if not isinstance(record, dict) or not isinstance(record.get("exit_code"), int):
            raise EvidenceValidationError(
                f"collector/process exit code was not recorded: {name}"
            )
        exit_code = record["exit_code"]
        computed_acceptable = exit_code in acceptable_exit_codes[name]
        if name == "gpu" and exit_code in {130, 143}:
            computed_acceptable = record.get("stop_requested_by_runner") is True
        if record.get("acceptable") is not computed_acceptable:
            raise EvidenceValidationError(
                f"collector/process {name} acceptable flag is inconsistent with "
                f"exit code {exit_code}"
            )
        if not computed_acceptable:
            raise EvidenceValidationError(
                f"collector/process {name} exited unexpectedly with {exit_code}"
            )


def validate_evidence_bundle(
    *,
    project_root: Path,
    artifacts: Mapping[str, Path],
    expected: Mapping[str, object],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, int]:
    missing_names = set(EXPECTED_ARTIFACT_NAMES) - set(artifacts)
    extra_names = set(artifacts) - set(EXPECTED_ARTIFACT_NAMES)
    if missing_names or extra_names:
        raise EvidenceValidationError(
            f"artifact set mismatch: missing={sorted(missing_names)}, unexpected={sorted(extra_names)}"
        )
    for name, path in artifacts.items():
        try:
            validated_path = validate_artifact_path(project_root, name, path)
        except ManifestError as error:
            raise EvidenceValidationError(str(error)) from error
        try:
            size = validated_path.stat().st_size
        except FileNotFoundError as error:
            raise EvidenceValidationError(
                f"required artifact is missing: {name}={validated_path}"
            ) from error
        if not validated_path.is_file() or size <= 0:
            raise EvidenceValidationError(
                f"required artifact is empty or not a file: {name}={validated_path}"
            )

    raw = validate_raw_result(artifacts["raw_result"], expected)
    metrics_samples, metrics_first, metrics_last = validate_metrics_jsonl(
        artifacts["metrics_timeseries"]
    )
    first_arrival = float(raw["start_times"][0])
    completion_times = [
        float(start_time) + float(ttft) + sum(float(value) for value in request_itls)
        for start_time, ttft, request_itls in zip(
            raw["start_times"], raw["ttfts"], raw["itls"]
        )
    ]
    last_completion = max(completion_times)
    if metrics_first > first_arrival + MONOTONIC_COVERAGE_TOLERANCE_S:
        raise EvidenceValidationError(
            "metrics time series starts after the first request: "
            f"metrics={metrics_first:.6f}, request={first_arrival:.6f}"
        )
    if metrics_last + MONOTONIC_COVERAGE_TOLERANCE_S < last_completion:
        raise EvidenceValidationError(
            "metrics time series ends before the last completion: "
            f"metrics={metrics_last:.6f}, completion={last_completion:.6f}"
        )
    gpu_samples, _gpu_coverage = validate_gpu_csv(
        artifacts["gpu_telemetry"], float(raw["duration"])
    )
    if manifest is not None:
        _validate_manifest_spec(manifest, expected)
        _validate_plan_binding(manifest, project_root, raw)
        _validate_collector_exits(manifest)
        _verify_manifest_hashes(manifest, project_root, artifacts)
    return {
        "completed_requests": raw["completed"],
        "metrics_samples": metrics_samples,
        "gpu_samples": gpu_samples,
    }


def _parse_artifacts(project_root: Path, values: Sequence[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values:
        name, separator, path_text = value.partition("=")
        if not separator or not name or name in artifacts:
            raise EvidenceValidationError(
                f"--artifact must be unique NAME=PATH, got {value!r}"
            )
        path = Path(path_text)
        artifacts[name] = path if path.is_absolute() else project_root / path
    return artifacts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--input-len", required=True, type=int)
    parser.add_argument("--output-len", required=True, type=int)
    parser.add_argument("--concurrency", required=True, type=int)
    parser.add_argument("--repetition", required=True, type=int)
    parser.add_argument("--num-prompts", required=True, type=int)
    parser.add_argument("--request-rate", required=True)
    parser.add_argument("--random-range-ratio")
    parser.add_argument("--loadgen-seed", type=int)
    parser.add_argument("--image")
    parser.add_argument("--model")
    parser.add_argument("--model-revision")
    parser.add_argument(
        "--mark-complete",
        action="store_true",
        help="atomically persist complete only after every validation passes",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        manifest_path = (
            validate_manifest_path(project_root, args.manifest, args.run_id)
            if args.manifest
            else None
        )
        manifest = load_manifest(manifest_path) if manifest_path else None
        if manifest is not None:
            artifacts = artifact_paths_from_manifest(manifest, project_root)
            if args.artifact:
                supplied = _parse_artifacts(project_root, args.artifact)
                if {key: value.resolve() for key, value in supplied.items()} != {
                    key: value.resolve() for key, value in artifacts.items()
                }:
                    raise EvidenceValidationError(
                        "command artifact paths do not match persisted manifest"
                    )
        else:
            artifacts = _parse_artifacts(project_root, args.artifact)

        expected: dict[str, object] = {
            "run_id": args.run_id,
            "phase": args.phase,
            "input_len": args.input_len,
            "output_len": args.output_len,
            "concurrency": args.concurrency,
            "repetition": args.repetition,
            "num_prompts": args.num_prompts,
            "request_rate": args.request_rate,
            "random_range_ratio": args.random_range_ratio,
            "loadgen_seed": args.loadgen_seed,
            "image": args.image,
            "model": args.model,
            "model_revision": args.model_revision,
        }
        summary = validate_evidence_bundle(
            project_root=project_root,
            artifacts=artifacts,
            expected=expected,
            manifest=manifest,
        )
        if args.mark_complete:
            if manifest is None or manifest_path is None:
                raise EvidenceValidationError("--mark-complete requires --manifest")
            if manifest.get("status") == "complete":
                pass  # Hashes and content were revalidated above; preserve the manifest byte-for-byte.
            elif manifest.get("status") in {"planned", "running"}:
                mark_complete(manifest, project_root)
                atomic_write_json(manifest_path, manifest)
            else:
                raise EvidenceValidationError(
                    f"cannot complete manifest in state {manifest.get('status')!r}"
                )
    except (EvidenceValidationError, ManifestError, OSError) as error:
        print(f"evidence validation failed: {error}", file=sys.stderr)
        return 1
    print(
        "evidence validation passed: "
        f"{args.run_id} ({summary['completed_requests']} requests, "
        f"{summary['metrics_samples']} metrics samples, "
        f"{summary['gpu_samples']} GPU samples)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
