from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from benchmark.evidence_manifest import (
    EXPECTED_ARTIFACT_NAMES,
    ManifestError,
    build_manifest,
    mark_complete,
    record_exit_codes,
    sha256_file,
    transition_status,
    validate_artifact_path,
    validate_manifest_invariants,
    validate_manifest_path,
)
from benchmark.plan_open_loop import build_plan, write_plan
from benchmark.validate_evidence import (
    EvidenceValidationError,
    _validate_plan_binding,
    artifact_paths_from_manifest,
    validate_evidence_bundle,
)


class EvidenceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.expected: dict[str, object] = {
            "run_id": "test-run-r1",
            "phase": "test",
            "input_len": 8,
            "output_len": 4,
            "concurrency": 2,
            "repetition": 1,
            "num_prompts": 2,
            "request_rate": "3",
            "random_range_ratio": "0",
            "loadgen_seed": 7,
            "image": "vllm/test@sha256:abc",
            "model": "test/model",
            "model_revision": "revision-1",
        }
        self.artifacts = {
            "raw_result": self.project_root / "results/raw/test-run-r1.json",
            "benchmark_log": self.project_root / "artifacts/logs/test-run-r1.log",
            "gpu_telemetry": self.project_root
            / "results/telemetry/test-run-r1-gpu.csv",
            "metrics_timeseries": self.project_root
            / "results/telemetry/test-run-r1-metrics.jsonl",
            "metrics_before": self.project_root
            / "results/telemetry/test-run-r1-metrics-before.txt",
            "metrics_after": self.project_root
            / "results/telemetry/test-run-r1-metrics-after.txt",
            "docker_before": self.project_root
            / "results/telemetry/test-run-r1-docker-before.txt",
            "docker_after": self.project_root
            / "results/telemetry/test-run-r1-docker-after.txt",
        }
        self.assertEqual(set(self.artifacts), set(EXPECTED_ARTIFACT_NAMES))
        for path in self.artifacts.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("sidecar\n", encoding="utf-8")
        self.artifacts["gpu_telemetry"].write_text(
            "timestamp, index, utilization.gpu [%], utilization.memory [%], "
            "memory.used [MiB], memory.free [MiB], power.draw [W], "
            "clocks.current.sm [MHz], clocks.current.memory [MHz], "
            "temperature.gpu\n"
            "2026/08/10 00:00:00.000, 0, 1 %, 2 %, 100 MiB, 200 MiB, "
            "[N/A], 210 MHz, 405 MHz, 50\n"
            "2026/08/10 00:00:02.000, 0, 3 %, 4 %, 110 MiB, 190 MiB, "
            "25 W, 420 MHz, 810 MHz, 51\n",
            encoding="utf-8",
        )

        raw_result = {
            "backend": "vllm",
            "model_id": "test/model",
            "completed": 2,
            "failed": 0,
            "duration": 1.0,
            "request_throughput": 2.0,
            "output_throughput": 8.0,
            "total_input_tokens": 16,
            "total_output_tokens": 8,
            "num_prompts": 2,
            "max_concurrency": 2,
            "run_id": "test-run-r1",
            "phase": "test",
            "input_len": "8",
            "output_len": "4",
            "concurrency": "2",
            "repetition": "1",
            "request_rate": 3.0,
            "loadgen_seed": "7",
            "input_lens": [8, 8],
            "output_lens": [4, 4],
            "ttfts": [0.1, 0.2],
            "itls": [[0.01], [0.01]],
            "start_times": [0.0, 0.1],
            "generated_texts": ["a", "b"],
            "errors": ["", ""],
        }
        self.artifacts["raw_result"].write_text(
            json.dumps(raw_result), encoding="utf-8"
        )
        metrics_record = {
            "timestamp_utc": "2026-08-10T00:00:00+00:00",
            "monotonic_s": 1.0,
            "metrics": {
                'vllm:num_requests_running{engine="0"}': 0.0,
                'vllm:num_requests_waiting{engine="0"}': 0.0,
                'vllm:num_requests_waiting_by_reason{engine="0",reason="capacity"}': 0.0,
                'vllm:num_requests_waiting_by_reason{engine="0",reason="deferred"}': 0.0,
                'vllm:kv_cache_usage_perc{engine="0"}': 0.0,
                'vllm:num_preemptions_total{engine="0"}': 0.0,
                'vllm:request_queue_time_seconds_sum{engine="0"}': 0.0,
            },
        }
        self.artifacts["metrics_timeseries"].write_text(
            json.dumps(metrics_record)
            + "\n"
            + json.dumps(
                {
                    **metrics_record,
                    "timestamp_utc": "2026-08-10T00:00:01+00:00",
                    "monotonic_s": 2.0,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build_manifest(
        self, *, provenance_capture: str = "run_start"
    ) -> dict[str, object]:
        return build_manifest(
            project_root=self.project_root,
            run_id="test-run-r1",
            phase="test",
            input_len=8,
            output_len=4,
            concurrency=2,
            repetition=1,
            num_prompts=2,
            num_warmups=1,
            request_rate="3",
            random_range_ratio="0",
            loadgen_seed=7,
            image="vllm/test@sha256:abc",
            model="test/model",
            model_revision="revision-1",
            tokenizer_snapshot="/cache/revision-1",
            server_settings={"max_model_len": "16"},
            workload_settings={"case": "unit"},
            artifacts=self.artifacts,
            provenance_capture=provenance_capture,
        )

    def test_running_bundle_completes_only_after_validation(self) -> None:
        manifest = self.build_manifest()
        transition_status(manifest, "running")
        record_exit_codes(manifest, gpu=143, metrics=0, benchmark=0)

        summary = validate_evidence_bundle(
            project_root=self.project_root,
            artifacts=self.artifacts,
            expected=self.expected,
            manifest=manifest,
        )
        self.assertEqual(
            summary,
            {"completed_requests": 2, "metrics_samples": 2, "gpu_samples": 2},
        )

        mark_complete(manifest, self.project_root)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["validation"]["status"], "passed")
        self.assertTrue(all(item["sha256"] for item in manifest["artifacts"]))

        persisted_paths = artifact_paths_from_manifest(manifest, self.project_root)
        validate_evidence_bundle(
            project_root=self.project_root,
            artifacts=persisted_paths,
            expected=self.expected,
            manifest=manifest,
        )

    def test_rejects_wrong_detailed_array_length(self) -> None:
        raw_path = self.artifacts["raw_result"]
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_result["ttfts"] = [0.1]
        raw_path.write_text(json.dumps(raw_result), encoding="utf-8")

        with self.assertRaisesRegex(EvidenceValidationError, "ttfts.*length mismatch"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_metrics_polling_error(self) -> None:
        self.artifacts["metrics_timeseries"].write_text(
            json.dumps(
                {
                    "timestamp_utc": "2026-08-10T00:00:00+00:00",
                    "monotonic_s": 1.0,
                    "error": "TimeoutError: timed out",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(EvidenceValidationError, "metrics polling error"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_metrics_missing_required_family(self) -> None:
        metrics_path = self.artifacts["metrics_timeseries"]
        records = [json.loads(line) for line in metrics_path.read_text().splitlines()]
        for record in records:
            record["metrics"] = {
                key: value
                for key, value in record["metrics"].items()
                if not key.startswith("vllm:kv_cache_usage_perc")
            }
        metrics_path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            EvidenceValidationError, "required metric families"
        ):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_metrics_that_end_before_requests_complete(self) -> None:
        raw_path = self.artifacts["raw_result"]
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_result["start_times"] = [10.0, 10.1]
        raw_path.write_text(json.dumps(raw_result), encoding="utf-8")

        with self.assertRaisesRegex(
            EvidenceValidationError, "ends before the last completion"
        ):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_nonfinite_detailed_latency(self) -> None:
        raw_path = self.artifacts["raw_result"]
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_result["ttfts"][0] = float("nan")
        raw_path.write_text(json.dumps(raw_result), encoding="utf-8")

        with self.assertRaisesRegex(EvidenceValidationError, "finite and non-negative"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_token_lengths_outside_fixed_workload(self) -> None:
        raw_path = self.artifacts["raw_result"]
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_result["output_lens"][0] = 3
        raw_result["total_output_tokens"] = 7
        raw_path.write_text(json.dumps(raw_result), encoding="utf-8")

        with self.assertRaisesRegex(EvidenceValidationError, "fixed-length workload"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_token_total_mismatch(self) -> None:
        raw_path = self.artifacts["raw_result"]
        raw_result = json.loads(raw_path.read_text(encoding="utf-8"))
        raw_result["total_output_tokens"] = 7
        raw_path.write_text(json.dumps(raw_result), encoding="utf-8")

        with self.assertRaisesRegex(EvidenceValidationError, "sum\(output_lens\)"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_rejects_gpu_telemetry_without_required_columns(self) -> None:
        self.artifacts["gpu_telemetry"].write_text(
            "timestamp,index\n2026/08/10 00:00:00.000,0\n2026/08/10 00:00:02.000,0\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(EvidenceValidationError, "required columns"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
            )

    def test_steady_manifest_is_bound_to_hashed_plan_row(self) -> None:
        manifest = self.build_manifest()
        workload = manifest["workload"]
        workload["phase"] = "open_loop_steady"
        workload["request_rate"] = "1"
        plan_path = self.project_root / "results/plans/test-v1.tsv"
        plan_path.parent.mkdir(parents=True)
        rows = build_plan(
            [Decimal("1")],
            [7],
            Decimal("2"),
            9,
            run_namespace="test-v1",
            input_len=8,
            output_len=4,
            max_concurrency=2,
            num_warmups=1,
            random_range_ratio="0",
            ttft_slo_ms="100",
            e2e_slo_ms="500",
        )
        with plan_path.open("w", newline="", encoding="utf-8") as output_file:
            write_plan(rows, output_file, delimiter="\t")

        workload["config"] = {
            "run_namespace": "test-v1",
            "run_attempt": "1",
            "plan_block": "1",
            "plan_order": "1",
            "plan_shuffle_seed": "9",
            "plan_path": "results/plans/test-v1.tsv",
            "plan_sha256": sha256_file(plan_path),
            "nominal_arrival_horizon_seconds": "2",
            "ttft_slo_ms": "100",
            "e2e_slo_ms": "500",
            "tpot_slo_ms": "",
        }
        raw = json.loads(self.artifacts["raw_result"].read_text(encoding="utf-8"))
        raw.update(
            {
                "run_namespace": "test-v1",
                "run_attempt": "1",
                "plan_block": "1",
                "plan_order": "1",
                "plan_shuffle_seed": "9",
                "plan_sha256": workload["config"]["plan_sha256"],
                "nominal_arrival_horizon_seconds": "2",
                "ttft_slo_ms": "100",
                "e2e_slo_ms": "500",
                "tpot_slo_ms": "",
            }
        )
        _validate_plan_binding(manifest, self.project_root, raw)

        plan_path.write_text(
            plan_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(EvidenceValidationError, "SHA256 mismatch"):
            _validate_plan_binding(manifest, self.project_root, raw)

    def test_complete_manifest_detects_artifact_tampering(self) -> None:
        manifest = self.build_manifest()
        transition_status(manifest, "running")
        record_exit_codes(manifest, gpu=143, metrics=0, benchmark=0)
        mark_complete(manifest, self.project_root)
        self.artifacts["benchmark_log"].write_text("changed\n", encoding="utf-8")

        with self.assertRaisesRegex(
            EvidenceValidationError, "artifact size changed|SHA256"
        ):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
                manifest=manifest,
            )

    def test_complete_manifest_requires_passed_validation_invariant(self) -> None:
        manifest = self.build_manifest()
        transition_status(manifest, "running")
        record_exit_codes(manifest, gpu=143, metrics=0, benchmark=0)
        mark_complete(manifest, self.project_root)
        manifest["validation"] = {"status": "pending", "validated_at_utc": None}

        with self.assertRaisesRegex(ManifestError, "passed validation"):
            validate_manifest_invariants(manifest)

    def test_collector_acceptable_flag_cannot_override_bad_exit_code(self) -> None:
        manifest = self.build_manifest()
        transition_status(manifest, "running")
        record_exit_codes(manifest, gpu=143, metrics=0, benchmark=0)
        manifest["collectors"]["gpu"]["exit_code"] = 99
        manifest["collectors"]["gpu"]["acceptable"] = True

        with self.assertRaisesRegex(EvidenceValidationError, "inconsistent"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=self.artifacts,
                expected=self.expected,
                manifest=manifest,
            )

    def test_backfilled_bundle_can_complete_without_unknown_exit_codes(self) -> None:
        manifest = self.build_manifest(provenance_capture="backfilled")
        self.assertFalse(manifest["provenance"]["represents_run_time"])
        self.assertEqual(manifest["git"]["capture_scope"], "manifest_backfill_time")
        self.assertEqual(manifest["server"]["capture_scope"], "manifest_backfill_time")
        self.assertEqual(
            manifest["workload"]["capture_scope"], "manifest_backfill_time"
        )
        validate_evidence_bundle(
            project_root=self.project_root,
            artifacts=self.artifacts,
            expected=self.expected,
            manifest=manifest,
        )
        mark_complete(manifest, self.project_root)
        self.assertEqual(manifest["status"], "complete")

    def test_rejects_artifact_outside_its_evidence_root(self) -> None:
        misplaced_artifacts = dict(self.artifacts)
        misplaced_artifacts["benchmark_log"] = self.project_root / "results/raw/log.txt"
        misplaced_artifacts["benchmark_log"].write_text("log\n", encoding="utf-8")

        with self.assertRaisesRegex(EvidenceValidationError, "must be inside"):
            validate_evidence_bundle(
                project_root=self.project_root,
                artifacts=misplaced_artifacts,
                expected=self.expected,
            )

    def test_rejects_evidence_root_symlink_that_escapes_project(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory:
            with tempfile.TemporaryDirectory() as outside_directory:
                project_root = Path(project_directory)
                outside_root = Path(outside_directory)
                (project_root / "results").mkdir()
                (project_root / "results/raw").symlink_to(
                    outside_root, target_is_directory=True
                )
                with self.assertRaisesRegex(ManifestError, "escapes project root"):
                    validate_artifact_path(
                        project_root, "raw_result", outside_root / "run.json"
                    )

                (project_root / "results/raw").unlink()
                (project_root / "results/manifests").symlink_to(
                    outside_root, target_is_directory=True
                )
                with self.assertRaisesRegex(ManifestError, "escapes project root"):
                    validate_manifest_path(project_root, outside_root / "run.json")


if __name__ == "__main__":
    unittest.main()
