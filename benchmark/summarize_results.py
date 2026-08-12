#!/usr/bin/env python3
"""CLI for turning raw benchmark JSON into a per-run summary CSV.

The implementation is intentionally an orchestrator.  The four boundaries
below keep the project readable:

* ``result_contract`` validates the shape of raw JSON;
* ``evidence_admission`` decides whether a run is trustworthy;
* ``result_normalization`` derives one canonical row;
* ``gain_calculation`` compares rows within one ExperimentSpec.

The public imports at the bottom are compatibility aliases for the other
analysis CLIs and for older notebooks.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # Support both package imports and direct execution from the repo root.
    from benchmark.evidence_admission import (
        PROJECT_ROOT,
        summary_values_equal,
        validate_complete_bundle_for_raw as _validate_complete_bundle_for_raw,
        validate_summary_evidence_rows as _validate_summary_evidence_rows,
    )
    from benchmark.evidence_manifest import load_manifest
    from benchmark.experiment_identity import (
        legacy_identity_fields,
        manifest_identity_fields,
    )
    from benchmark.gain_calculation import add_percent_gains
    from benchmark.result_contract import (
        BASE_FIELDS,
        FIELDS,
        finite_nonnegative,
        finite_request_rate,
        metadata_from,
        result_value,
        validate_result,
    )
    from benchmark.result_normalization import summarize_file
except ModuleNotFoundError:  # pragma: no cover - direct script execution.
    from evidence_admission import (  # type: ignore[no-redef]
        PROJECT_ROOT,
        summary_values_equal,
        validate_complete_bundle_for_raw as _validate_complete_bundle_for_raw,
        validate_summary_evidence_rows as _validate_summary_evidence_rows,
    )
    from evidence_manifest import load_manifest  # type: ignore[no-redef]
    from experiment_identity import (  # type: ignore[no-redef]
        legacy_identity_fields,
        manifest_identity_fields,
    )
    from gain_calculation import add_percent_gains  # type: ignore[no-redef]
    from result_contract import (  # type: ignore[no-redef]
        BASE_FIELDS,
        FIELDS,
        finite_nonnegative,
        finite_request_rate,
        metadata_from,
        result_value,
        validate_result,
    )
    from result_normalization import summarize_file  # type: ignore[no-redef]


RAW_DIR = PROJECT_ROOT / "results" / "raw"
SUMMARY_DIR = PROJECT_ROOT / "results" / "summary"
MANIFEST_DIR = PROJECT_ROOT / "results" / "manifests"


def validate_complete_bundle_for_raw(path: Path) -> str:
    """Compatibility wrapper that honours this module's configurable roots."""
    return _validate_complete_bundle_for_raw(
        path,
        project_root=PROJECT_ROOT,
        manifest_dir=MANIFEST_DIR,
    )


def validate_summary_evidence_rows(
    rows: list[dict[str, str]], *, allow_legacy_unmanifested: bool
) -> None:
    """Compatibility wrapper for aggregate consumers and test fixtures."""
    _validate_summary_evidence_rows(
        rows,
        allow_legacy_unmanifested=allow_legacy_unmanifested,
        project_root=PROJECT_ROOT,
        raw_dir=RAW_DIR,
        manifest_dir=MANIFEST_DIR,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize vLLM benchmark JSON runs into a CSV"
    )
    parser.add_argument(
        "--pattern",
        default="baseline-*.json",
        help="Glob under results/raw/ (default: baseline-*.json)",
    )
    parser.add_argument(
        "--output",
        default="baseline-summary.csv",
        help="Output CSV file (default: baseline-summary.csv)",
    )
    parser.add_argument(
        "--no-gains",
        action="store_true",
        help="Do not calculate percent gains",
    )
    parser.add_argument(
        "--allow-legacy-unmanifested",
        action="store_true",
        help=(
            "Explicitly admit historical raw files without manifests; "
            "rows are labelled legacy-unmanifested"
        ),
    )
    return parser.parse_args()


def _admit_raw(path: Path, *, allow_legacy_unmanifested: bool) -> tuple[str, dict]:
    """Return evidence status and fixed-factor identity for one raw file."""
    manifest_path = MANIFEST_DIR / f"{path.stem}.json"
    if manifest_path.exists():
        status = validate_complete_bundle_for_raw(path)
        return status, manifest_identity_fields(load_manifest(manifest_path))
    if allow_legacy_unmanifested:
        return "legacy-unmanifested", legacy_identity_fields()
    raise ValueError(
        f"Missing manifest for {path}; backfill and validate the bundle, or use "
        "--allow-legacy-unmanifested for an explicitly labelled historical rebuild"
    )


def main() -> int:
    args = parse_args()
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(RAW_DIR.glob(args.pattern))
    if not paths:
        raise ValueError(
            f"No raw result files matched {args.pattern!r} under {RAW_DIR}"
        )

    rows = []
    for path in paths:
        evidence_status, identity = _admit_raw(
            path, allow_legacy_unmanifested=args.allow_legacy_unmanifested
        )
        row = summarize_file(path)
        row.update(identity)
        row["evidence_status"] = evidence_status
        rows.append(row)

    if not args.no_gains:
        add_percent_gains(rows)
        fieldnames = FIELDS
    else:
        fieldnames = BASE_FIELDS

    output_path = SUMMARY_DIR / args.output
    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")
    for row in rows:
        print(
            f"{row['run_id']}: "
            f"output_tok_s={row['output_throughput']} "
            f"p99_ttft_ms={row['p99_ttft_ms']} "
            f"p99_tpot_ms={row['p99_tpot_ms']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
