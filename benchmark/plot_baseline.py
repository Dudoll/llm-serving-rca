#!/usr/bin/env python3

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_FILE = PROJECT_ROOT / "results" / "summary" / "baseline-summary.csv"
CHART_DIR = PROJECT_ROOT / "charts"


def numeric(row: dict, key: str) -> float:
    value = row.get(key, "")
    return float(value) if value not in (None, "") else float("nan")


def median_and_range(rows: list[dict], key: str) -> tuple[float, float, float]:
    values = [numeric(row, key) for row in rows if int(row.get("failed", "0")) == 0]
    if not values:
        raise ValueError(f"No successful values for {key}")
    median = statistics.median(values)
    return median, median - min(values), max(values) - median


def main() -> int:
    with SUMMARY_FILE.open(newline="", encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))

    grouped: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["input_len"]), int(row["output_len"]))].append(row)

    CHART_DIR.mkdir(parents=True, exist_ok=True)
    for (input_len, output_len), workload_rows in grouped.items():
        by_concurrency: dict[int, list[dict]] = defaultdict(list)
        for row in workload_rows:
            by_concurrency[int(row["concurrency"])].append(row)

        concurrencies = sorted(by_concurrency)
        throughput_stats = [
            median_and_range(by_concurrency[c], "output_throughput")
            for c in concurrencies
        ]
        output_throughput = [item[0] for item in throughput_stats]
        throughput_error = [
            [item[1] for item in throughput_stats],
            [item[2] for item in throughput_stats],
        ]

        plt.figure(figsize=(7.2, 4.5))
        plt.errorbar(
            concurrencies,
            output_throughput,
            yerr=throughput_error,
            marker="o",
            capsize=4,
            label="Median (min-max across repetitions)",
        )
        plt.xlabel("Maximum concurrency")
        plt.ylabel("Output tokens/s")
        plt.title(f"Throughput: input={input_len}, output={output_len}")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        throughput_path = CHART_DIR / f"baseline-in{input_len}-out{output_len}-throughput.png"
        plt.savefig(throughput_path, dpi=160)
        plt.close()

        plt.figure(figsize=(7.2, 4.5))
        for key, label in (
            ("p50_ttft_ms", "P50 TTFT"),
            ("p95_ttft_ms", "P95 TTFT"),
            ("p99_ttft_ms", "P99 TTFT"),
        ):
            stats = [median_and_range(by_concurrency[c], key) for c in concurrencies]
            values = [item[0] for item in stats]
            errors = [[item[1] for item in stats], [item[2] for item in stats]]
            plt.errorbar(
                concurrencies,
                values,
                yerr=errors,
                marker="o",
                capsize=3,
                label=label,
            )
        plt.xlabel("Maximum concurrency")
        plt.ylabel("Latency (ms)")
        plt.title(f"TTFT: input={input_len}, output={output_len}")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        ttft_path = CHART_DIR / f"baseline-in{input_len}-out{output_len}-ttft.png"
        plt.savefig(ttft_path, dpi=160)
        plt.close()

        print(f"Wrote {throughput_path}")
        print(f"Wrote {ttft_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
