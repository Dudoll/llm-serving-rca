#!/usr/bin/env python3

from __future__ import annotations

import argparse
import http.client
import json
import statistics
import time
from pathlib import Path
from urllib.parse import urlparse


def run_request(base_url: str, model: str, prompt: str, max_tokens: int) -> dict:
    parsed = urlparse(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=120)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    request_start = time.perf_counter()
    connection.request(
        "POST",
        "/v1/chat/completions",
        body=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    if response.status != 200:
        body = response.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {response.status}: {body}")

    first_content_time = None
    content_event_times: list[float] = []
    completion_tokens = None
    content_parts: list[str] = []

    while True:
        raw_line = response.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break

        event = json.loads(data)
        usage = event.get("usage")
        if usage:
            completion_tokens = usage.get("completion_tokens")

        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if content:
            now = time.perf_counter()
            if first_content_time is None:
                first_content_time = now
            content_event_times.append(now)
            content_parts.append(content)

    request_end = time.perf_counter()
    connection.close()

    if first_content_time is None:
        raise RuntimeError("No streamed content was received")

    ttft_ms = (first_content_time - request_start) * 1000
    e2e_ms = (request_end - request_start) * 1000
    tpot_ms = None
    if completion_tokens and completion_tokens > 1:
        tpot_ms = (request_end - first_content_time) * 1000 / (completion_tokens - 1)

    return {
        "ttft_ms": ttft_ms,
        "tpot_ms": tpot_ms,
        "e2e_ms": e2e_ms,
        "completion_tokens": completion_tokens,
        "stream_content_events": len(content_event_times),
        "text": "".join(content_parts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal streaming latency client")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--prompt", default="Explain virtual memory in one sentence.")
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    results = []
    for repetition in range(1, args.repetitions + 1):
        result = run_request(args.base_url, args.model, args.prompt, args.max_tokens)
        result["repetition"] = repetition
        results.append(result)
        print(json.dumps(result, ensure_ascii=False))

    summary = {
        "repetitions": args.repetitions,
        "median_ttft_ms": statistics.median(item["ttft_ms"] for item in results),
        "median_e2e_ms": statistics.median(item["e2e_ms"] for item in results),
        "median_tpot_ms": statistics.median(
            item["tpot_ms"] for item in results if item["tpot_ms"] is not None
        ),
        "results": results,
    }
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

