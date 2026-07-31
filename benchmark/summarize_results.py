#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "results" / "raw"
SUMMARY_DIR = PROJECT_ROOT / "results" / "summary"

FIELDS = [
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


def metadata_from(result: dict) -> dict:
    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    return {}


def summarize_file(path: Path) -> dict | None:
    result = json.loads(path.read_text(encoding="utf-8"))
    if "request_throughput" not in result:
        return None

    metadata = metadata_from(result)
    row = {field: result.get(field, "") for field in FIELDS}
    if not row["input_throughput"] and result.get("duration"):
        row["input_throughput"] = result.get("total_input_tokens", 0) / result["duration"]
    row["run_id"] = metadata.get("run_id", result.get("run_id", path.stem))
    for key in ("input_len", "output_len", "concurrency", "repetition"):
        row[key] = metadata.get(key, result.get(key, row.get(key, "")))
    return row


def main() -> int:
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(RAW_DIR.glob("baseline-*.json")):
        row = summarize_file(path)
        if row:
            rows.append(row)

    output_path = SUMMARY_DIR / "baseline-summary.csv"
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=FIELDS)
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
