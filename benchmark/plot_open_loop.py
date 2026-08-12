#!/usr/bin/env python3
"""Plot the finite open-loop boundary scan and its queue/KV evidence."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_DIR = PROJECT_ROOT / "results/summary"
DEFAULT_AGGREGATE = SUMMARY_DIR / "open-loop-aggregate.csv"
DEFAULT_RUN_SUMMARY = SUMMARY_DIR / "open-loop-summary.csv"
DEFAULT_TELEMETRY = SUMMARY_DIR / "open-loop-telemetry-summary.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "charts/open-loop-saturation.png"


def finite_number(raw_value: str, context: str) -> float:
    value = float(raw_value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{context} must be finite and non-negative")
    return value


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))
    if not rows:
        raise ValueError(f"{path} contains no rows")
    return rows


def telemetry_by_rate(
    run_rows: list[dict[str, str]], telemetry_rows: list[dict[str, str]]
) -> dict[float, list[dict[str, str]]]:
    rate_by_run = {
        row["run_id"]: finite_number(row["request_rate"], f"{row['run_id']} rate")
        for row in run_rows
    }
    grouped: dict[float, list[dict[str, str]]] = defaultdict(list)
    for row in telemetry_rows:
        run_id = row["run_id"]
        if run_id not in rate_by_run:
            raise ValueError(f"Telemetry run {run_id} is absent from the run summary")
        grouped[rate_by_run[run_id]].append(row)
    return grouped


def median_and_range(
    rows: list[dict[str, str]], field: str
) -> tuple[float, float, float]:
    values = [finite_number(row[field], f"{row['run_id']} {field}") for row in rows]
    return statistics.median(values), min(values), max(values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate", type=Path, default=DEFAULT_AGGREGATE)
    parser.add_argument("--run-summary", type=Path, default=DEFAULT_RUN_SUMMARY)
    parser.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    import matplotlib.pyplot as plt

    aggregate_rows = sorted(
        read_rows(args.aggregate), key=lambda row: float(row["request_rate"])
    )
    run_rows = read_rows(args.run_summary)
    telemetry_rows = read_rows(args.telemetry)
    grouped_telemetry = telemetry_by_rate(run_rows, telemetry_rows)

    rates = [
        finite_number(row["request_rate"], "request_rate") for row in aggregate_rows
    ]
    completed = [
        finite_number(row["request_throughput_median"], "completed throughput")
        for row in aggregate_rows
    ]
    completed_min = [
        finite_number(row["request_throughput_min"], "completed throughput min")
        for row in aggregate_rows
    ]
    completed_max = [
        finite_number(row["request_throughput_max"], "completed throughput max")
        for row in aggregate_rows
    ]

    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    throughput_axis, latency_axis, queue_axis, cache_axis = axes.flat

    throughput_axis.plot(rates, rates, linestyle="--", color="0.55", label="ideal y=x")
    throughput_axis.fill_between(
        rates, completed_min, completed_max, color="#4C78A8", alpha=0.18
    )
    throughput_axis.plot(
        rates, completed, marker="o", color="#4C78A8", label="completed median"
    )
    throughput_axis.set(title="Offered vs completed", ylabel="requests/s")
    throughput_axis.legend()

    latency_axis.plot(
        rates,
        [float(row["p99_ttft_ms_median"]) for row in aggregate_rows],
        marker="o",
        label="P99 TTFT",
        color="#F58518",
    )
    latency_axis.plot(
        rates,
        [float(row["p99_e2el_ms_median"]) for row in aggregate_rows],
        marker="s",
        label="P99 E2E",
        color="#E45756",
    )
    latency_axis.set(title="Tail latency", ylabel="milliseconds")
    latency_axis.legend()

    waiting_stats = [
        median_and_range(grouped_telemetry[rate], "max_waiting") for rate in rates
    ]
    queue_axis.fill_between(
        rates,
        [item[1] for item in waiting_stats],
        [item[2] for item in waiting_stats],
        color="#72B7B2",
        alpha=0.2,
    )
    queue_axis.plot(
        rates,
        [item[0] for item in waiting_stats],
        marker="o",
        color="#54A24B",
    )
    queue_axis.set(title="Maximum waiting requests", ylabel="requests")

    kv_medians = [
        median_and_range(grouped_telemetry[rate], "kv_cache_usage_max_pct")[0]
        for rate in rates
    ]
    preemption_totals = [
        sum(
            finite_number(row["preemptions_delta"], "preemptions_delta")
            for row in grouped_telemetry[rate]
        )
        for rate in rates
    ]
    cache_axis.plot(
        rates, kv_medians, marker="o", color="#B279A2", label="KV max median"
    )
    cache_axis.set(title="Cache pressure and preemption", ylabel="KV cache usage (%)")
    preemption_axis = cache_axis.twinx()
    preemption_axis.bar(
        rates,
        preemption_totals,
        width=0.65,
        alpha=0.22,
        color="#E45756",
        label="preemptions (5 runs)",
    )
    preemption_axis.set_ylabel("preemptions across 5 runs")
    handles_left, labels_left = cache_axis.get_legend_handles_labels()
    handles_right, labels_right = preemption_axis.get_legend_handles_labels()
    cache_axis.legend(handles_left + handles_right, labels_left + labels_right)

    for axis in axes.flat:
        axis.set_xlabel("configured request rate (requests/s)")
        axis.grid(alpha=0.2)
    figure.suptitle(
        "Finite Open-Loop Boundary Scan — 256 requests/run, 5 repetitions\n"
        "Boundary evidence only; not a sustainable-capacity claim",
        fontsize=14,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=160)
    plt.close(figure)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
