from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "benchmark"))

import aggregate_telemetry  # noqa: E402


def metrics_sample(
    *,
    running: float,
    waiting: float,
    capacity: float,
    deferred: float,
    kv_usage: float,
    preemptions: float,
    queue_time_sum: float,
) -> dict[str, float]:
    labels = '{engine="0",model_name="test"}'
    return {
        f"vllm:num_requests_running{labels}": running,
        f"vllm:num_requests_waiting{labels}": waiting,
        'vllm:num_requests_waiting_by_reason{engine="0",model_name="test",reason="capacity"}': capacity,
        'vllm:num_requests_waiting_by_reason{engine="0",model_name="test",reason="deferred"}': deferred,
        f"vllm:kv_cache_usage_perc{labels}": kv_usage,
        f"vllm:num_preemptions_total{labels}": preemptions,
        f"vllm:request_queue_time_seconds_sum{labels}": queue_time_sum,
    }


def record(monotonic_s: float, metrics: dict[str, float] | None = None) -> dict:
    result = {
        "timestamp_utc": f"2026-08-10T00:00:{int(monotonic_s):02d}+00:00",
        "monotonic_s": monotonic_s,
    }
    if metrics is None:
        result["error"] = "TimeoutError: synthetic timeout"
    else:
        result["metrics"] = metrics
    return result


class TelemetryAggregationTests(unittest.TestCase):
    def write_jsonl(self, path: Path, records: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(item) + "\n" for item in records),
            encoding="utf-8",
        )

    def test_aggregates_gauges_percentiles_errors_and_counter_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "steady-r1-metrics.jsonl"
            self.write_jsonl(
                path,
                [
                    record(
                        1,
                        metrics_sample(
                            running=1,
                            waiting=0,
                            capacity=0,
                            deferred=0,
                            kv_usage=0.2,
                            preemptions=10,
                            queue_time_sum=3,
                        ),
                    ),
                    record(2, None),
                    record(
                        3,
                        metrics_sample(
                            running=4,
                            waiting=3,
                            capacity=2,
                            deferred=1,
                            kv_usage=0.8,
                            preemptions=12,
                            queue_time_sum=7.5,
                        ),
                    ),
                    record(
                        4,
                        metrics_sample(
                            running=2,
                            waiting=1,
                            capacity=1,
                            deferred=0,
                            kv_usage=1.0,
                            preemptions=13,
                            queue_time_sum=9,
                        ),
                    ),
                ],
            )

            row = aggregate_telemetry.aggregate_file(path)

            self.assertEqual(row["run_id"], "steady-r1")
            self.assertEqual(row["samples"], 3)
            self.assertEqual(row["errors"], 1)
            self.assertEqual(row["coverage_s"], 3)
            self.assertEqual(row["max_running"], 4)
            self.assertEqual(row["max_waiting"], 3)
            self.assertEqual(row["max_capacity_waiting"], 2)
            self.assertEqual(row["max_deferred_waiting"], 1)
            self.assertAlmostEqual(row["kv_cache_usage_p95_pct"], 98.0)
            self.assertEqual(row["kv_cache_usage_max_pct"], 100.0)
            self.assertEqual(row["preemptions_delta"], 3)
            self.assertEqual(row["queue_time_sum_delta_s"], 6)
            self.assertFalse(row["counter_reset"])

    def test_counter_reset_is_corrected_and_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reset-metrics.jsonl"
            self.write_jsonl(
                path,
                [
                    record(
                        1,
                        metrics_sample(
                            running=0,
                            waiting=0,
                            capacity=0,
                            deferred=0,
                            kv_usage=0,
                            preemptions=8,
                            queue_time_sum=10,
                        ),
                    ),
                    record(
                        2,
                        metrics_sample(
                            running=0,
                            waiting=0,
                            capacity=0,
                            deferred=0,
                            kv_usage=0,
                            preemptions=1,
                            queue_time_sum=2,
                        ),
                    ),
                    record(
                        3,
                        metrics_sample(
                            running=0,
                            waiting=0,
                            capacity=0,
                            deferred=0,
                            kv_usage=0,
                            preemptions=4,
                            queue_time_sum=5,
                        ),
                    ),
                ],
            )

            row = aggregate_telemetry.aggregate_file(path)

            self.assertTrue(row["counter_reset"])
            self.assertEqual(row["preemptions_delta"], 4)
            self.assertEqual(row["queue_time_sum_delta_s"], 5)

    def test_snapshot_deltas_handle_each_counter_reset_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before = Path(directory) / "before.txt"
            after = Path(directory) / "after.txt"
            before.write_text(
                "vllm:num_preemptions_total 10\n"
                "vllm:request_queue_time_seconds_sum 20\n",
                encoding="utf-8",
            )
            after.write_text(
                "vllm:num_preemptions_total 2\n"
                "vllm:request_queue_time_seconds_sum 25\n",
                encoding="utf-8",
            )
            preemptions, queue_time, reset = aggregate_telemetry.snapshot_counter_delta(
                before, after
            )
        self.assertEqual(preemptions, 2)
        self.assertEqual(queue_time, 5)
        self.assertTrue(reset)

    def test_missing_metric_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-metrics.jsonl"
            incomplete = metrics_sample(
                running=0,
                waiting=0,
                capacity=0,
                deferred=0,
                kv_usage=0,
                preemptions=0,
                queue_time_sum=0,
            )
            del incomplete[next(key for key in incomplete if "kv_cache" in key)]
            self.write_jsonl(path, [record(1, incomplete), record(2, incomplete)])

            with self.assertRaisesRegex(
                aggregate_telemetry.TelemetryError, "missing required metrics"
            ):
                aggregate_telemetry.aggregate_file(path)

    def test_cli_pattern_writes_only_matching_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry_dir = Path(directory) / "telemetry"
            telemetry_dir.mkdir()
            samples = [
                record(
                    1,
                    metrics_sample(
                        running=0,
                        waiting=0,
                        capacity=0,
                        deferred=0,
                        kv_usage=0.1,
                        preemptions=0,
                        queue_time_sum=0,
                    ),
                ),
                record(
                    2,
                    metrics_sample(
                        running=1,
                        waiting=0,
                        capacity=0,
                        deferred=0,
                        kv_usage=0.2,
                        preemptions=0,
                        queue_time_sum=0.5,
                    ),
                ),
            ]
            self.write_jsonl(telemetry_dir / "keep-metrics.jsonl", samples)
            self.write_jsonl(telemetry_dir / "skip-metrics.jsonl", samples)
            output = Path(directory) / "summary.csv"

            status = aggregate_telemetry.main(
                [
                    "--telemetry-dir",
                    str(telemetry_dir),
                    "--pattern",
                    "keep*-metrics.jsonl",
                    "--output",
                    str(output),
                    "--allow-legacy-unmanifested",
                ]
            )

            self.assertEqual(status, 0)
            with output.open(newline="", encoding="utf-8") as input_file:
                rows = list(csv.DictReader(input_file))
            self.assertEqual([row["run_id"] for row in rows], ["keep"])

    def test_cli_fails_closed_on_poll_error_unless_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry_dir = Path(directory) / "telemetry"
            telemetry_dir.mkdir()
            samples = [
                record(
                    1,
                    metrics_sample(
                        running=0,
                        waiting=0,
                        capacity=0,
                        deferred=0,
                        kv_usage=0.1,
                        preemptions=0,
                        queue_time_sum=0,
                    ),
                ),
                record(2, None),
                record(
                    3,
                    metrics_sample(
                        running=0,
                        waiting=0,
                        capacity=0,
                        deferred=0,
                        kv_usage=0.2,
                        preemptions=0,
                        queue_time_sum=0.5,
                    ),
                ),
            ]
            self.write_jsonl(telemetry_dir / "poll-error-metrics.jsonl", samples)
            output = Path(directory) / "summary.csv"
            common_args = [
                "--telemetry-dir",
                str(telemetry_dir),
                "--output",
                str(output),
                "--allow-legacy-unmanifested",
            ]

            with self.assertRaisesRegex(
                aggregate_telemetry.TelemetryError, "polling errors"
            ):
                aggregate_telemetry.main(common_args)
            self.assertFalse(output.exists())

            status = aggregate_telemetry.main([*common_args, "--allow-poll-errors"])
            self.assertEqual(status, 0)
            with output.open(newline="", encoding="utf-8") as input_file:
                rows = list(csv.DictReader(input_file))
            self.assertEqual(rows[0]["errors"], "1")

    def test_cli_fails_closed_on_counter_reset_unless_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            telemetry_dir = Path(directory) / "telemetry"
            telemetry_dir.mkdir()
            samples = [
                record(
                    1,
                    metrics_sample(
                        running=0,
                        waiting=0,
                        capacity=0,
                        deferred=0,
                        kv_usage=0.1,
                        preemptions=5,
                        queue_time_sum=5,
                    ),
                ),
                record(
                    2,
                    metrics_sample(
                        running=0,
                        waiting=0,
                        capacity=0,
                        deferred=0,
                        kv_usage=0.2,
                        preemptions=1,
                        queue_time_sum=1,
                    ),
                ),
            ]
            self.write_jsonl(telemetry_dir / "reset-metrics.jsonl", samples)
            output = Path(directory) / "summary.csv"
            common_args = [
                "--telemetry-dir",
                str(telemetry_dir),
                "--output",
                str(output),
                "--allow-legacy-unmanifested",
            ]

            with self.assertRaisesRegex(
                aggregate_telemetry.TelemetryError, "counter resets"
            ):
                aggregate_telemetry.main(common_args)
            self.assertFalse(output.exists())

            status = aggregate_telemetry.main([*common_args, "--allow-counter-resets"])
            self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
