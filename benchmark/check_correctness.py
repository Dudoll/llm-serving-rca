#!/usr/bin/env python3
"""Fail when a deterministic candidate changes benchmark outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


COMPARISON_FIELDS = ("input_lens", "output_lens", "generated_texts")


def load_result(path: Path) -> dict:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    for field in ("completed", "failed", *COMPARISON_FIELDS):
        if field not in result:
            raise ValueError(f"{path} is missing {field!r}")
    if int(result["failed"]) != 0:
        raise ValueError(f"{path} contains failed requests")
    return result


def compare(baseline: dict, candidate: dict) -> dict[str, object]:
    mismatches: dict[str, int] = {}
    if int(baseline["completed"]) != int(candidate["completed"]):
        mismatches["completed"] = 1

    for field in COMPARISON_FIELDS:
        baseline_values = baseline[field]
        candidate_values = candidate[field]
        if not isinstance(baseline_values, list) or not isinstance(
            candidate_values, list
        ):
            raise ValueError(f"{field!r} must be a list in both results")
        mismatch_count = abs(len(baseline_values) - len(candidate_values))
        mismatch_count += sum(
            left != right for left, right in zip(baseline_values, candidate_values)
        )
        if mismatch_count:
            mismatches[field] = mismatch_count

    return {"passed": not mismatches, "mismatches": mismatches}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare deterministic outputs before accepting a performance change"
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = compare(load_result(args.baseline), load_result(args.candidate))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
