#!/usr/bin/env python3
"""Aggregate prefill/decode repetitions.

Pipeline:
  results/raw/pd-*.json
    -> summarize_results.py     -> prefill-decoder-summary.csv
       (one row per run/repetition)
    -> aggregate_prefill_decoder.py
       -> prefill-decoder-aggregate.csv
          (one row per workload, with median/min/max across repetitions)

Unlike the closed-loop baseline, the prefill/decode experiment keeps
concurrency fixed at 8, so there are no c1/previous-concurrency gain fields.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

try:
    from benchmark.summarize_results import validate_summary_evidence_rows
except ModuleNotFoundError:  # pragma: no cover - direct script execution path.
    from summarize_results import validate_summary_evidence_rows  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = PROJECT_ROOT / "results" / "summary" / "prefill-decoder-summary.csv"
OUTPUT_FILE = PROJECT_ROOT / "results" / "summary" / "prefill-decoder-aggregate.csv"

# These are the numeric fields in prefill-decoder-summary.csv.  Aggregate all
# of them so the aggregate retains the same measurement coverage as the
# per-run summary rather than silently dropping input throughput, means, etc.
METRICS = (
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


def values(rows: list[dict[str, str]], key: str) -> list[float]:
    """Return one required finite, non-negative value per repetition."""
    if not all(row.get(key, "") != "" for row in rows):
        raise ValueError(f"Metric {key!r} is missing from part of a workload group")
    parsed = [float(row[key]) for row in rows]
    if any(not math.isfinite(value) or value < 0 for value in parsed):
        raise ValueError(f"Metric {key!r} must be finite and non-negative")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=SUMMARY_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument(
        "--expected-repetitions",
        type=int,
        default=5,
        help="Required unique repetitions per workload (default: 5)",
    )
    parser.add_argument(
        "--allow-legacy-unmanifested",
        action="store_true",
        help="Explicitly aggregate historical summary rows without manifests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.expected_repetitions <= 0:
        raise ValueError("--expected-repetitions must be positive")
    with args.input.open(newline="", encoding="utf-8") as input_file:
        source_rows = list(csv.DictReader(input_file))
    validate_summary_evidence_rows(
        source_rows,
        allow_legacy_unmanifested=args.allow_legacy_unmanifested,
    )

    grouped: dict[tuple[str, str, int, int, int, int], list[dict[str, str]]] = (
        defaultdict(list)
    )
    for row in source_rows:
        key = (
            row["experiment_spec_sha256"],
            row["phase"],
            int(row["input_len"]),
            int(row["output_len"]),
            int(row["concurrency"]),
            int(row["num_prompts"]),
        )
        grouped[key].append(row)

    aggregate_rows: list[dict[str, str | int | float]] = []
    for (
        experiment_spec,
        phase,
        input_len,
        output_len,
        concurrency,
        num_prompts,
    ), rows in sorted(grouped.items()):
        repetitions = [int(row["repetition"]) for row in rows]
        if set(repetitions) != set(range(1, args.expected_repetitions + 1)):
            raise ValueError(
                f"Workload {(phase, input_len, output_len, concurrency)} has "
                f"repetitions {sorted(repetitions)}"
            )
        if len(repetitions) != len(set(repetitions)):
            raise ValueError("Prefill/decode workload has duplicate repetitions")
        if any(int(row["failed"]) != 0 for row in rows):
            raise ValueError(
                "Failed requests cannot enter the prefill/decode aggregate"
            )
        statuses = {row["evidence_status"] for row in rows}
        if len(statuses) != 1:
            raise ValueError("A prefill/decode group cannot mix evidence provenance")
        aggregate_row: dict[str, str | int | float] = {
            "evidence_status": rows[0]["evidence_status"],
            "experiment_spec_sha256": experiment_spec,
            "phase": phase,
            "input_len": input_len,
            "output_len": output_len,
            "concurrency": concurrency,
            "num_prompts": num_prompts,
            "repetitions": len(rows),
            "completed_total": sum(int(row["completed"]) for row in rows),
            "failed_total": sum(int(row["failed"]) for row in rows),
        }

        for metric in METRICS:
            metric_values = values(rows, metric)
            if not metric_values:
                continue
            aggregate_row[f"{metric}_median"] = statistics.median(metric_values)
            aggregate_row[f"{metric}_min"] = min(metric_values)
            aggregate_row[f"{metric}_max"] = max(metric_values)

        aggregate_rows.append(aggregate_row)

    metric_columns = [
        f"{metric}_{suffix}"
        for metric in METRICS
        for suffix in ("median", "min", "max")
    ]
    fieldnames = [
        "evidence_status",
        "experiment_spec_sha256",
        "phase",
        "input_len",
        "output_len",
        "concurrency",
        "num_prompts",
        "repetitions",
        "completed_total",
        "failed_total",
        *metric_columns,
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(aggregate_rows)

    print(f"Wrote {len(aggregate_rows)} rows to {args.output}")
    for row in aggregate_rows:
        print(
            f"in={row['input_len']} out={row['output_len']} "
            f"c={row['concurrency']} reps={row['repetitions']}: "
            f"output_tok_s={float(row['output_throughput_median']):.2f} "
            f"p99_ttft_ms={float(row['p99_ttft_ms_median']):.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
