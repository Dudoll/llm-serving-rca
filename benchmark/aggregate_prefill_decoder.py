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

import csv
import statistics
from collections import defaultdict
from pathlib import Path


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
    """Return non-empty values for one metric as floats."""
    return [float(row[key]) for row in rows if row.get(key, "") != ""]


def main() -> int:
    with SUMMARY_FILE.open(newline="", encoding="utf-8") as input_file:
        source_rows = list(csv.DictReader(input_file))

    grouped: dict[tuple[int, int, int], list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        key = (
            int(row["input_len"]),
            int(row["output_len"]),
            int(row["concurrency"]),
        )
        grouped[key].append(row)

    aggregate_rows: list[dict[str, str | int | float]] = []
    for (input_len, output_len, concurrency), rows in sorted(grouped.items()):
        aggregate_row: dict[str, str | int | float] = {
            "input_len": input_len,
            "output_len": output_len,
            "concurrency": concurrency,
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
        "input_len",
        "output_len",
        "concurrency",
        "repetitions",
        "completed_total",
        "failed_total",
        *metric_columns,
    ]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(aggregate_rows)

    print(f"Wrote {len(aggregate_rows)} rows to {OUTPUT_FILE}")
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
