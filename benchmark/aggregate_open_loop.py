#!/usr/bin/env python3
"""Aggregate open-loop repetitions by offered request rate.

Pipeline:
  results/raw/open_loop-*.json
    -> summarize_results.py       -> open-loop-summary.csv
       (one row per run / repetition)
    -> aggregate_open_loop.py     -> open-loop-aggregate.csv
       (one row per arrival-rate point, with median/min/max across reps)

The offered request rate is part of the grouping key.  Omitting it would
merge different arrival conditions into one row and make the saturation
curve unusable.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

try:  # Support both package imports and direct execution from the repository root.
    from benchmark.summarize_results import (
        validate_summary_evidence_rows,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution path.
    from summarize_results import (  # type: ignore[no-redef]
        validate_summary_evidence_rows,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = PROJECT_ROOT / "results" / "summary" / "open-loop-summary.csv"
OUTPUT_FILE = PROJECT_ROOT / "results" / "summary" / "open-loop-aggregate.csv"

# Core metrics must be present in every repetition. Optional metrics are
# accepted only when every repetition contains them, so schema drift cannot
# silently create aggregates from different measurement sets.
CORE_METRICS = (
    "duration",
    "request_throughput",
    "input_throughput",
    "output_throughput",
    "total_token_throughput",
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
)

OPTIONAL_METRICS = (
    "request_goodput",
    "actual_input_len_min",
    "actual_input_len_max",
    "actual_input_len_mean",
    "input_token_abs_drift_total",
    "input_token_abs_drift_pct",
    "arrival_span_s",
    "realized_send_rate",
    "drain_time_approx_s",
)

REQUIRED_FIELDS = (
    "evidence_status",
    "experiment_spec_sha256",
    "phase",
    "run_id",
    "input_len",
    "output_len",
    "concurrency",
    "request_rate",
    "num_prompts",
    "repetition",
    "completed",
    "failed",
)


def parse_request_rate(raw_value: str) -> float:
    """Parse finite rates and the vLLM ``inf`` burst-control value."""
    value = raw_value.strip().lower()
    if value in {"inf", "+inf", "infinity", "+infinity"}:
        return float("inf")

    rate = float(value)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError(f"request_rate must be positive or inf, got {raw_value!r}")
    return rate


def values(rows: list[dict[str, str]], key: str, *, required: bool) -> list[float]:
    """Collect one numeric metric without silently accepting partial rows."""
    present = [row.get(key, "") not in ("", None) for row in rows]
    if required and not all(present):
        missing_runs = [
            row.get("run_id", "<unknown>") for row, ok in zip(rows, present) if not ok
        ]
        raise ValueError(f"Missing required metric {key!r} in runs: {missing_runs}")
    if any(present) and not all(present):
        raise ValueError(f"Optional metric {key!r} is present in only part of a group")
    if not all(present):
        return []
    parsed = [float(row[key]) for row in rows]
    invalid_runs = [
        row.get("run_id", "<unknown>")
        for row, value in zip(rows, parsed)
        if not math.isfinite(value) or value < 0
    ]
    if invalid_runs:
        raise ValueError(
            f"Metric {key!r} must be finite and non-negative in runs: {invalid_runs}"
        )
    return parsed


def rate_label(rate: float) -> str:
    """Return a compact label for console output."""
    return "inf" if math.isinf(rate) else f"{rate:g}"


def validate_group(
    rows: list[dict[str, str]],
    *,
    key: tuple[str, str, int, int, int, float, int],
    expected_repetitions: int | None,
) -> None:
    if expected_repetitions is not None and len(rows) != expected_repetitions:
        raise ValueError(
            f"Group {key} has {len(rows)} repetitions; expected {expected_repetitions}"
        )

    repetitions = [int(row["repetition"]) for row in rows]
    if len(repetitions) != len(set(repetitions)):
        raise ValueError(f"Group {key} has duplicate repetitions: {repetitions}")
    if expected_repetitions is not None:
        expected = set(range(1, expected_repetitions + 1))
        if set(repetitions) != expected:
            raise ValueError(
                f"Group {key} repetition set is {sorted(repetitions)}; "
                f"expected {sorted(expected)}"
            )

    run_ids = [row["run_id"] for row in rows]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError(f"Group {key} has duplicate run_ids: {run_ids}")

    evidence_statuses = {row["evidence_status"] for row in rows}
    if len(evidence_statuses) != 1:
        raise ValueError(
            f"Group {key} mixes evidence provenance: {sorted(evidence_statuses)}"
        )

    for row in rows:
        if int(row["failed"]) != 0:
            raise ValueError(f"Run {row['run_id']} has failed requests")
        if int(row["completed"]) <= 0:
            raise ValueError(f"Run {row['run_id']} has no completed requests")

    seeds = [row.get("loadgen_seed", "") for row in rows]
    present_seeds = [seed for seed in seeds if seed not in ("", None)]
    if present_seeds and len(present_seeds) != len(rows):
        raise ValueError(f"Group {key} mixes seeded and unseeded repetitions")
    if present_seeds:
        unique_seeds = set(present_seeds)
        # Phase 3a uses one shared default seed for every repetition. Phase 3b
        # paired blocks require a unique seed per repetition. Reject the mixed
        # case where some seeds repeat but not all.
        if len(unique_seeds) not in {1, len(present_seeds)}:
            raise ValueError(
                f"Group {key} has duplicate load-generator seeds: {seeds}"
            )


def _seed_mapping(rows: list[dict[str, str]]) -> dict[int, str] | None:
    """Return repetition->seed when seeds form a paired block; else None.

    Empty seeds and a single shared seed across all repetitions (Phase 3a
    default) are not paired-seed designs.
    """
    if all(row.get("loadgen_seed", "") in ("", None) for row in rows):
        return None
    mapping = {int(row["repetition"]): row["loadgen_seed"] for row in rows}
    if any(seed in ("", None) for seed in mapping.values()):
        # Mixed empty/non-empty is rejected by validate_group; treat as seeded
        # here so validate_paired_seed_blocks can surface the inconsistency.
        return mapping
    if len(set(mapping.values())) == 1:
        return None
    return mapping


def validate_paired_seed_blocks(
    grouped: dict[tuple[str, str, int, int, int, float, int], list[dict[str, str]]],
) -> None:
    """Require uniquely seeded rate sweeps to reuse the same repetition->seed blocks."""
    experiments: dict[
        tuple[str, str, int, int, int],
        list[tuple[float, list[dict[str, str]]]],
    ] = defaultdict(list)
    for (
        experiment_spec,
        phase,
        input_len,
        output_len,
        concurrency,
        rate,
        _num_prompts,
    ), rows in grouped.items():
        experiments[
            (experiment_spec, phase, input_len, output_len, concurrency)
        ].append((rate, rows))

    for experiment, rate_groups in experiments.items():
        mappings: list[tuple[float, dict[int, str] | None]] = []
        for rate, rows in rate_groups:
            mappings.append((rate, _seed_mapping(rows)))

        seeded = [mapping is not None for _, mapping in mappings]
        if any(seeded) and not all(seeded):
            raise ValueError(
                f"Experiment {experiment} mixes seeded and unseeded rate groups"
            )
        if not any(seeded):
            continue
        reference_rate, reference = mappings[0]
        for rate, mapping in mappings[1:]:
            if mapping != reference:
                raise ValueError(
                    f"Experiment {experiment} does not use paired seed blocks: "
                    f"rate {reference_rate:g} has {reference}, rate {rate:g} has {mapping}"
                )


def aggregate(
    source_rows: list[dict[str, str]],
    *,
    expected_repetitions: int | None,
    allow_legacy_unmanifested: bool = False,
) -> list[dict[str, str | int | float]]:
    if not source_rows:
        raise ValueError("Open-loop summary contains no rows")

    allowed_statuses = {"complete", "complete-backfilled"}
    if allow_legacy_unmanifested:
        allowed_statuses.add("legacy-unmanifested")
    for row in source_rows:
        status = row.get("evidence_status", "")
        if status not in allowed_statuses:
            raise ValueError(
                f"Run {row.get('run_id', '<unknown>')} has inadmissible "
                f"evidence_status {status!r}; allowed={sorted(allowed_statuses)}"
            )

    grouped: dict[tuple[str, str, int, int, int, float, int], list[dict[str, str]]] = (
        defaultdict(list)
    )
    for row in source_rows:
        key = (
            row["experiment_spec_sha256"],
            row["phase"],
            int(row["input_len"]),
            int(row["output_len"]),
            int(row["concurrency"]),
            parse_request_rate(row["request_rate"]),
            int(row["num_prompts"]),
        )
        grouped[key].append(row)

    validate_paired_seed_blocks(grouped)

    aggregate_rows: list[dict[str, str | int | float]] = []
    for key, rows in sorted(grouped.items()):
        (
            experiment_spec,
            phase,
            input_len,
            output_len,
            concurrency,
            request_rate,
            num_prompts,
        ) = key
        validate_group(rows, key=key, expected_repetitions=expected_repetitions)
        seed_values = [row.get("loadgen_seed", "") for row in rows]
        aggregate_row: dict[str, str | int | float] = {
            "evidence_status": rows[0]["evidence_status"],
            "experiment_spec_sha256": experiment_spec,
            "run_namespace": rows[0].get("run_namespace", ""),
            "plan_sha256": rows[0].get("plan_sha256", ""),
            "nominal_arrival_horizon_seconds": rows[0].get(
                "nominal_arrival_horizon_seconds", ""
            ),
            "ttft_slo_ms": rows[0].get("ttft_slo_ms", ""),
            "e2e_slo_ms": rows[0].get("e2e_slo_ms", ""),
            "tpot_slo_ms": rows[0].get("tpot_slo_ms", ""),
            "phase": phase,
            "input_len": input_len,
            "output_len": output_len,
            "concurrency": concurrency,
            "request_rate": request_rate,
            "num_prompts": num_prompts,
            "repetitions": len(rows),
            "distinct_loadgen_seeds": len(
                {value for value in seed_values if value not in ("", None)}
            ),
            "completed_total": sum(int(row["completed"]) for row in rows),
            "failed_total": sum(int(row["failed"]) for row in rows),
        }

        for metric in CORE_METRICS:
            metric_values = values(rows, metric, required=True)
            aggregate_row[f"{metric}_median"] = statistics.median(metric_values)
            aggregate_row[f"{metric}_min"] = min(metric_values)
            aggregate_row[f"{metric}_max"] = max(metric_values)

        for metric in OPTIONAL_METRICS:
            metric_values = values(rows, metric, required=False)
            if not metric_values:
                continue
            aggregate_row[f"{metric}_median"] = statistics.median(metric_values)
            aggregate_row[f"{metric}_min"] = min(metric_values)
            aggregate_row[f"{metric}_max"] = max(metric_values)

        # This ratio uses vLLM's whole-benchmark duration, including the final
        # response drain. It is useful for describing a finite scan but is not
        # a fulfillment percentage or a sustainable-capacity decision.
        throughput_median = float(aggregate_row["request_throughput_median"])
        if math.isfinite(request_rate):
            aggregate_row["benchmark_throughput_vs_configured_rate_pct"] = (
                throughput_median / request_rate * 100.0
            )
        else:
            aggregate_row["benchmark_throughput_vs_configured_rate_pct"] = ""

        aggregate_rows.append(aggregate_row)

    return aggregate_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate validated open-loop repetitions by configured rate"
    )
    parser.add_argument("--input", type=Path, default=SUMMARY_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument(
        "--expected-repetitions",
        type=int,
        default=5,
        help="Required unique repetitions per rate (default: 5; use 0 to disable)",
    )
    parser.add_argument(
        "--allow-legacy-unmanifested",
        action="store_true",
        help=(
            "Explicitly aggregate rows labelled legacy-unmanifested; complete rows "
            "are always revalidated against their manifests"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.expected_repetitions < 0:
        raise ValueError("--expected-repetitions cannot be negative")
    expected_repetitions = args.expected_repetitions or None

    with args.input.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        fieldnames = reader.fieldnames or []
        missing = [field for field in REQUIRED_FIELDS if field not in fieldnames]
        if missing:
            raise ValueError(
                f"{args.input} is missing required columns: {', '.join(missing)}"
            )
        source_rows = list(reader)

    validate_summary_evidence_rows(
        source_rows,
        allow_legacy_unmanifested=args.allow_legacy_unmanifested,
    )

    aggregate_rows = aggregate(
        source_rows,
        expected_repetitions=expected_repetitions,
        allow_legacy_unmanifested=args.allow_legacy_unmanifested,
    )

    metric_columns = [
        f"{metric}_{suffix}"
        for metric in (*CORE_METRICS, *OPTIONAL_METRICS)
        for suffix in ("median", "min", "max")
    ]
    fieldnames = [
        "evidence_status",
        "experiment_spec_sha256",
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
        "num_prompts",
        "repetitions",
        "distinct_loadgen_seeds",
        "completed_total",
        "failed_total",
        "benchmark_throughput_vs_configured_rate_pct",
        *metric_columns,
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(aggregate_rows)

    print(f"Wrote {len(aggregate_rows)} rows to {args.output}")
    for row in aggregate_rows:
        ratio = row["benchmark_throughput_vs_configured_rate_pct"]
        ratio_text = "n/a" if ratio == "" else f"{float(ratio):.1f}%"
        print(
            f"in={row['input_len']} out={row['output_len']} "
            f"c={row['concurrency']} offered={rate_label(float(row['request_rate']))} "
            f"reps={row['repetitions']}: "
            f"completed_req_s={float(row['request_throughput_median']):.2f} "
            f"({ratio_text} of configured rate over full benchmark) "
            f"output_tok_s={float(row['output_throughput_median']):.2f} "
            f"p99_ttft_ms={float(row['p99_ttft_ms_median']):.2f} "
            f"p99_e2e_ms={float(row['p99_e2el_ms_median']):.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
