#!/usr/bin/env python3
"""Evaluate request-level lab SLOs without calling the serving endpoint.

The resulting goodput uses vLLM's whole benchmark duration, so it is named
``benchmark_goodput`` rather than steady-state goodput. Phase 3b combines this
request-level gate with arrival-window and queue-slope evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from decimal import Decimal
from pathlib import Path

if __package__:
    from .accepted_attempts import load_accepted_run_ids
    from .evidence_manifest import load_manifest
    from .summarize_results import (
        MANIFEST_DIR,
        finite_request_rate,
        legacy_identity_fields,
        manifest_identity_fields,
        metadata_from,
        validate_complete_bundle_for_raw,
        validate_result,
    )
else:  # Support ``python3 benchmark/analyze_slo.py`` from the repository root.
    from accepted_attempts import load_accepted_run_ids  # type: ignore[no-redef]
    from evidence_manifest import load_manifest  # type: ignore[no-redef]
    from summarize_results import (  # type: ignore[no-redef]
        MANIFEST_DIR,
        finite_request_rate,
        legacy_identity_fields,
        manifest_identity_fields,
        metadata_from,
        validate_complete_bundle_for_raw,
        validate_result,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "results" / "raw"
SUMMARY_DIR = PROJECT_ROOT / "results" / "summary"


def validate_frozen_slos(
    manifest: dict,
    *,
    ttft_slo_ms: float,
    e2e_slo_ms: float,
    tpot_slo_ms: float | None,
) -> None:
    """Reject post-hoc threshold changes for a pre-registered steady run."""
    workload = manifest.get("workload")
    if not isinstance(workload, dict) or workload.get("phase") != "open_loop_steady":
        return
    config = workload.get("config")
    if not isinstance(config, dict):
        raise ValueError("steady manifest lacks frozen SLO configuration")

    requested = {
        "ttft_slo_ms": ttft_slo_ms,
        "e2e_slo_ms": e2e_slo_ms,
        "tpot_slo_ms": tpot_slo_ms,
    }
    for key, cli_value in requested.items():
        frozen_value = config.get(key, "")
        if frozen_value in (None, ""):
            if cli_value is not None:
                raise ValueError(
                    f"{key} was not pre-registered but CLI requested {cli_value}"
                )
            continue
        if cli_value is None or Decimal(str(cli_value)) != Decimal(str(frozen_value)):
            raise ValueError(
                f"{key} differs from pre-registered ExperimentSpec: "
                f"frozen={frozen_value}, CLI={cli_value}"
            )


def request_latencies(result: dict) -> list[tuple[float, float, float]]:
    """Return (TTFT, TPOT, E2E) in milliseconds for every successful request."""
    latencies: list[tuple[float, float, float]] = []
    for ttft_s, request_itls, output_len in zip(
        result["ttfts"], result["itls"], result["output_lens"]
    ):
        if not isinstance(request_itls, list):
            raise ValueError("Every itls entry must be a list")
        ttft_ms = float(ttft_s) * 1000.0
        e2e_ms = (float(ttft_s) + sum(float(value) for value in request_itls)) * 1000.0
        # ITL records streaming chunk gaps, not necessarily one entry per token.
        # Match vLLM's TPOT definition by dividing decode time by output tokens-1.
        decode_token_intervals = max(int(output_len) - 1, 0)
        tpot_ms = (
            sum(float(value) for value in request_itls)
            / decode_token_intervals
            * 1000.0
            if decode_token_intervals
            else 0.0
        )
        latencies.append((ttft_ms, tpot_ms, e2e_ms))
    return latencies


def analyze_result(
    path: Path,
    *,
    ttft_slo_ms: float,
    e2e_slo_ms: float,
    tpot_slo_ms: float | None,
) -> dict[str, str | int | float]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"{path} does not contain a JSON object")
    validate_result(result, path)
    metadata = metadata_from(result)

    latencies = request_latencies(result)
    good = 0
    for ttft_ms, tpot_ms, e2e_ms in latencies:
        if ttft_ms > ttft_slo_ms or e2e_ms > e2e_slo_ms:
            continue
        if tpot_slo_ms is not None and tpot_ms > tpot_slo_ms:
            continue
        good += 1

    duration = float(result["duration"])
    start_times = [float(value) for value in result["start_times"]]
    request_rate_value = metadata.get("request_rate", result.get("request_rate", ""))
    finite_rate = finite_request_rate(request_rate_value)
    arrival_span = ""
    realized_send_rate = ""
    if finite_rate is not None and len(start_times) >= 2:
        span = start_times[-1] - start_times[0]
        if span <= 0:
            raise ValueError(f"{path} has a non-positive arrival span")
        arrival_span = span
        realized_send_rate = (len(start_times) - 1) / span

    completed = int(result["completed"])
    return {
        "phase": metadata.get("phase", result.get("phase", "")),
        "run_id": metadata.get("run_id", result.get("run_id", path.stem)),
        "input_len": metadata.get("input_len", result.get("input_len", "")),
        "output_len": metadata.get("output_len", result.get("output_len", "")),
        "concurrency": metadata.get("concurrency", result.get("concurrency", "")),
        "request_rate": request_rate_value,
        "loadgen_seed": metadata.get("loadgen_seed", result.get("loadgen_seed", "")),
        "repetition": metadata.get("repetition", result.get("repetition", "")),
        "completed": completed,
        "failed": int(result["failed"]),
        "duration_s": duration,
        "ttft_slo_ms": ttft_slo_ms,
        "tpot_slo_ms": "" if tpot_slo_ms is None else tpot_slo_ms,
        "e2e_slo_ms": e2e_slo_ms,
        "slo_good_requests": good,
        "slo_good_fraction_pct": good / completed * 100.0,
        "benchmark_goodput_req_s": good / duration,
        "arrival_span_s": arrival_span,
        "realized_send_rate": realized_send_rate,
    }


def positive_finite(value: str) -> float:
    parsed = float(value)
    if parsed <= 0 or not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("value must be positive and finite")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate request-level lab SLO goodput"
    )
    parser.add_argument("--pattern", required=True, help="Glob under results/raw/")
    parser.add_argument(
        "--accepted-attempts",
        type=Path,
        help="TSV ledger selecting one complete attempt for each logical plan row",
    )
    parser.add_argument("--output", type=Path, default=SUMMARY_DIR / "slo-summary.csv")
    parser.add_argument("--ttft-slo-ms", type=positive_finite, required=True)
    parser.add_argument("--e2e-slo-ms", type=positive_finite, required=True)
    parser.add_argument("--tpot-slo-ms", type=positive_finite)
    parser.add_argument(
        "--allow-legacy-unmanifested",
        action="store_true",
        help="Explicitly analyze historical raw files without complete manifests",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.accepted_attempts:
        if args.allow_legacy_unmanifested:
            raise ValueError(
                "--accepted-attempts cannot be combined with legacy admission"
            )
        run_ids = load_accepted_run_ids(
            args.accepted_attempts, project_root=PROJECT_ROOT
        )
        paths = [RAW_DIR / f"{run_id}.json" for run_id in run_ids]
    else:
        paths = sorted(RAW_DIR.glob(args.pattern))
    if not paths:
        raise ValueError(
            f"No raw result files matched {args.pattern!r} under {RAW_DIR}"
        )
    missing_paths = [str(path) for path in paths if not path.is_file()]
    if missing_paths:
        raise ValueError(
            "Accepted raw result files are missing: " + ", ".join(missing_paths)
        )
    rows = []
    for path in paths:
        manifest_path = MANIFEST_DIR / f"{path.stem}.json"
        if manifest_path.exists():
            evidence_status = validate_complete_bundle_for_raw(path)
            manifest = load_manifest(manifest_path)
            validate_frozen_slos(
                manifest,
                ttft_slo_ms=args.ttft_slo_ms,
                e2e_slo_ms=args.e2e_slo_ms,
                tpot_slo_ms=args.tpot_slo_ms,
            )
            identity = manifest_identity_fields(manifest)
        elif args.allow_legacy_unmanifested:
            evidence_status = "legacy-unmanifested"
            identity = legacy_identity_fields()
        else:
            raise ValueError(
                f"Missing complete manifest for {path}; use "
                "--allow-legacy-unmanifested only for an explicitly labelled "
                "historical analysis"
            )
        row = analyze_result(
            path,
            ttft_slo_ms=args.ttft_slo_ms,
            e2e_slo_ms=args.e2e_slo_ms,
            tpot_slo_ms=args.tpot_slo_ms,
        )
        row = {"evidence_status": evidence_status, **identity, **row}
        rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} SLO rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
