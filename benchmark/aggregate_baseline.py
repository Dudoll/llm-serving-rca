#!/usr/bin/env python3
"""Collapse per-run baseline rows into one row per (input, output, concurrency).

Pipeline:
  results/raw/*.json
    -> summarize_results.py  -> baseline-summary.csv   (one row per run / repetition)
    -> aggregate_baseline.py -> baseline-aggregate.csv (median/min/max across reps)

Reports like reports/baseline.md use the aggregate file (median across 5 reps).
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
SUMMARY_FILE = PROJECT_ROOT / "results" / "summary" / "baseline-summary.csv"
OUTPUT_FILE = PROJECT_ROOT / "results" / "summary" / "baseline-aggregate.csv"

# Metrics we summarize across the 5 repetitions of each concurrency point.
METRICS = (
    "request_throughput",
    "output_throughput",
    "p50_ttft_ms",
    "p95_ttft_ms",
    "p99_ttft_ms",
    "p99_tpot_ms",
    "p99_itl_ms",
    "p99_e2el_ms",
)

# Keep legacy aliases used by reports/README.
LEGACY_THROUGHPUT_GAIN_FIELDS = (
    "throughput_gain_vs_c1_pct",
    "throughput_gain_vs_previous_pct",
)


def values(rows: list[dict[str, str]], key: str) -> list[float]:
    """Collect one required, finite, non-negative metric from every repetition."""
    missing = [row.get("run_id", "<unknown>") for row in rows if not row.get(key)]
    if missing:
        raise ValueError(f"Missing metric {key!r} in runs: {missing}")
    parsed = [float(row[key]) for row in rows]
    if any(not math.isfinite(value) or value < 0 for value in parsed):
        raise ValueError(f"Metric {key!r} must be finite and non-negative")
    return parsed


def pct_change(current: float, baseline: float) -> float:
    """Percent change: ((current / baseline) - 1) * 100."""
    return (current / baseline - 1.0) * 100.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=SUMMARY_FILE)
    parser.add_argument("--output", type=Path, default=OUTPUT_FILE)
    parser.add_argument(
        "--allow-legacy-unmanifested",
        action="store_true",
        help="Explicitly aggregate historical summary rows without manifests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # --- 1. Load per-run summary (e.g. 30 rows for 6 concurrencies x 5 reps) ---
    with args.input.open(newline="", encoding="utf-8") as input_file:
        source_rows = list(csv.DictReader(input_file))
    validate_summary_evidence_rows(
        source_rows,
        allow_legacy_unmanifested=args.allow_legacy_unmanifested,
    )

    # --- 2. Group repetitions that share the same workload + concurrency ---
    # Key: (input_len, output_len, concurrency) -> [row_r1, row_r2, ...]
    grouped: dict[tuple[str, str, int, int, int, int], list[dict[str, str]]] = (
        defaultdict(list)
    )
    for row in source_rows:
        grouped[
            (
                row["experiment_spec_sha256"],
                row["phase"],
                int(row["input_len"]),
                int(row["output_len"]),
                int(row["concurrency"]),
                int(row["num_prompts"]),
            )
        ].append(row)

    # --- 3. Re-bucket by workload so we can walk concurrencies in order ---
    # Needed for "gain vs previous concurrency" (c1->c2, c2->c4, ...).
    aggregate_rows: list[dict[str, str | int | float]] = []
    by_workload: dict[
        tuple[str, str, int, int], list[tuple[int, int, list[dict[str, str]]]]
    ] = defaultdict(list)
    for (
        experiment_spec,
        phase,
        input_len,
        output_len,
        concurrency,
        num_prompts,
    ), rows in grouped.items():
        by_workload[(experiment_spec, phase, input_len, output_len)].append(
            (concurrency, num_prompts, rows)
        )

    for (experiment_spec, phase, input_len, output_len), concurrency_groups in sorted(
        by_workload.items()
    ):
        ordered_groups = sorted(concurrency_groups)  # sort by concurrency ascending

        # c1 is the control: every other concurrency's gain_vs_c1 is relative to this.
        c1_rows = next(
            (
                rows
                for concurrency, _num_prompts, rows in ordered_groups
                if concurrency == 1
            ),
            None,
        )
        if c1_rows is None:
            raise ValueError(
                f"Missing concurrency=1 control for input={input_len}, output={output_len}"
            )

        c1_medians = {
            metric: statistics.median(values(c1_rows, metric)) for metric in METRICS
        }
        previous_medians: dict[str, float] | None = None

        for concurrency, num_prompts, rows in ordered_groups:
            repetitions = [int(item["repetition"]) for item in rows]
            if len(repetitions) != len(set(repetitions)):
                raise ValueError(
                    f"Duplicate repetitions for {(phase, input_len, output_len, concurrency)}"
                )
            if any(int(item["failed"]) != 0 for item in rows):
                raise ValueError("Failed requests cannot enter the baseline aggregate")
            statuses = {item["evidence_status"] for item in rows}
            if len(statuses) != 1:
                raise ValueError("A baseline group cannot mix evidence provenance")
            # One aggregate row for this concurrency point.
            row: dict[str, str | int | float] = {
                "evidence_status": rows[0]["evidence_status"],
                "experiment_spec_sha256": experiment_spec,
                "phase": phase,
                "input_len": input_len,
                "output_len": output_len,
                "concurrency": concurrency,
                "num_prompts": num_prompts,
                "repetitions": len(rows),
                "completed_total": sum(int(item["completed"]) for item in rows),
                "failed_total": sum(int(item["failed"]) for item in rows),
            }

            # --- 4. Across repetitions: median (headline), min/max (spread) ---
            medians: dict[str, float] = {}
            for metric in METRICS:
                metric_values = values(rows, metric)
                if not metric_values:
                    continue
                median = statistics.median(metric_values)
                medians[metric] = median
                row[f"{metric}_median"] = median
                row[f"{metric}_min"] = min(metric_values)
                row[f"{metric}_max"] = max(metric_values)

            # --- 5. Relative gains: vs c1, and vs the previous concurrency step ---
            for metric in METRICS:
                if metric not in medians:
                    continue
                current = medians[metric]
                baseline = c1_medians[metric]
                row[f"{metric}_gain_vs_c1_pct"] = (
                    "" if baseline == 0 else pct_change(current, baseline)
                )
                if previous_medians is None or previous_medians.get(metric, 0) == 0:
                    # First concurrency in the sweep (usually c1): no "previous".
                    row[f"{metric}_gain_vs_previous_pct"] = ""
                else:
                    row[f"{metric}_gain_vs_previous_pct"] = pct_change(
                        current, previous_medians[metric]
                    )

            # Shorter names kept so older report text can still refer to them.
            row["throughput_gain_vs_c1_pct"] = row.get(
                "output_throughput_gain_vs_c1_pct", ""
            )
            row["throughput_gain_vs_previous_pct"] = row.get(
                "output_throughput_gain_vs_previous_pct", ""
            )

            previous_medians = medians
            aggregate_rows.append(row)

    # --- 6. Write CSV with a stable column order ---
    metric_columns = [
        f"{metric}_{suffix}"
        for metric in METRICS
        for suffix in ("median", "min", "max")
    ]
    gain_columns = [
        field
        for metric in METRICS
        for field in (f"{metric}_gain_vs_c1_pct", f"{metric}_gain_vs_previous_pct")
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
        *gain_columns,
        *LEGACY_THROUGHPUT_GAIN_FIELDS,
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(aggregate_rows)

    # Quick console check: throughput and P99 TTFT at each concurrency.
    print(f"Wrote {len(aggregate_rows)} rows to {args.output}")
    for row in aggregate_rows:
        print(
            f"in={row['input_len']} out={row['output_len']} c={row['concurrency']}: "
            f"output_tok_s={float(row['output_throughput_median']):.2f} "
            f"({row['output_throughput_gain_vs_previous_pct']}% vs prev) "
            f"p99_ttft_ms={float(row['p99_ttft_ms_median']):.2f} "
            f"({row['p99_ttft_ms_gain_vs_previous_pct']}% vs prev)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
