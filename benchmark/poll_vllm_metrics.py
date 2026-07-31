#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen


SELECTED_METRICS = {
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:num_requests_waiting_by_reason",
    "vllm:kv_cache_usage_perc",
    "vllm:num_preemptions_total",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:request_queue_time_seconds_sum",
    "vllm:request_queue_time_seconds_count",
    "vllm:request_prefill_time_seconds_sum",
    "vllm:request_prefill_time_seconds_count",
    "vllm:request_decode_time_seconds_sum",
    "vllm:request_decode_time_seconds_count",
}

SAMPLE_RE = re.compile(r"^(?P<name>[^\s{]+)(?P<labels>\{[^}]*\})?\s+(?P<value>[^\s]+)$")
STOP_REQUESTED = False


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def parse_metrics(payload: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for line in payload.splitlines():
        if not line or line.startswith("#"):
            continue
        match = SAMPLE_RE.match(line)
        if match is None or match.group("name") not in SELECTED_METRICS:
            continue
        sample_name = match.group("name") + (match.group("labels") or "")
        metrics[sample_name] = float(match.group("value"))
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll selected vLLM Prometheus metrics")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    if args.interval <= 0 or args.timeout <= 0:
        parser.error("--interval and --timeout must be positive")

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    next_poll = time.monotonic()
    with args.output.open("w", encoding="utf-8", buffering=1) as output_file:
        while not STOP_REQUESTED:
            monotonic_start = time.monotonic()
            record: dict[str, object] = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "monotonic_s": monotonic_start,
            }
            try:
                with urlopen(args.url, timeout=args.timeout) as response:
                    payload = response.read().decode("utf-8")
                record["metrics"] = parse_metrics(payload)
            except Exception as error:  # Preserve polling failures in the evidence stream.
                record["error"] = f"{type(error).__name__}: {error}"
            output_file.write(json.dumps(record, separators=(",", ":")) + "\n")

            next_poll += args.interval
            sleep_time = next_poll - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_poll = time.monotonic()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
