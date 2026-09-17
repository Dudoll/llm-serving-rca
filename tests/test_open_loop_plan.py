from __future__ import annotations

import io
import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "benchmark"))

import plan_open_loop  # noqa: E402


class OpenLoopPlanTests(unittest.TestCase):
    def test_plan_pairs_seed_across_rates_and_is_deterministic(self) -> None:
        rates = [Decimal("12"), Decimal("13"), Decimal("14")]
        seeds = [31001, 31002, 31003, 31004, 31005]

        first = plan_open_loop.build_plan(rates, seeds, Decimal("300"), 20260810)
        second = plan_open_loop.build_plan(rates, seeds, Decimal("300"), 20260810)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 15)
        for block, seed in enumerate(seeds, start=1):
            block_rows = [row for row in first if row.block == block]
            self.assertEqual({row.loadgen_seed for row in block_rows}, {seed})
            self.assertEqual({row.request_rate for row in block_rows}, set(rates))
            self.assertEqual([row.order_in_block for row in block_rows], [1, 2, 3])
            self.assertEqual(
                {row.request_rate: row.num_prompts for row in block_rows},
                {Decimal("12"): 3600, Decimal("13"): 3900, Decimal("14"): 4200},
            )

        for rate in rates:
            position_counts = [
                sum(
                    row.request_rate == rate and row.order_in_block == position
                    for row in first
                )
                for position in (1, 2, 3)
            ]
            self.assertLessEqual(max(position_counts) - min(position_counts), 1)

        predecessor_counts = []
        for left in rates:
            for right in rates:
                if left == right:
                    continue
                predecessor_counts.append(
                    sum(
                        any(
                            pair == (left, right)
                            for pair in zip(
                                [
                                    row.request_rate
                                    for row in first
                                    if row.block == block
                                ],
                                [
                                    row.request_rate
                                    for row in first
                                    if row.block == block
                                ][1:],
                            )
                        )
                        for block in range(1, len(seeds) + 1)
                    )
                )
        self.assertLessEqual(max(predecessor_counts) - min(predecessor_counts), 1)

    def test_plan_rejects_duplicate_seeds_and_fractional_prompt_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "seeds must be unique"):
            plan_open_loop.build_plan([Decimal("12")], [7, 7], Decimal("300"), 1)
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            plan_open_loop.build_plan([Decimal("1.25")], [7], Decimal("0.5"), 1)

    def test_tsv_writer_can_omit_header_for_shell_runner(self) -> None:
        rows = plan_open_loop.build_plan([Decimal("12")], [31001], Decimal("10"), 9)
        output = io.StringIO()
        plan_open_loop.write_plan(rows, output, delimiter="\t", include_header=False)
        columns = output.getvalue().rstrip("\n").split("\t")
        self.assertEqual(columns[:8], ["1", "1", "1", "31001", "12", "120", "10", "9"])
        self.assertEqual(len(columns), len(plan_open_loop.FIELDNAMES))
        self.assertTrue(all(value == "" for value in columns[8:]))


if __name__ == "__main__":
    unittest.main()
