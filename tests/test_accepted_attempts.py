from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from benchmark.accepted_attempts import (
    AcceptedAttemptsError,
    build_ledger_rows,
    load_accepted_run_ids,
    write_ledger,
)
from benchmark.evidence_manifest import (
    ARTIFACT_FILENAME_SUFFIXES,
    EVIDENCE_ROOTS,
    atomic_write_json,
    build_manifest,
    mark_complete,
    mark_failed,
    sha256_file,
    transition_status,
)
from benchmark.plan_open_loop import build_plan, format_decimal, write_plan


class AcceptedAttemptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)
        self.manifest_dir = self.project_root / "results/manifests"
        self.manifest_dir.mkdir(parents=True)
        self.plan_path = self.project_root / "results/plans/test-plan.tsv"
        self.plan_path.parent.mkdir(parents=True)
        self.plan_rows = build_plan(
            [Decimal("12")],
            [31001, 31002],
            Decimal("1"),
            9,
            run_namespace="test-v1",
            input_len=512,
            output_len=128,
            max_concurrency=32,
            num_warmups=1,
            random_range_ratio="0",
            ttft_slo_ms="200",
            e2e_slo_ms="4000",
        )
        with self.plan_path.open("w", newline="", encoding="utf-8") as output_file:
            write_plan(self.plan_rows, output_file, delimiter="\t")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def add_attempt(self, *, block: int, attempt: int, status: str) -> str:
        plan_row = next(row for row in self.plan_rows if row.block == block)
        rate = format_decimal(plan_row.request_rate)
        run_id = (
            f"open_loop_steady-test-v1-in512-out128-l{rate}"
            f"-s{plan_row.loadgen_seed}-b{block}-a{attempt}"
        )
        artifacts = {}
        for name, suffix in ARTIFACT_FILENAME_SUFFIXES.items():
            path = self.project_root / EVIDENCE_ROOTS[name] / f"{run_id}{suffix}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{name}\n", encoding="utf-8")
            artifacts[name] = path
        manifest = build_manifest(
            project_root=self.project_root,
            run_id=run_id,
            phase="open_loop_steady",
            input_len=512,
            output_len=128,
            concurrency=32,
            repetition=plan_row.repetition,
            num_prompts=plan_row.num_prompts,
            num_warmups=1,
            request_rate=rate,
            random_range_ratio="0",
            loadgen_seed=plan_row.loadgen_seed,
            image="vllm/test@sha256:abc",
            model="test/model",
            model_revision="revision-1",
            tokenizer_snapshot="/cache/revision-1",
            server_settings={},
            workload_settings={
                "run_namespace": "test-v1",
                "run_attempt": str(attempt),
                "plan_block": str(block),
                "plan_order": str(plan_row.order_in_block),
                "plan_shuffle_seed": "9",
                "plan_path": "results/plans/test-plan.tsv",
                "plan_sha256": sha256_file(self.plan_path),
                "nominal_arrival_horizon_seconds": "1",
                "ttft_slo_ms": "200",
                "e2e_slo_ms": "4000",
                "tpot_slo_ms": "",
            },
            artifacts=artifacts,
            provenance_capture="run_start",
        )
        transition_status(manifest, "running", reason="test")
        if status == "complete":
            mark_complete(manifest, self.project_root)
        elif status == "failed":
            mark_failed(
                manifest,
                self.project_root,
                exit_code=1,
                reason="evidence validation failed in test",
            )
        else:
            self.fail(f"unsupported test status {status}")
        atomic_write_json(self.manifest_dir / f"{run_id}.json", manifest)
        return run_id

    def test_builder_selects_one_complete_attempt_and_preserves_history(self) -> None:
        failed = self.add_attempt(block=1, attempt=1, status="failed")
        accepted_first = self.add_attempt(block=1, attempt=2, status="complete")
        accepted_second = self.add_attempt(block=2, attempt=1, status="complete")

        rows = build_ledger_rows(
            plan_path=self.plan_path,
            manifest_dir=self.manifest_dir,
            project_root=self.project_root,
        )
        self.assertEqual(rows[0]["accepted_run_id"], accepted_first)
        self.assertEqual(rows[0]["attempt_history"], "a1:failed,a2:complete")
        self.assertNotIn(failed, [row["accepted_run_id"] for row in rows])

        ledger_path = self.project_root / "results/plans/accepted.tsv"
        write_ledger(rows, ledger_path)
        self.assertEqual(
            load_accepted_run_ids(ledger_path, project_root=self.project_root),
            [accepted_first, accepted_second],
        )

    def test_incomplete_ledger_is_persistable_but_not_consumable(self) -> None:
        self.add_attempt(block=1, attempt=1, status="complete")
        rows = build_ledger_rows(
            plan_path=self.plan_path,
            manifest_dir=self.manifest_dir,
            project_root=self.project_root,
            allow_incomplete=True,
        )
        self.assertEqual(rows[1]["accepted_run_id"], "")
        ledger_path = self.project_root / "results/plans/accepted.tsv"
        write_ledger(rows, ledger_path)
        with self.assertRaisesRegex(AcceptedAttemptsError, "incomplete.*b2/o1"):
            load_accepted_run_ids(ledger_path, project_root=self.project_root)

    def test_builder_rejects_multiple_complete_attempts(self) -> None:
        self.add_attempt(block=1, attempt=1, status="complete")
        self.add_attempt(block=1, attempt=2, status="complete")
        self.add_attempt(block=2, attempt=1, status="complete")
        with self.assertRaisesRegex(AcceptedAttemptsError, "multiple complete"):
            build_ledger_rows(
                plan_path=self.plan_path,
                manifest_dir=self.manifest_dir,
                project_root=self.project_root,
            )

    def test_loader_rejects_an_accepted_run_swapped_between_plan_rows(self) -> None:
        self.add_attempt(block=1, attempt=1, status="complete")
        self.add_attempt(block=2, attempt=1, status="complete")
        rows = build_ledger_rows(
            plan_path=self.plan_path,
            manifest_dir=self.manifest_dir,
            project_root=self.project_root,
        )
        rows[0]["accepted_run_id"], rows[1]["accepted_run_id"] = (
            rows[1]["accepted_run_id"],
            rows[0]["accepted_run_id"],
        )
        ledger_path = self.project_root / "results/plans/accepted.tsv"
        write_ledger(rows, ledger_path)
        with self.assertRaisesRegex(AcceptedAttemptsError, "does not match its plan"):
            load_accepted_run_ids(ledger_path, project_root=self.project_root)


if __name__ == "__main__":
    unittest.main()
