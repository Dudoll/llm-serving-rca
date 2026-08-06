#!/usr/bin/env python3

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = PROJECT_ROOT / "results" / "summary" / "baseline-summary.csv"
OUTPUT_FILE = PROJECT_ROOT / "results" / "summary" / "baseline-aggregate.csv"

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
    return [float(row[key]) for row in rows if row.get(key, "") != ""]


def pct_change(current: float, baseline: float) -> float:
    return (current / baseline - 1.0) * 100.0


def main() -> int:
    with SUMMARY_FILE.open(newline="", encoding="utf-8") as input_file:
        source_rows = list(csv.DictReader(input_file))

    grouped: dict[tuple[int, int, int], list[dict[str, str]]] = defaultdict(list)
    for row in source_rows:
        grouped[
            (int(row["input_len"]), int(row["output_len"]), int(row["concurrency"]))
        ].append(row)

    aggregate_rows: list[dict[str, str | int | float]] = []
    by_workload: dict[tuple[int, int], list[tuple[int, list[dict[str, str]]]]] = defaultdict(list)
    for (input_len, output_len, concurrency), rows in grouped.items():
        by_workload[(input_len, output_len)].append((concurrency, rows))

    for (input_len, output_len), concurrency_groups in sorted(by_workload.items()):
        ordered_groups = sorted(concurrency_groups)
        c1_rows = next((rows for concurrency, rows in ordered_groups if concurrency == 1), None)
        if c1_rows is None:
            raise ValueError(f"Missing concurrency=1 control for input={input_len}, output={output_len}")

        c1_medians = {
            metric: statistics.median(values(c1_rows, metric)) for metric in METRICS
        }
        previous_medians: dict[str, float] | None = None

        for concurrency, rows in ordered_groups:
            row: dict[str, str | int | float] = {
                "input_len": input_len,
                "output_len": output_len,
                "concurrency": concurrency,
                "repetitions": len(rows),
                "completed_total": sum(int(item["completed"]) for item in rows),
                "failed_total": sum(int(item["failed"]) for item in rows),
            }

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

            for metric in METRICS:
                if metric not in medians:
                    continue
                current = medians[metric]
                baseline = c1_medians[metric]
                row[f"{metric}_gain_vs_c1_pct"] = (
                    "" if baseline == 0 else pct_change(current, baseline)
                )
                if previous_medians is None or previous_medians.get(metric, 0) == 0:
                    row[f"{metric}_gain_vs_previous_pct"] = ""
                else:
                    row[f"{metric}_gain_vs_previous_pct"] = pct_change(
                        current, previous_medians[metric]
                    )

            # Legacy aliases for output throughput gains.
            row["throughput_gain_vs_c1_pct"] = row.get("output_throughput_gain_vs_c1_pct", "")
            row["throughput_gain_vs_previous_pct"] = row.get(
                "output_throughput_gain_vs_previous_pct", ""
            )

            previous_medians = medians
            aggregate_rows.append(row)

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
