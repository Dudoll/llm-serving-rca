#!/usr/bin/env python3
"""Run the offline analysis pipeline for one phase.

Examples from the repository root::

    python3 benchmark/analyze_phase.py phase1 --legacy --plot
    python3 benchmark/analyze_phase.py phase2 --legacy
    python3 benchmark/analyze_phase.py phase3a --legacy --plot
    python3 benchmark/analyze_phase.py phase3b \
        --ttft-slo-ms 200 --e2e-slo-ms 2000 --plot

The command is deliberately a thin orchestrator.  The individual modules stay
usable on their own, so a learner can run one stage at a time and inspect the
CSV written between stages.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from benchmark.phase_pipeline import PHASE_PIPELINES, resolve_phase
except ModuleNotFoundError:  # pragma: no cover - direct script execution.
    from phase_pipeline import PHASE_PIPELINES, resolve_phase  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_DIR = PROJECT_ROOT / "results" / "summary"
CHART_DIR = PROJECT_ROOT / "charts"


def run_stage(label: str, module: str, *arguments: str) -> None:
    """Run one existing analysis module and show the exact command."""
    command = [sys.executable, "-m", module, *arguments]
    print(f"\n[{label}] {' '.join(command)}")
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the offline analysis pipeline for one experiment phase.",
        epilog=(
            "Examples: phase1 --legacy, phase2 --legacy, phase3a --plot, "
            "phase3b --ttft-slo-ms 200 --e2e-slo-ms 2000"
        ),
    )
    parser.add_argument(
        "phase",
        choices=sorted(
            (
                *PHASE_PIPELINES,
                "phase1",
                "phase2",
                "phase3a",
                "phase3b",
                "prefill-decode",
                "open-loop",
                "open-loop-steady",
            )
        ),
        help="Phase name or alias: phase1, phase2, phase3a, phase3b",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help=(
            "Explicitly admit historical raw/telemetry files without manifests. "
            "Do not use for new runs."
        ),
    )
    parser.add_argument(
        "--accepted-attempts",
        type=Path,
        help="Override the phase's accepted-attempt TSV ledger",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Run the phase chart generator after CSV analysis",
    )
    parser.add_argument(
        "--expected-repetitions",
        type=int,
        default=5,
        help="Expected repetitions per workload/rate (default: 5)",
    )
    parser.add_argument("--ttft-slo-ms", type=float)
    parser.add_argument("--e2e-slo-ms", type=float)
    parser.add_argument("--tpot-slo-ms", type=float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    pipeline = resolve_phase(args.phase)
    if args.legacy and args.accepted_attempts:
        raise ValueError("--accepted-attempts cannot be combined with --legacy")
    accepted_attempts = args.accepted_attempts
    if accepted_attempts is None and not args.legacy:
        accepted_attempts = pipeline.accepted_attempts

    if args.expected_repetitions <= 0:
        raise ValueError("--expected-repetitions must be positive")
    slo_values = (args.ttft_slo_ms, args.e2e_slo_ms, args.tpot_slo_ms)
    if any(value is not None for value in slo_values) and (
        args.ttft_slo_ms is None or args.e2e_slo_ms is None
    ):
        raise ValueError("--ttft-slo-ms and --e2e-slo-ms must be supplied together")
    if any(value is not None for value in slo_values) and pipeline.name != "open_loop_steady":
        raise ValueError("SLO analysis is currently defined for phase3b only")

    summary_args = [
        "benchmark.summarize_results",
        "--pattern",
        pipeline.raw_pattern,
        "--output",
        pipeline.summary_output,
    ]
    if pipeline.summary_no_gains:
        summary_args.append("--no-gains")
    if args.legacy:
        summary_args.append("--allow-legacy-unmanifested")
    if accepted_attempts:
        summary_args.extend(["--accepted-attempts", str(accepted_attempts)])
    run_stage("per-run summary", *summary_args)

    aggregate_args = [
        pipeline.aggregate_module,
        "--input",
        str(SUMMARY_DIR / pipeline.summary_output),
        "--output",
        str(SUMMARY_DIR / pipeline.aggregate_output),
        "--expected-repetitions",
        str(args.expected_repetitions),
    ]
    if args.legacy:
        aggregate_args.append("--allow-legacy-unmanifested")
    run_stage("repetition aggregate", *aggregate_args)

    if pipeline.telemetry_pattern and pipeline.telemetry_output:
        telemetry_args = [
            "benchmark.aggregate_telemetry",
            "--pattern",
            pipeline.telemetry_pattern,
            "--output",
            str(SUMMARY_DIR / pipeline.telemetry_output),
        ]
        if args.legacy:
            telemetry_args.append("--allow-legacy-unmanifested")
        if accepted_attempts:
            telemetry_args.extend(["--accepted-attempts", str(accepted_attempts)])
        run_stage("telemetry aggregate", *telemetry_args)

    if args.ttft_slo_ms is not None and args.e2e_slo_ms is not None:
        slo_args = [
            "benchmark.analyze_slo",
            "--pattern",
            pipeline.raw_pattern,
            "--output",
            str(SUMMARY_DIR / "open-loop-steady-slo-summary.csv"),
            "--ttft-slo-ms",
            str(args.ttft_slo_ms),
            "--e2e-slo-ms",
            str(args.e2e_slo_ms),
        ]
        if args.tpot_slo_ms is not None:
            slo_args.extend(["--tpot-slo-ms", str(args.tpot_slo_ms)])
        if args.legacy:
            slo_args.append("--allow-legacy-unmanifested")
        if accepted_attempts:
            slo_args.extend(["--accepted-attempts", str(accepted_attempts)])
        run_stage("request-level SLO", *slo_args)

    if args.plot and pipeline.plot_module:
        plot_args: list[str] = [pipeline.plot_module]
        if pipeline.plot_module == "benchmark.plot_open_loop":
            plot_args.extend(
                [
                    "--aggregate",
                    str(SUMMARY_DIR / pipeline.aggregate_output),
                    "--run-summary",
                    str(SUMMARY_DIR / pipeline.summary_output),
                    "--telemetry",
                    str(SUMMARY_DIR / (pipeline.telemetry_output or "")),
                    "--output",
                    str(CHART_DIR / (pipeline.plot_output or "open-loop.png")),
                ]
            )
        run_stage("chart", *plot_args)

    print(f"\nFinished offline analysis for {pipeline.name}.")
    print(f"Per-run summary: {SUMMARY_DIR / pipeline.summary_output}")
    print(f"Aggregate:       {SUMMARY_DIR / pipeline.aggregate_output}")
    if pipeline.telemetry_output:
        print(f"Telemetry:       {SUMMARY_DIR / pipeline.telemetry_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
