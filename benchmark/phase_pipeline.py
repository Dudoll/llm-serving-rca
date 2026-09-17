"""Declarative description of the offline analysis pipeline for each phase.

The benchmark has several experiment phases, but their post-processing shape is
the same:

    raw JSON -> per-run summary -> repetition aggregate -> optional telemetry
    -> optional chart

Keeping this mapping in data (rather than repeating it in shell commands) makes
the project easier to teach and reduces the chance of sending Phase 2 files to
the Phase 3 aggregator by accident.  This module does not run commands or
validate evidence; it only describes the workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PhaseName = Literal["baseline", "prefill_decode", "open_loop", "open_loop_steady"]


@dataclass(frozen=True)
class PhasePipeline:
    """File patterns and tools belonging to one experiment phase."""

    name: PhaseName
    raw_pattern: str
    summary_output: str
    aggregate_module: str
    aggregate_output: str
    telemetry_pattern: str | None = None
    telemetry_output: str | None = None
    plot_module: str | None = None
    plot_output: str | None = None
    summary_no_gains: bool = False
    accepted_attempts: str | None = None


PHASE_PIPELINES: dict[PhaseName, PhasePipeline] = {
    "baseline": PhasePipeline(
        name="baseline",
        raw_pattern="baseline-*.json",
        summary_output="baseline-summary.csv",
        aggregate_module="benchmark.aggregate_baseline",
        aggregate_output="baseline-aggregate.csv",
        plot_module="benchmark.plot_baseline",
    ),
    "prefill_decode": PhasePipeline(
        name="prefill_decode",
        raw_pattern="pd-*.json",
        summary_output="prefill-decoder-summary.csv",
        aggregate_module="benchmark.aggregate_prefill_decoder",
        aggregate_output="prefill-decoder-aggregate.csv",
        plot_module="benchmark.plot_prefill_decoder",
        summary_no_gains=True,
    ),
    "open_loop": PhasePipeline(
        name="open_loop",
        raw_pattern="open_loop-*.json",
        summary_output="open-loop-summary.csv",
        aggregate_module="benchmark.aggregate_open_loop",
        aggregate_output="open-loop-aggregate.csv",
        telemetry_pattern="open_loop-*-metrics.jsonl",
        telemetry_output="open-loop-telemetry-summary.csv",
        plot_module="benchmark.plot_open_loop",
        plot_output="open-loop-saturation.png",
        summary_no_gains=True,
    ),
    "open_loop_steady": PhasePipeline(
        name="open_loop_steady",
        raw_pattern="open_loop_steady-*.json",
        summary_output="open-loop-steady-summary.csv",
        aggregate_module="benchmark.aggregate_open_loop",
        aggregate_output="open-loop-steady-aggregate.csv",
        telemetry_pattern="open_loop_steady-*-metrics.jsonl",
        telemetry_output="open-loop-steady-telemetry-summary.csv",
        plot_module="benchmark.plot_open_loop",
        plot_output="open-loop-steady.png",
        summary_no_gains=True,
        accepted_attempts="results/plans/phase3b-v1-accepted-attempts.tsv",
    ),
}


PHASE_ALIASES: dict[str, PhaseName] = {
    "phase1": "baseline",
    "phase2": "prefill_decode",
    "phase3a": "open_loop",
    "phase3b": "open_loop_steady",
    "prefill-decode": "prefill_decode",
    "open-loop": "open_loop",
    "open-loop-steady": "open_loop_steady",
}


def resolve_phase(name: str) -> PhasePipeline:
    """Resolve a human-friendly phase name or raise an actionable error."""
    canonical_name = PHASE_ALIASES.get(name, name)
    try:
        return PHASE_PIPELINES[canonical_name]  # type: ignore[index]
    except KeyError as error:
        choices = ", ".join((*PHASE_PIPELINES, *PHASE_ALIASES))
        raise ValueError(f"unknown phase {name!r}; choose one of: {choices}") from error
