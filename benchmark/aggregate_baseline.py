#!/usr/bin/env python3
"""Collapse per-run baseline rows into one row per (input, output, concurrency).

Pipeline:
  results/raw/*.json
    -> summarize_results.py  -> baseline-summary.csv   (one row per run / repetition)
    -> aggregate_baseline.py -> baseline-aggregate.csv (median/min/max across reps)

Reports like reports/baseline.md use the aggregate file (median across 5 reps).
"""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path


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
    """Collect one metric from all repetition rows, skipping empty cells."""
    return [float(row[key]) for row in rows if row.get(key, "") != ""]


def pct_change(current: float, baseline: float) -> float:
    """Percent change: ((current / baseline) - 1) * 100."""
    return (current / baseline - 1.0) * 100.0


def main() -> int:
    # --- 1. Load per-run summary (e.g. 30 rows for 6 concurrencies x 5 reps) ---
    with SUMMARY_FILE.open(newline="", encoding="utf-8") as input_file:
        source_rows = list(csv.DictReader(input_file))

    # --- 2. Group repetitions that share the same workload + concurrency ---
    # Key: (input_len, output_len, concurrency) -> [row_r1, row_r2, ...]
    grouped: dict[tuple[int, int, int], list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        grouped[
            (int(row["input_len"]), int(row["output_len"]), int(row["concurrency"]))
        ].append(row)

    # --- 3. Re-bucket by workload so we can walk concurrencies in order ---
    # Needed for "gain vs previous concurrency" (c1->c2, c2->c4, ...).
    aggregate_rows: list[dict[str, str | int | float]] = []
    by_workload: dict[tuple[int, int], list[tuple[int, list[dict[str, str]]]]] = defaultdict(list)
    for (input_len, output_len, concurrency), rows in grouped.items():
        by_workload[(input_len, output_len)].append((concurrency, rows))

    for (input_len, output_len), concurrency_groups in sorted(by_workload.items()):
        ordered_groups = sorted(concurrency_groups)  # sort by concurrency ascending

        # c1 is the control: every other concurrency's gain_vs_c1 is relative to this.
        c1_rows = next((rows for concurrency, rows in ordered_groups if concurrency == 1), None)
        if c1_rows is None:
            raise ValueError(f"Missing concurrency=1 control for input={input_len}, output={output_len}")

        c1_medians = {
            metric: statistics.median(values(c1_rows, metric)) for metric in METRICS
        }
        previous_medians: dict[str, float] | None = None

        for concurrency, rows in ordered_groups:
            # One aggregate row for this concurrency point.
            row: dict[str, str | int | float] = {
                "input_len": input_len,
                "output_len": output_len,
                "concurrency": concurrency,
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
            row["throughput_gain_vs_c1_pct"] = row.get("output_throughput_gain_vs_c1_pct", "")
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
        "input_len",
        "output_len",
        "concurrency",
        "repetitions",
        "completed_total",
        "failed_total",
        *metric_columns,
        *gain_columns,
        *LEGACY_THROUGHPUT_GAIN_FIELDS,
    ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(aggregate_rows)

    # Quick console check: throughput and P99 TTFT at each concurrency.
    print(f"Wrote {len(aggregate_rows)} rows to {OUTPUT_FILE}")
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
