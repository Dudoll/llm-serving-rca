#!/usr/bin/env python3
"""Gate a candidate aggregate against explicit throughput and latency budgets."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def read_one(path: Path, selectors: dict[str, str]) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as input_file:
        rows = list(csv.DictReader(input_file))
    matches = [
        row
        for row in rows
        if all(
            str(row.get(field, "")) == expected for field, expected in selectors.items()
        )
    ]
    if len(matches) != 1:
        raise ValueError(
            f"{path} matched {len(matches)} rows for selectors {selectors}; expected exactly one"
        )
    return matches[0]


def finite_metric(row: dict[str, str], field: str) -> float:
    raw = row.get(field, "")
    if raw in ("", None):
        raise ValueError(f"Missing metric {field!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"Metric {field!r} is not finite: {raw!r}")
    return value


def evaluate(
    baseline: dict[str, str],
    candidate: dict[str, str],
    *,
    throughput_field: str,
    latency_field: str,
    max_throughput_regression_pct: float,
    max_latency_regression_pct: float,
) -> dict[str, object]:
    baseline_throughput = finite_metric(baseline, throughput_field)
    candidate_throughput = finite_metric(candidate, throughput_field)
    baseline_latency = finite_metric(baseline, latency_field)
    candidate_latency = finite_metric(candidate, latency_field)
    if baseline_throughput <= 0 or baseline_latency <= 0:
        raise ValueError("Baseline throughput and latency must be positive")

    throughput_change_pct = (candidate_throughput / baseline_throughput - 1.0) * 100.0
    latency_change_pct = (candidate_latency / baseline_latency - 1.0) * 100.0
    failures = int(candidate.get("failed_total", candidate.get("failed", "0")) or 0)
    checks = {
        "zero_failures": failures == 0,
        "throughput_budget": throughput_change_pct >= -max_throughput_regression_pct,
        "latency_budget": latency_change_pct <= max_latency_regression_pct,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "throughput_change_pct": throughput_change_pct,
        "latency_change_pct": latency_change_pct,
        "candidate_failures": failures,
    }


def non_negative_finite(raw: str) -> float:
    value = float(raw)
    if value < 0 or not math.isfinite(value):
        raise argparse.ArgumentTypeError("value must be non-negative and finite")
    return value


def parse_selector(raw: str) -> tuple[str, str]:
    field, separator, value = raw.partition("=")
    if not separator or not field or not value:
        raise argparse.ArgumentTypeError("selector must use FIELD=VALUE")
    return field, value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check an aggregate performance regression"
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--select",
        type=parse_selector,
        action="append",
        default=[],
        metavar="FIELD=VALUE",
    )
    parser.add_argument("--throughput-field", default="output_throughput_median")
    parser.add_argument("--latency-field", default="p99_ttft_ms_median")
    parser.add_argument(
        "--max-throughput-regression-pct", type=non_negative_finite, required=True
    )
    parser.add_argument(
        "--max-latency-regression-pct", type=non_negative_finite, required=True
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selectors = dict(args.select)
    result = evaluate(
        read_one(args.baseline, selectors),
        read_one(args.candidate, selectors),
        throughput_field=args.throughput_field,
        latency_field=args.latency_field,
        max_throughput_regression_pct=args.max_throughput_regression_pct,
        max_latency_regression_pct=args.max_latency_regression_pct,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
