#!/usr/bin/env python3
"""Aggregate vLLM metric JSONL streams into one fail-closed row per run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Sequence

try:  # Support package import and direct execution.
    from benchmark.accepted_attempts import load_accepted_run_ids
    from benchmark.evidence_manifest import load_manifest
    from benchmark.summarize_results import (
        MANIFEST_DIR,
        RAW_DIR,
        validate_complete_bundle_for_raw,
    )
    from benchmark.validate_evidence import artifact_paths_from_manifest
except ModuleNotFoundError:  # pragma: no cover - direct script execution path.
    from accepted_attempts import load_accepted_run_ids  # type: ignore[no-redef]
    from evidence_manifest import load_manifest  # type: ignore[no-redef]
    from summarize_results import (  # type: ignore[no-redef]
        MANIFEST_DIR,
        RAW_DIR,
        validate_complete_bundle_for_raw,
    )
    from validate_evidence import (  # type: ignore[no-redef]
        artifact_paths_from_manifest,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_DIR = PROJECT_ROOT / "results" / "telemetry"
DEFAULT_OUTPUT = PROJECT_ROOT / "results" / "summary" / "telemetry-summary.csv"

RUNNING = "vllm:num_requests_running"
WAITING = "vllm:num_requests_waiting"
WAITING_BY_REASON = "vllm:num_requests_waiting_by_reason"
KV_USAGE = "vllm:kv_cache_usage_perc"
PREEMPTIONS = "vllm:num_preemptions_total"
QUEUE_TIME_SUM = "vllm:request_queue_time_seconds_sum"

REQUIRED_BASE_METRICS = {
    RUNNING,
    WAITING,
    WAITING_BY_REASON,
    KV_USAGE,
    PREEMPTIONS,
    QUEUE_TIME_SUM,
}

FIELDNAMES = (
    "evidence_status",
    "run_id",
    "samples",
    "errors",
    "coverage_s",
    "max_running",
    "max_waiting",
    "max_capacity_waiting",
    "max_deferred_waiting",
    "kv_cache_usage_p95_pct",
    "kv_cache_usage_max_pct",
    "preemptions_delta",
    "queue_time_sum_delta_s",
    "counter_delta_source",
    "counter_reset",
)

LABEL_RE = re.compile(
    r'(?:^|,)(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<value>(?:\\.|[^"\\])*)"'
)


class TelemetryError(ValueError):
    """Raised when a telemetry stream cannot support a trustworthy summary."""


def percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise TelemetryError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def split_sample_name(sample_name: str) -> tuple[str, dict[str, str]]:
    if "{" not in sample_name:
        return sample_name, {}
    base_name, raw_labels = sample_name.split("{", 1)
    if not raw_labels.endswith("}"):
        raise TelemetryError(f"malformed Prometheus sample name: {sample_name!r}")
    labels = {
        match.group("name"): match.group("value")
        for match in LABEL_RE.finditer(raw_labels[:-1])
    }
    return base_name, labels


def finite_number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryError(f"{context} must be a JSON number")
    number = float(value)
    if not math.isfinite(number):
        raise TelemetryError(f"{context} must be finite")
    if number < 0:
        raise TelemetryError(f"{context} must be non-negative")
    return number


def aggregate_metric_sample(metrics: object, context: str) -> dict[str, float]:
    if not isinstance(metrics, dict):
        raise TelemetryError(f"{context}.metrics must be a JSON object")

    by_base: dict[str, list[tuple[dict[str, str], float]]] = {}
    for raw_name, raw_value in metrics.items():
        if not isinstance(raw_name, str):
            raise TelemetryError(f"{context}.metrics contains a non-string key")
        base_name, labels = split_sample_name(raw_name)
        if base_name not in REQUIRED_BASE_METRICS:
            continue
        value = finite_number(raw_value, f"{context}.metrics[{raw_name!r}]")
        by_base.setdefault(base_name, []).append((labels, value))

    missing = sorted(REQUIRED_BASE_METRICS.difference(by_base))
    if missing:
        raise TelemetryError(
            f"{context}.metrics is missing required metrics: {', '.join(missing)}"
        )

    capacity = [
        value
        for labels, value in by_base[WAITING_BY_REASON]
        if labels.get("reason") == "capacity"
    ]
    deferred = [
        value
        for labels, value in by_base[WAITING_BY_REASON]
        if labels.get("reason") == "deferred"
    ]
    if not capacity or not deferred:
        raise TelemetryError(
            f"{context}.metrics must include capacity and deferred waiting reasons"
        )

    return {
        "running": sum(value for _, value in by_base[RUNNING]),
        "waiting": sum(value for _, value in by_base[WAITING]),
        "capacity_waiting": sum(capacity),
        "deferred_waiting": sum(deferred),
        # Cache usage is a per-engine fraction; the most constrained engine is
        # the useful signal if a future configuration exports multiple engines.
        "kv_usage": max(value for _, value in by_base[KV_USAGE]),
        "preemptions": sum(value for _, value in by_base[PREEMPTIONS]),
        "queue_time_sum": sum(value for _, value in by_base[QUEUE_TIME_SUM]),
    }


def counter_delta(values: Sequence[float]) -> tuple[float, bool]:
    if len(values) < 2:
        raise TelemetryError("counter deltas require at least two metric samples")
    delta = 0.0
    reset = False
    previous = values[0]
    for current in values[1:]:
        if current < previous:
            # After a reset the first observed value is the only known part of
            # the new counter epoch.  Preserve it but mark the run for review.
            reset = True
            delta += current
        else:
            delta += current - previous
        previous = current
    return delta, reset


def prometheus_counter_value(path: Path, metric_name: str) -> float:
    values: list[float] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise TelemetryError(f"cannot read counter snapshot {path}: {error}") from error
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2 or fields[0].split("{", 1)[0] != metric_name:
            continue
        try:
            raw_value = float(fields[1])
        except ValueError as error:
            raise TelemetryError(
                f"invalid {metric_name} value at {path}:{line_number}"
            ) from error
        values.append(finite_number(raw_value, f"{path}:{line_number}"))
    if not values:
        raise TelemetryError(f"counter snapshot {path} lacks {metric_name}")
    return sum(values)


def snapshot_counter_delta(
    before_path: Path, after_path: Path
) -> tuple[float, float, bool]:
    before_preemptions = prometheus_counter_value(before_path, PREEMPTIONS)
    after_preemptions = prometheus_counter_value(after_path, PREEMPTIONS)
    before_queue = prometheus_counter_value(before_path, QUEUE_TIME_SUM)
    after_queue = prometheus_counter_value(after_path, QUEUE_TIME_SUM)
    preemption_reset = after_preemptions < before_preemptions
    queue_reset = after_queue < before_queue
    return (
        (
            after_preemptions - before_preemptions
            if not preemption_reset
            else after_preemptions
        ),
        after_queue - before_queue if not queue_reset else after_queue,
        preemption_reset or queue_reset,
    )


def parse_timestamp(raw_timestamp: object, context: str) -> None:
    if not isinstance(raw_timestamp, str) or not raw_timestamp:
        raise TelemetryError(f"{context}.timestamp_utc must be a non-empty string")
    try:
        timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise TelemetryError(
            f"{context}.timestamp_utc is not an ISO-8601 timestamp"
        ) from error
    if timestamp.tzinfo is None:
        raise TelemetryError(f"{context}.timestamp_utc must include a timezone")


def run_id_from_path(path: Path) -> str:
    suffix = "-metrics.jsonl"
    if not path.name.endswith(suffix):
        raise TelemetryError(
            f"telemetry filename must end with {suffix!r}: {path.name}"
        )
    run_id = path.name[: -len(suffix)]
    if not run_id:
        raise TelemetryError(f"telemetry filename has an empty run id: {path.name}")
    return run_id


def aggregate_file(path: Path) -> dict[str, str | int | float | bool]:
    run_id = run_id_from_path(path)
    attempts: list[float] = []
    metric_samples: list[dict[str, float]] = []
    error_count = 0

    try:
        input_file = path.open(encoding="utf-8")
    except OSError as error:
        raise TelemetryError(f"cannot read {path}: {error}") from error

    with input_file:
        for line_number, raw_line in enumerate(input_file, start=1):
            context = f"{path}:{line_number}"
            if not raw_line.strip():
                raise TelemetryError(f"{context} is blank")
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise TelemetryError(f"{context} is not valid JSON: {error}") from error
            if not isinstance(record, dict):
                raise TelemetryError(f"{context} must contain a JSON object")

            parse_timestamp(record.get("timestamp_utc"), context)
            monotonic_s = finite_number(
                record.get("monotonic_s"), f"{context}.monotonic_s"
            )
            if attempts and monotonic_s <= attempts[-1]:
                raise TelemetryError(
                    f"{context}.monotonic_s must be strictly increasing"
                )
            attempts.append(monotonic_s)

            has_metrics = "metrics" in record
            has_error = "error" in record
            if has_metrics == has_error:
                raise TelemetryError(
                    f"{context} must contain exactly one of metrics or error"
                )
            if has_error:
                if not isinstance(record["error"], str) or not record["error"].strip():
                    raise TelemetryError(f"{context}.error must be a non-empty string")
                error_count += 1
                continue
            metric_samples.append(aggregate_metric_sample(record["metrics"], context))

    if not attempts:
        raise TelemetryError(f"{path} is empty")
    if len(metric_samples) < 2:
        raise TelemetryError(
            f"{path} has {len(metric_samples)} successful sample(s); at least 2 required"
        )

    preemption_delta, preemption_reset = counter_delta(
        [sample["preemptions"] for sample in metric_samples]
    )
    queue_time_delta, queue_time_reset = counter_delta(
        [sample["queue_time_sum"] for sample in metric_samples]
    )
    kv_usage = [sample["kv_usage"] for sample in metric_samples]

    return {
        "run_id": run_id,
        "samples": len(metric_samples),
        "errors": error_count,
        "coverage_s": attempts[-1] - attempts[0],
        "max_running": max(sample["running"] for sample in metric_samples),
        "max_waiting": max(sample["waiting"] for sample in metric_samples),
        "max_capacity_waiting": max(
            sample["capacity_waiting"] for sample in metric_samples
        ),
        "max_deferred_waiting": max(
            sample["deferred_waiting"] for sample in metric_samples
        ),
        "kv_cache_usage_p95_pct": percentile(kv_usage, 0.95) * 100.0,
        "kv_cache_usage_max_pct": max(kv_usage) * 100.0,
        "preemptions_delta": preemption_delta,
        "queue_time_sum_delta_s": queue_time_delta,
        "counter_delta_source": "timeseries",
        "counter_reset": preemption_reset or queue_time_reset,
    }


def aggregate_paths(paths: Sequence[Path]) -> list[dict[str, str | int | float | bool]]:
    if not paths:
        raise TelemetryError("telemetry pattern matched no files")
    rows = [aggregate_file(path) for path in paths]
    run_ids = [str(row["run_id"]) for row in rows]
    if len(set(run_ids)) != len(run_ids):
        raise TelemetryError("telemetry inputs contain duplicate run ids")
    return rows


def write_rows(
    rows: Sequence[dict[str, str | int | float | bool]], output_path: Path
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate per-run vLLM metrics telemetry"
    )
    parser.add_argument(
        "--telemetry-dir",
        type=Path,
        default=TELEMETRY_DIR,
        help=f"Directory containing metric JSONL files (default: {TELEMETRY_DIR})",
    )
    parser.add_argument(
        "--allow-legacy-unmanifested",
        action="store_true",
        help="Explicitly aggregate historical telemetry without complete manifests",
    )
    parser.add_argument(
        "--pattern",
        default="*-metrics.jsonl",
        help="Glob relative to --telemetry-dir (default: %(default)s)",
    )
    parser.add_argument(
        "--accepted-attempts",
        type=Path,
        help="TSV ledger selecting one complete attempt for each logical plan row",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-poll-errors",
        action="store_true",
        help=(
            "Write a diagnostic summary even when poller error records exist; "
            "by default any polling error fails closed"
        ),
    )
    parser.add_argument(
        "--allow-counter-resets",
        action="store_true",
        help=(
            "Write a diagnostic summary even when cumulative counters reset; "
            "by default a reset fails closed"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if Path(args.pattern).is_absolute():
        raise TelemetryError("--pattern must be relative to --telemetry-dir")
    if args.accepted_attempts:
        if args.allow_legacy_unmanifested:
            raise TelemetryError(
                "--accepted-attempts cannot be combined with legacy admission"
            )
        run_ids = load_accepted_run_ids(
            args.accepted_attempts, project_root=PROJECT_ROOT
        )
        paths = [
            args.telemetry_dir / f"{run_id}-metrics.jsonl"
            for run_id in run_ids
        ]
    else:
        paths = sorted(
            path for path in args.telemetry_dir.glob(args.pattern) if path.is_file()
        )
    rows = aggregate_paths(paths)
    paths_by_run = {run_id_from_path(path): path.resolve() for path in paths}
    for row in rows:
        run_id = str(row["run_id"])
        manifest_path = MANIFEST_DIR / f"{run_id}.json"
        if manifest_path.exists():
            row["evidence_status"] = validate_complete_bundle_for_raw(
                RAW_DIR / f"{run_id}.json"
            )
            manifest = load_manifest(manifest_path)
            artifacts = artifact_paths_from_manifest(manifest, PROJECT_ROOT)
            canonical_metrics = artifacts["metrics_timeseries"].resolve()
            if paths_by_run[run_id] != canonical_metrics:
                raise TelemetryError(
                    f"telemetry input for {run_id} is not the manifest-bound artifact: "
                    f"input={paths_by_run[run_id]}, manifest={canonical_metrics}"
                )
            before_path = artifacts["metrics_before"]
            after_path = artifacts["metrics_after"]
        elif args.allow_legacy_unmanifested:
            row["evidence_status"] = "legacy-unmanifested"
            before_path = paths_by_run[run_id].with_name(f"{run_id}-metrics-before.txt")
            after_path = paths_by_run[run_id].with_name(f"{run_id}-metrics-after.txt")
        else:
            raise TelemetryError(
                f"missing complete manifest for telemetry run {run_id}; use "
                "--allow-legacy-unmanifested only for historical diagnostics"
            )
        if before_path.is_file() and after_path.is_file():
            preemptions_delta, queue_delta, snapshot_reset = snapshot_counter_delta(
                before_path, after_path
            )
            row["preemptions_delta"] = preemptions_delta
            row["queue_time_sum_delta_s"] = queue_delta
            row["counter_delta_source"] = "metrics_before_after"
            row["counter_reset"] = bool(row["counter_reset"]) or snapshot_reset
        elif manifest_path.exists():
            raise TelemetryError(
                f"manifest-bound counter snapshots are missing for {run_id}"
            )
    rows_with_errors = [row for row in rows if int(row["errors"]) > 0]
    if rows_with_errors and not args.allow_poll_errors:
        affected_runs = ", ".join(str(row["run_id"]) for row in rows_with_errors)
        raise TelemetryError(
            "polling errors make telemetry incomplete for: "
            f"{affected_runs}; use --allow-poll-errors only for diagnostic output"
        )
    rows_with_resets = [row for row in rows if bool(row["counter_reset"])]
    if rows_with_resets and not args.allow_counter_resets:
        affected_runs = ", ".join(str(row["run_id"]) for row in rows_with_resets)
        raise TelemetryError(
            "cumulative counter resets make deltas ambiguous for: "
            f"{affected_runs}; use --allow-counter-resets only for diagnostic output"
        )
    write_rows(rows, args.output)
    print(f"Wrote {len(rows)} rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
