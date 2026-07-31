#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "results" / "raw"
TELEMETRY_DIR = PROJECT_ROOT / "results" / "telemetry"
OUTPUT_FILE = PROJECT_ROOT / "results" / "summary" / "gpu-telemetry-summary.csv"
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

FIELDS = {
    "utilization.gpu [%]": "gpu_util_pct",
    "utilization.memory [%]": "memory_util_pct",
    "memory.used [MiB]": "memory_used_mib",
    "clocks.current.sm [MHz]": "sm_clock_mhz",
    "clocks.current.memory [MHz]": "memory_clock_mhz",
    "temperature.gpu": "temperature_c",
}


def parse_number(value: str) -> float | None:
    match = re.search(r"[-+]?[0-9]*\.?[0-9]+", value)
    return float(match.group(0)) if match else None


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def benchmark_window(result: dict) -> tuple[datetime, datetime]:
    # vLLM's `date` is emitted at result time in UTC with second precision.
    end_utc = datetime.strptime(result["date"], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    end_local = end_utc.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
    start_local = end_local - timedelta(seconds=float(result["duration"]))
    # Include the final one-second nvidia-smi sample despite date rounding.
    return start_local, end_local + timedelta(seconds=1)


def main() -> int:
    output_rows: list[dict[str, str | int | float]] = []

    for result_path in sorted(RAW_DIR.glob("baseline-in*-out*-c*-r*.json")):
        result = json.loads(result_path.read_text(encoding="utf-8"))
        run_id = result["run_id"]
        telemetry_path = TELEMETRY_DIR / f"{run_id}-gpu.csv"
        if not telemetry_path.exists():
            raise FileNotFoundError(f"Missing GPU telemetry for {run_id}: {telemetry_path}")

        window_start, window_end = benchmark_window(result)
        samples: dict[str, list[float]] = {name: [] for name in FIELDS.values()}
        sample_times: list[datetime] = []

        with telemetry_path.open(newline="", encoding="utf-8") as input_file:
            reader = csv.DictReader(input_file)
            for raw_row in reader:
                row = {key.strip(): value.strip() for key, value in raw_row.items()}
                sample_time = datetime.strptime(row["timestamp"], "%Y/%m/%d %H:%M:%S.%f")
                if not (window_start <= sample_time <= window_end):
                    continue
                sample_times.append(sample_time)
                for source_name, output_name in FIELDS.items():
                    value = parse_number(row[source_name])
                    if value is not None:
                        samples[output_name].append(value)

        if not sample_times:
            raise ValueError(
                f"No GPU samples aligned with benchmark window for {run_id}: "
                f"{window_start.isoformat()}..{window_end.isoformat()}"
            )

        output_row: dict[str, str | int | float] = {
            "run_id": run_id,
            "input_len": int(result["input_len"]),
            "output_len": int(result["output_len"]),
            "concurrency": int(result["concurrency"]),
            "repetition": int(result["repetition"]),
            "benchmark_duration_s": float(result["duration"]),
            "sample_count": len(sample_times),
            "sample_coverage_s": (max(sample_times) - min(sample_times)).total_seconds(),
            "window_start_local": window_start.isoformat(timespec="milliseconds"),
            "window_end_local": window_end.isoformat(timespec="milliseconds"),
        }
        for metric_name, metric_values in samples.items():
            output_row[f"{metric_name}_mean"] = sum(metric_values) / len(metric_values)
            output_row[f"{metric_name}_p50"] = percentile(metric_values, 0.50)
            output_row[f"{metric_name}_p95"] = percentile(metric_values, 0.95)
            output_row[f"{metric_name}_min"] = min(metric_values)
            output_row[f"{metric_name}_max"] = max(metric_values)

        output_rows.append(output_row)

    if not output_rows:
        raise ValueError("No baseline result files found")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(output_rows[0].keys()))
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Wrote {len(output_rows)} rows to {OUTPUT_FILE}")
    for row in output_rows:
        if int(row["input_len"]) != 512 or int(row["output_len"]) != 128:
            continue
        print(
            f"{row['run_id']}: samples={row['sample_count']} "
            f"gpu_mean={float(row['gpu_util_pct_mean']):.1f}% "
            f"gpu_p95={float(row['gpu_util_pct_p95']):.1f}% "
            f"sm_clock_p50={float(row['sm_clock_mhz_p50']):.0f}MHz "
            f"temp_max={float(row['temperature_c_max']):.0f}C"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
