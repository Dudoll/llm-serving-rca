from __future__ import annotations

import unittest

from benchmark.phase_pipeline import PHASE_PIPELINES, resolve_phase


class PhasePipelineTests(unittest.TestCase):
    def test_aliases_resolve_to_the_expected_pipeline(self) -> None:
        self.assertEqual(resolve_phase("phase1").name, "baseline")
        self.assertEqual(resolve_phase("phase2").name, "prefill_decode")
        self.assertEqual(resolve_phase("phase3a").name, "open_loop")
        self.assertEqual(resolve_phase("phase3b").name, "open_loop_steady")

    def test_each_pipeline_has_a_summary_and_aggregate(self) -> None:
        for pipeline in PHASE_PIPELINES.values():
            self.assertTrue(pipeline.raw_pattern)
            self.assertTrue(pipeline.summary_output.endswith(".csv"))
            self.assertTrue(pipeline.aggregate_module.startswith("benchmark."))
            self.assertTrue(pipeline.aggregate_output.endswith(".csv"))

    def test_only_open_loop_phases_have_telemetry(self) -> None:
        self.assertIsNone(PHASE_PIPELINES["baseline"].telemetry_pattern)
        self.assertIsNone(PHASE_PIPELINES["prefill_decode"].telemetry_pattern)
        self.assertIsNotNone(PHASE_PIPELINES["open_loop"].telemetry_pattern)
        self.assertIsNotNone(PHASE_PIPELINES["open_loop_steady"].telemetry_pattern)

    def test_only_phase3b_has_an_accepted_attempt_ledger(self) -> None:
        self.assertIsNone(PHASE_PIPELINES["baseline"].accepted_attempts)
        self.assertIsNone(PHASE_PIPELINES["prefill_decode"].accepted_attempts)
        self.assertIsNone(PHASE_PIPELINES["open_loop"].accepted_attempts)
        self.assertEqual(
            PHASE_PIPELINES["open_loop_steady"].accepted_attempts,
            "results/plans/phase3b-v1-accepted-attempts.tsv",
        )

    def test_unknown_phase_error_lists_choices(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown phase"):
            resolve_phase("phase9")


if __name__ == "__main__":
    unittest.main()
