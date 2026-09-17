from __future__ import annotations

import json
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmark.aggregate_open_loop import CORE_METRICS, aggregate
from benchmark.analyze_slo import analyze_result, validate_frozen_slos
from benchmark.check_correctness import compare
from benchmark.check_regression import evaluate
from benchmark.summarize_results import summarize_file
from benchmark.summarize_results import (
    legacy_identity_fields,
    manifest_identity_fields,
    validate_summary_evidence_rows,
)


def valid_raw_result() -> dict:
    return {
        "phase": "open_loop_steady",
        "run_id": "steady-l12-r1",
        "input_len": "512",
        "output_len": "3",
        "concurrency": "32",
        "request_rate": 12,
        "loadgen_seed": "1",
        "repetition": "1",
        "num_prompts": 2,
        "completed": 2,
        "failed": 0,
        "duration": 1.0,
        "request_throughput": 2.0,
        "request_goodput": None,
        "input_throughput": 1024.0,
        "output_throughput": 6.0,
        "total_token_throughput": 1030.0,
        "mean_ttft_ms": 15.0,
        "p50_ttft_ms": 15.0,
        "p95_ttft_ms": 19.5,
        "p99_ttft_ms": 19.9,
        "mean_tpot_ms": 7.5,
        "p50_tpot_ms": 7.5,
        "p95_tpot_ms": 9.75,
        "p99_tpot_ms": 9.95,
        "mean_itl_ms": 7.5,
        "p50_itl_ms": 7.5,
        "p95_itl_ms": 9.75,
        "p99_itl_ms": 9.95,
        "mean_e2el_ms": 30.0,
        "p50_e2el_ms": 30.0,
        "p95_e2el_ms": 38.0,
        "p99_e2el_ms": 39.6,
        "input_lens": [512, 512],
        "output_lens": [3, 3],
        "ttfts": [0.01, 0.02],
        "itls": [[0.005, 0.005], [0.01, 0.01]],
        "start_times": [10.0, 10.1],
        "generated_texts": ["a", "b"],
        "errors": ["", ""],
    }


def aggregate_row(*, repetition: int, seed: int) -> dict[str, str]:
    row = {
        "evidence_status": "complete",
        "experiment_spec_sha256": "spec-a",
        "phase": "open_loop_steady",
        "run_id": f"steady-l12-r{repetition}",
        "input_len": "512",
        "output_len": "128",
        "concurrency": "256",
        "request_rate": "12",
        "loadgen_seed": str(seed),
        "repetition": str(repetition),
        "num_prompts": "120",
        "completed": "120",
        "failed": "0",
        "request_goodput": "",
        "arrival_span_s": "10",
        "realized_send_rate": "12",
        "drain_time_approx_s": "1",
    }
    row.update({metric: "10" for metric in CORE_METRICS})
    row["request_throughput"] = "11"
    return row


class SummarizeTests(unittest.TestCase):
    def write_result(self, result: dict, directory: str) -> Path:
        path = Path(directory) / "result.json"
        path.write_text(json.dumps(result), encoding="utf-8")
        return path

    def test_summarize_derives_arrival_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            row = summarize_file(self.write_result(valid_raw_result(), directory))
        self.assertAlmostEqual(float(row["arrival_span_s"]), 0.1)
        self.assertAlmostEqual(float(row["realized_send_rate"]), 10.0)
        self.assertAlmostEqual(float(row["drain_time_approx_s"]), 0.9)

    def test_summarize_audits_actual_input_token_drift(self) -> None:
        result = valid_raw_result()
        result["input_lens"] = [514, 512]
        with tempfile.TemporaryDirectory() as directory:
            row = summarize_file(self.write_result(result, directory))
        self.assertEqual(row["actual_input_len_min"], 512)
        self.assertEqual(row["actual_input_len_max"], 514)
        self.assertEqual(row["actual_input_len_mean"], 513)
        self.assertEqual(row["input_token_abs_drift_total"], 2)
        self.assertEqual(row["input_token_net_drift_total"], 2)
        self.assertAlmostEqual(row["input_token_abs_drift_pct"], 2 / 1024 * 100)

    def test_summarize_rejects_incomplete_arrays(self) -> None:
        result = valid_raw_result()
        result["ttfts"] = [0.01]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "ttfts"):
                summarize_file(self.write_result(result, directory))

    def test_summarize_rejects_nonfinite_request_latency(self) -> None:
        result = valid_raw_result()
        result["ttfts"][0] = float("nan")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "finite and non-negative"):
                summarize_file(self.write_result(result, directory))

    def test_summary_admission_rejects_modified_intermediate_csv_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw_dir = Path(directory)
            raw_path = raw_dir / "steady-l12-r1.json"
            raw_path.write_text(json.dumps(valid_raw_result()), encoding="utf-8")
            row = summarize_file(raw_path)
            row.update(legacy_identity_fields())
            row["evidence_status"] = "legacy-unmanifested"
            row["request_throughput"] = "999"
            with patch("benchmark.summarize_results.RAW_DIR", raw_dir):
                with self.assertRaisesRegex(ValueError, "was modified"):
                    validate_summary_evidence_rows(
                        [row], allow_legacy_unmanifested=True
                    )

    def test_experiment_identity_ignores_evidence_tree_churn(self) -> None:
        manifest = {
            "git": {
                "sha": "abc",
                "tracked_diff_sha256": "tracked",
                "untracked_source_tree_sha256": "source",
                "status_sha256": "status-1",
                "untracked_tree_sha256": "evidence-1",
                "capture_error": None,
            },
            "server": {"image": "image@sha256:abc", "config": {}},
            "workload": {
                "phase": "open_loop_steady",
                "num_warmups": 8,
                "random_range_ratio": "0",
                "config": {"plan_sha256": "plan"},
            },
        }
        changed_evidence = copy.deepcopy(manifest)
        changed_evidence["git"]["status_sha256"] = "status-2"
        changed_evidence["git"]["untracked_tree_sha256"] = "evidence-2"
        changed_evidence["git"]["tracked_diff_sha256"] = "tracked-evidence-churn"
        changed_evidence["git"]["untracked_source_tree_sha256"] = "source-2"
        self.assertEqual(
            manifest_identity_fields(manifest)["experiment_spec_sha256"],
            manifest_identity_fields(changed_evidence)["experiment_spec_sha256"],
        )
        changed_evidence["git"]["sha"] = "def"
        self.assertNotEqual(
            manifest_identity_fields(manifest)["experiment_spec_sha256"],
            manifest_identity_fields(changed_evidence)["experiment_spec_sha256"],
        )


class OpenLoopAggregateTests(unittest.TestCase):
    def test_aggregate_requires_unique_repetition_and_seed(self) -> None:
        rows = [
            aggregate_row(repetition=1, seed=1),
            aggregate_row(repetition=2, seed=2),
        ]
        result = aggregate(rows, expected_repetitions=2)[0]
        self.assertEqual(result["distinct_loadgen_seeds"], 2)
        self.assertAlmostEqual(
            float(result["benchmark_throughput_vs_configured_rate_pct"]),
            11 / 12 * 100,
        )

        rows[1]["repetition"] = "1"
        with self.assertRaisesRegex(ValueError, "duplicate repetitions"):
            aggregate(rows, expected_repetitions=2)

    def test_aggregate_allows_shared_default_seed(self) -> None:
        rows = [
            aggregate_row(repetition=1, seed=0),
            aggregate_row(repetition=2, seed=0),
        ]
        result = aggregate(rows, expected_repetitions=2)[0]
        self.assertEqual(result["distinct_loadgen_seeds"], 1)

        rate_b = [
            aggregate_row(repetition=1, seed=0),
            aggregate_row(repetition=2, seed=0),
        ]
        for row in rate_b:
            row["request_rate"] = "13"
            row["run_id"] = row["run_id"].replace("l12", "l13")
        self.assertEqual(
            len(aggregate([*rows, *rate_b], expected_repetitions=2)),
            2,
        )

    def test_aggregate_rejects_partially_duplicated_seeds(self) -> None:
        rows = [
            aggregate_row(repetition=1, seed=1),
            aggregate_row(repetition=2, seed=1),
            aggregate_row(repetition=3, seed=2),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate load-generator seeds"):
            aggregate(rows, expected_repetitions=3)

    def test_aggregate_rejects_partial_optional_metric(self) -> None:
        rows = [
            aggregate_row(repetition=1, seed=1),
            aggregate_row(repetition=2, seed=2),
        ]
        rows[1]["arrival_span_s"] = ""
        with self.assertRaisesRegex(ValueError, "present in only part"):
            aggregate(rows, expected_repetitions=2)

        rows[1]["arrival_span_s"] = "10"
        rows[1]["p95_ttft_ms"] = "nan"
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            aggregate(rows, expected_repetitions=2)

    def test_aggregate_requires_exact_repetition_set_and_paired_seeds(self) -> None:
        missing_first = [
            aggregate_row(repetition=2, seed=2),
            aggregate_row(repetition=3, seed=3),
        ]
        with self.assertRaisesRegex(ValueError, "repetition set"):
            aggregate(missing_first, expected_repetitions=2)

        rate_12 = [
            aggregate_row(repetition=1, seed=1),
            aggregate_row(repetition=2, seed=2),
        ]
        rate_13 = [
            aggregate_row(repetition=1, seed=1),
            aggregate_row(repetition=2, seed=99),
        ]
        for row in rate_13:
            row["request_rate"] = "13"
            row["run_id"] = row["run_id"].replace("l12", "l13")
        with self.assertRaisesRegex(ValueError, "paired seed blocks"):
            aggregate([*rate_12, *rate_13], expected_repetitions=2)

    def test_aggregate_requires_explicit_legacy_admission(self) -> None:
        rows = [aggregate_row(repetition=1, seed=1)]
        rows[0]["evidence_status"] = "legacy-unmanifested"
        with self.assertRaisesRegex(ValueError, "inadmissible evidence_status"):
            aggregate(rows, expected_repetitions=1)
        self.assertEqual(
            len(
                aggregate(
                    rows,
                    expected_repetitions=1,
                    allow_legacy_unmanifested=True,
                )
            ),
            1,
        )

        mixed = [
            aggregate_row(repetition=1, seed=1),
            aggregate_row(repetition=2, seed=2),
        ]
        mixed[1]["evidence_status"] = "complete-backfilled"
        with self.assertRaisesRegex(ValueError, "mixes evidence provenance"):
            aggregate(mixed, expected_repetitions=2)


class SloAndCorrectnessTests(unittest.TestCase):
    def test_slo_thresholds_must_match_frozen_steady_spec(self) -> None:
        manifest = {
            "workload": {
                "phase": "open_loop_steady",
                "config": {
                    "ttft_slo_ms": "100",
                    "e2e_slo_ms": "500",
                    "tpot_slo_ms": "",
                },
            }
        }
        validate_frozen_slos(
            manifest,
            ttft_slo_ms=100.0,
            e2e_slo_ms=500.0,
            tpot_slo_ms=None,
        )
        with self.assertRaisesRegex(ValueError, "pre-registered ExperimentSpec"):
            validate_frozen_slos(
                manifest,
                ttft_slo_ms=101.0,
                e2e_slo_ms=500.0,
                tpot_slo_ms=None,
            )

    def test_slo_goodput_uses_request_level_latency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(valid_raw_result()), encoding="utf-8")
            row = analyze_result(
                path,
                ttft_slo_ms=15,
                e2e_slo_ms=25,
                tpot_slo_ms=10,
            )
        self.assertEqual(row["slo_good_requests"], 1)
        self.assertEqual(row["slo_good_fraction_pct"], 50.0)

    def test_tpot_uses_output_tokens_not_stream_chunk_count(self) -> None:
        result = valid_raw_result()
        result["output_lens"] = [4, 4]
        result["itls"] = [[0.03], [0.03]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            row = analyze_result(
                path,
                ttft_slo_ms=100,
                e2e_slo_ms=100,
                tpot_slo_ms=15,
            )
        self.assertEqual(row["slo_good_requests"], 2)

    def test_correctness_detects_text_change(self) -> None:
        baseline = valid_raw_result()
        candidate = valid_raw_result()
        self.assertTrue(compare(baseline, candidate)["passed"])
        candidate["generated_texts"][1] = "changed"
        result = compare(baseline, candidate)
        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatches"], {"generated_texts": 1})

    def test_regression_gate_enforces_both_budgets(self) -> None:
        baseline = {
            "output_throughput_median": "100",
            "p99_ttft_ms_median": "200",
            "failed_total": "0",
        }
        candidate = {
            "output_throughput_median": "96",
            "p99_ttft_ms_median": "218",
            "failed_total": "0",
        }
        result = evaluate(
            baseline,
            candidate,
            throughput_field="output_throughput_median",
            latency_field="p99_ttft_ms_median",
            max_throughput_regression_pct=5,
            max_latency_regression_pct=10,
        )
        self.assertTrue(result["passed"])
        candidate["p99_ttft_ms_median"] = "221"
        self.assertFalse(
            evaluate(
                baseline,
                candidate,
                throughput_field="output_throughput_median",
                latency_field="p99_ttft_ms_median",
                max_throughput_regression_pct=5,
                max_latency_regression_pct=10,
            )["passed"]
        )


if __name__ == "__main__":
    unittest.main()
