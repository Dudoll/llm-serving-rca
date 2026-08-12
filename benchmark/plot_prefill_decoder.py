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
SUMMARY_FILE = PROJECT_ROOT / "results" / "summary" / "prefill-decoder-summary.csv"
CHART_FILE = PROJECT_ROOT / "charts" / "prefill-decoder-matrix.png"


def stats(rows: list[dict[str, str]], field: str) -> tuple[float, float, float]:
    values = [float(row[field]) for row in rows if int(row["failed"]) == 0]
    if not values:
        raise ValueError(f"No successful values for {field}")
    median = statistics.median(values)
    return median, median - min(values), max(values) - median


def main() -> int:
    with SUMMARY_FILE.open(newline="", encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))

    grouped: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["input_len"]), int(row["output_len"]))].append(row)

    case_order = {
        (128, 32): "A",
        (1536, 32): "B",
        (128, 256): "C",
        (1536, 256): "D",
    }
    ordered = sorted(grouped.items(), key=lambda item: case_order[item[0]])
    labels = [
        f"{case_order[(input_len, output_len)]}\n{input_len}/{output_len}"
        for (input_len, output_len), _ in ordered
    ]

    panels = (
        ("output_throughput", "Output tokens/s", "Output throughput"),
        ("p99_ttft_ms", "Milliseconds", "P99 TTFT"),
        ("p99_tpot_ms", "Milliseconds", "P99 TPOT"),
        ("p99_e2el_ms", "Milliseconds", "P99 E2E"),
    )

    figure, axes = plt.subplots(2, 2, figsize=(9.2, 7.0))
    for axis, (field, ylabel, title) in zip(axes.flat, panels):
        values: list[float] = []
        lower_errors: list[float] = []
        upper_errors: list[float] = []
        for _, workload_rows in ordered:
            median, lower, upper = stats(workload_rows, field)
            values.append(median)
            lower_errors.append(lower)
            upper_errors.append(upper)

        axis.errorbar(
            range(len(values)),
            values,
            yerr=[lower_errors, upper_errors],
            marker="o",
            capsize=4,
            linewidth=1.5,
        )
        axis.set_xticks(range(len(labels)), labels)
        axis.set_ylabel(ylabel)
        axis.set_title(title)
        axis.grid(True, alpha=0.3)

    figure.suptitle("Prefill/Decode workload matrix (concurrency=8)")
    figure.tight_layout()
    CHART_FILE.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(CHART_FILE, dpi=160)
    plt.close(figure)
    print(f"Wrote {CHART_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
