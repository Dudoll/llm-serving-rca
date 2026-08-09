#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "results" / "raw"
SUMMARY_DIR = PROJECT_ROOT / "results" / "summary"

BASE_FIELDS = [
    "run_id",
    "input_len",
    "output_len",
    "concurrency",
    "repetition",
    "completed",
    "failed",
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
]

# Percent change vs concurrency=1 and vs previous concurrency (same workload + repetition).
GAIN_METRICS = (
    "request_throughput",
    "output_throughput",
    "p50_ttft_ms",
    "p95_ttft_ms",
    "p99_ttft_ms",
    "p99_tpot_ms",
    "p99_itl_ms",
    "p99_e2el_ms",
)

GAIN_FIELDS = [
    field
    for metric in GAIN_METRICS
    for field in (f"{metric}_gain_vs_c1_pct", f"{metric}_gain_vs_previous_pct")
]

FIELDS = [*BASE_FIELDS, *GAIN_FIELDS]


def metadata_from(result: dict) -> dict:
    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    return {}


def pct_change(current: float, baseline: float) -> float:
    return (current / baseline - 1.0) * 100.0


def summarize_file(path: Path) -> dict | None:
    result = json.loads(path.read_text(encoding="utf-8"))
    if "request_throughput" not in result:
        return None

    metadata = metadata_from(result)
    row = {field: result.get(field, "") for field in BASE_FIELDS}
    if not row["input_throughput"] and result.get("duration"):
        row["input_throughput"] = result.get("total_input_tokens", 0) / result["duration"]
    row["run_id"] = metadata.get("run_id", result.get("run_id", path.stem))
    for key in ("input_len", "output_len", "concurrency", "repetition"):
        row[key] = metadata.get(key, result.get(key, row.get(key, "")))
    return row


def add_percent_gains(rows: list[dict]) -> None:
    grouped: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[
            (int(row["input_len"]), int(row["output_len"]), int(row["repetition"]))
        ].append(row)

    for group_rows in grouped.values():
        ordered = sorted(group_rows, key=lambda item: int(item["concurrency"]))
        c1 = next((item for item in ordered if int(item["concurrency"]) == 1), None)
        previous: dict | None = None

        for row in ordered:
            for metric in GAIN_METRICS:
                current = float(row[metric])
                if c1 is None or float(c1[metric]) == 0:
                    row[f"{metric}_gain_vs_c1_pct"] = ""
                else:
                    row[f"{metric}_gain_vs_c1_pct"] = pct_change(current, float(c1[metric]))

                if previous is None or float(previous[metric]) == 0:
                    row[f"{metric}_gain_vs_previous_pct"] = ""
                else:
                    row[f"{metric}_gain_vs_previous_pct"] = pct_change(
                        current, float(previous[metric])
                    )
            previous = row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize the vLLM bench JSON runs into a CSV"
    )

    parser.add_argument(
        "--pattern",
        default="baseline-*.json",
        help="Glob under results/raw/ (default: baseline-*.json)"
    )

    parser.add_argument(
        "--output",
        default="baseline-summary.csv",
        help="Output CSV file (default: baseline-summary.csv)",
    )

    parser.add_argument(
        "--no-gains",
        action="store_true",
        default=False,
        help="Don't calculate percent gains"
    )

    return parser.parse_args()

def main() -> int:
    args = parse_args()
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(RAW_DIR.glob(args.pattern)):
        row = summarize_file(path)
        if row:
            rows.append(row)

    if not args.no_gains:
        add_percent_gains(rows)
        fieldnames = FIELDS
    else:
        fieldnames = BASE_FIELDS

    output_path = SUMMARY_DIR / args.output
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")
    for row in rows:
        print(
            f"{row['run_id']}: "
            f"output_tok_s={row['output_throughput']} "
            f"p99_ttft_ms={row['p99_ttft_ms']} "
            f"p99_tpot_ms={row['p99_tpot_ms']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
