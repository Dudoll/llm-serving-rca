"""Cross-run comparisons for one fixed ExperimentSpec."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .result_contract import GAIN_METRICS


def pct_change(current: float, baseline: float) -> float:
    return (current / baseline - 1.0) * 100.0


def add_percent_gains(rows: list[dict[str, Any]]) -> None:
    """Add gains vs c1 and previous concurrency inside one fixed group.

    The ExperimentSpec digest is part of the key.  Two runs with the same
    input/output lengths but different server settings must never be compared.
    """
    grouped: dict[tuple[str, int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row["experiment_spec_sha256"]),
                int(row["input_len"]),
                int(row["output_len"]),
                int(row["repetition"]),
            )
        ].append(row)

    for group_rows in grouped.values():
        ordered = sorted(group_rows, key=lambda item: int(item["concurrency"]))
        concurrencies = [int(item["concurrency"]) for item in ordered]
        if len(concurrencies) != len(set(concurrencies)):
            raise ValueError(
                f"Duplicate concurrency within one workload/repetition: {concurrencies}"
            )

        c1 = next((item for item in ordered if int(item["concurrency"]) == 1), None)
        previous: dict[str, Any] | None = None
        for row in ordered:
            for metric in GAIN_METRICS:
                current = float(row[metric])
                if c1 is None or float(c1[metric]) == 0:
                    row[f"{metric}_gain_vs_c1_pct"] = ""
                else:
                    row[f"{metric}_gain_vs_c1_pct"] = pct_change(
                        current, float(c1[metric])
                    )

                if previous is None or float(previous[metric]) == 0:
                    row[f"{metric}_gain_vs_previous_pct"] = ""
                else:
                    row[f"{metric}_gain_vs_previous_pct"] = pct_change(
                        current, float(previous[metric])
                    )
            previous = row
