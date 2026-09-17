#!/usr/bin/env python3
"""Persist the one accepted run attempt for every row in a Phase 3b plan.

Run manifests are immutable, so retries create new run IDs instead of replacing
failed evidence.  This ledger is the explicit bridge from those physical
attempts back to the logical rows in the pre-registered execution plan.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # Support package import and direct execution.
    from benchmark.evidence_manifest import load_manifest, sha256_file
except ModuleNotFoundError:  # pragma: no cover - direct script execution.
    from evidence_manifest import load_manifest, sha256_file  # type: ignore[no-redef]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEDGER_SCHEMA_VERSION = "1"
PLAN_IDENTITY_FIELDS = (
    "block",
    "order_in_block",
    "repetition",
    "loadgen_seed",
    "request_rate",
    "num_prompts",
)
LEDGER_FIELDS = (
    "ledger_schema_version",
    "plan_path",
    "plan_sha256",
    *PLAN_IDENTITY_FIELDS,
    "accepted_run_id",
    "accepted_attempt",
    "accepted_execution_order",
    "accepted_started_at_utc",
    "accepted_completed_at_utc",
    "attempt_history",
)


class AcceptedAttemptsError(ValueError):
    """Raised when attempts cannot form an unambiguous accepted-run mapping."""


def _project_path(path: Path, project_root: Path) -> Path:
    root = project_root.resolve()
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise AcceptedAttemptsError(
            f"path must remain inside project root {root}: {path}"
        ) from error
    return resolved


def _relative_project_path(path: Path, project_root: Path) -> str:
    return (
        _project_path(path, project_root).relative_to(project_root.resolve()).as_posix()
    )


def _read_tsv(path: Path, required_fields: Sequence[str]) -> list[dict[str, str]]:
    try:
        input_file = path.open(newline="", encoding="utf-8")
    except OSError as error:
        raise AcceptedAttemptsError(f"cannot read {path}: {error}") from error
    with input_file:
        reader = csv.DictReader(input_file, delimiter="\t")
        missing = [
            field for field in required_fields if field not in (reader.fieldnames or [])
        ]
        if missing:
            raise AcceptedAttemptsError(
                f"{path} is missing required columns: {', '.join(missing)}"
            )
        rows = list(reader)
    if not rows:
        raise AcceptedAttemptsError(f"{path} contains no rows")
    return rows


def _decimal_equal(actual: object, expected: object) -> bool:
    if str(actual) == str(expected):
        return True
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return False


def _require_value(
    actual: object, expected: object, *, run_id: str, field: str
) -> None:
    if not _decimal_equal(actual, expected):
        raise AcceptedAttemptsError(
            f"attempt {run_id} does not match plan field {field!r}: "
            f"plan={expected!r}, manifest={actual!r}"
        )


def _lifecycle_timestamp(manifest: Mapping[str, Any], status: str) -> str:
    lifecycle = manifest.get("lifecycle")
    if isinstance(lifecycle, list):
        for event in lifecycle:
            if isinstance(event, dict) and event.get("status") == status:
                timestamp = event.get("timestamp_utc")
                if isinstance(timestamp, str):
                    return timestamp
    fallback = manifest.get(
        "created_at_utc" if status == "running" else "updated_at_utc"
    )
    return fallback if isinstance(fallback, str) else ""


def _attempt_for_plan_row(
    manifest: Mapping[str, Any],
    plan_row: Mapping[str, str],
    *,
    plan_relative_path: str,
    plan_sha256: str,
) -> dict[str, Any]:
    run_id = manifest.get("run_id")
    workload = manifest.get("workload")
    if not isinstance(run_id, str) or not isinstance(workload, dict):
        raise AcceptedAttemptsError("attempt manifest lacks run_id/workload")
    config = workload.get("config")
    if not isinstance(config, dict):
        raise AcceptedAttemptsError(f"attempt {run_id} lacks workload.config")

    manifest_values = {
        "block": config.get("plan_block"),
        "order_in_block": config.get("plan_order"),
        "repetition": workload.get("repetition"),
        "loadgen_seed": workload.get("loadgen_seed"),
        "request_rate": workload.get("request_rate"),
        "num_prompts": workload.get("num_prompts"),
        "arrival_window_seconds": config.get("nominal_arrival_horizon_seconds"),
        "plan_shuffle_seed": config.get("plan_shuffle_seed"),
        "run_namespace": config.get("run_namespace"),
        "input_len": workload.get("input_len"),
        "output_len": workload.get("output_len"),
        "max_concurrency": workload.get("concurrency"),
        "num_warmups": workload.get("num_warmups"),
        "random_range_ratio": workload.get("random_range_ratio"),
        "ttft_slo_ms": config.get("ttft_slo_ms"),
        "e2e_slo_ms": config.get("e2e_slo_ms"),
        "tpot_slo_ms": config.get("tpot_slo_ms", ""),
        "plan_path": config.get("plan_path"),
        "plan_sha256": config.get("plan_sha256"),
    }
    for field, expected in plan_row.items():
        if field in manifest_values:
            _require_value(manifest_values[field], expected, run_id=run_id, field=field)
    _require_value(
        manifest_values["plan_path"],
        plan_relative_path,
        run_id=run_id,
        field="plan_path",
    )
    _require_value(
        manifest_values["plan_sha256"],
        plan_sha256,
        run_id=run_id,
        field="plan_sha256",
    )

    raw_attempt = config.get("run_attempt")
    try:
        attempt = int(str(raw_attempt))
    except (TypeError, ValueError) as error:
        raise AcceptedAttemptsError(
            f"attempt {run_id} has invalid run_attempt {raw_attempt!r}"
        ) from error
    if attempt <= 0:
        raise AcceptedAttemptsError(f"attempt {run_id} has non-positive run_attempt")

    expected_run_id = (
        f"open_loop_steady-{plan_row['run_namespace']}"
        f"-in{plan_row['input_len']}-out{plan_row['output_len']}"
        f"-l{plan_row['request_rate']}-s{plan_row['loadgen_seed']}"
        f"-b{plan_row['block']}-a{attempt}"
    )
    if run_id != expected_run_id:
        raise AcceptedAttemptsError(
            f"attempt run_id does not encode its plan row: expected "
            f"{expected_run_id!r}, got {run_id!r}"
        )
    return {
        "run_id": run_id,
        "attempt": attempt,
        "status": manifest.get("status"),
        "started_at_utc": _lifecycle_timestamp(manifest, "running"),
        "completed_at_utc": _lifecycle_timestamp(manifest, "complete"),
    }


def build_ledger_rows(
    *,
    plan_path: Path,
    manifest_dir: Path,
    project_root: Path = PROJECT_ROOT,
    allow_incomplete: bool = False,
) -> list[dict[str, str | int]]:
    """Build one ledger row per plan row without mutating any run bundle."""
    plan_path = _project_path(plan_path, project_root)
    manifest_dir = _project_path(manifest_dir, project_root)
    plan_rows = _read_tsv(
        plan_path,
        (
            *PLAN_IDENTITY_FIELDS,
            "arrival_window_seconds",
            "plan_shuffle_seed",
            "run_namespace",
            "input_len",
            "output_len",
            "max_concurrency",
            "num_warmups",
            "random_range_ratio",
            "ttft_slo_ms",
            "e2e_slo_ms",
            "tpot_slo_ms",
        ),
    )
    logical_keys = [(row["block"], row["order_in_block"]) for row in plan_rows]
    if len(set(logical_keys)) != len(logical_keys):
        raise AcceptedAttemptsError(f"{plan_path} contains duplicate plan rows")

    namespaces = {row["run_namespace"] for row in plan_rows}
    if len(namespaces) != 1 or not next(iter(namespaces)):
        raise AcceptedAttemptsError("plan must contain one non-empty run_namespace")
    namespace = next(iter(namespaces))
    plan_relative_path = _relative_project_path(plan_path, project_root)
    plan_digest = sha256_file(plan_path)
    rows_by_key = {(row["block"], row["order_in_block"]): row for row in plan_rows}
    attempts_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key in rows_by_key
    }

    pattern = f"open_loop_steady-{namespace}-*.json"
    for manifest_path in sorted(manifest_dir.glob(pattern)):
        manifest = load_manifest(manifest_path)
        workload = manifest.get("workload")
        config = workload.get("config") if isinstance(workload, dict) else None
        if not isinstance(config, dict):
            raise AcceptedAttemptsError(f"{manifest_path} lacks workload.config")
        if not _decimal_equal(config.get("plan_sha256"), plan_digest):
            raise AcceptedAttemptsError(
                f"run namespace {namespace!r} is bound to a different plan in "
                f"{manifest_path}"
            )
        key = (str(config.get("plan_block", "")), str(config.get("plan_order", "")))
        plan_row = rows_by_key.get(key)
        if plan_row is None:
            raise AcceptedAttemptsError(
                f"attempt {manifest.get('run_id')} does not match any row in {plan_path}"
            )
        attempts_by_key[key].append(
            _attempt_for_plan_row(
                manifest,
                plan_row,
                plan_relative_path=plan_relative_path,
                plan_sha256=plan_digest,
            )
        )

    accepted_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    incomplete_keys: list[str] = []
    for key, attempts in attempts_by_key.items():
        attempt_numbers = [attempt["attempt"] for attempt in attempts]
        if len(set(attempt_numbers)) != len(attempt_numbers):
            raise AcceptedAttemptsError(
                f"plan row block={key[0]}, order={key[1]} has duplicate attempt numbers"
            )
        complete = [attempt for attempt in attempts if attempt["status"] == "complete"]
        if len(complete) > 1:
            run_ids = ", ".join(attempt["run_id"] for attempt in complete)
            raise AcceptedAttemptsError(
                f"plan row block={key[0]}, order={key[1]} has multiple complete "
                f"attempts; choose explicitly before analysis: {run_ids}"
            )
        if complete:
            accepted_by_key[key] = complete[0]
        else:
            incomplete_keys.append(f"b{key[0]}/o{key[1]}")
    if incomplete_keys and not allow_incomplete:
        raise AcceptedAttemptsError(
            "plan has no complete accepted attempt for: " + ", ".join(incomplete_keys)
        )

    ordered_accepted = sorted(
        accepted_by_key.items(),
        key=lambda item: (item[1]["started_at_utc"], item[1]["run_id"]),
    )
    execution_order = {
        key: index for index, (key, _attempt) in enumerate(ordered_accepted, start=1)
    }

    ledger_rows: list[dict[str, str | int]] = []
    for plan_row in plan_rows:
        key = (plan_row["block"], plan_row["order_in_block"])
        attempts = sorted(attempts_by_key[key], key=lambda item: item["attempt"])
        accepted = accepted_by_key.get(key)
        ledger_rows.append(
            {
                "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                "plan_path": plan_relative_path,
                "plan_sha256": plan_digest,
                **{field: plan_row[field] for field in PLAN_IDENTITY_FIELDS},
                "accepted_run_id": accepted["run_id"] if accepted else "",
                "accepted_attempt": accepted["attempt"] if accepted else "",
                "accepted_execution_order": execution_order.get(key, ""),
                "accepted_started_at_utc": (
                    accepted["started_at_utc"] if accepted else ""
                ),
                "accepted_completed_at_utc": (
                    accepted["completed_at_utc"] if accepted else ""
                ),
                "attempt_history": ",".join(
                    f"a{attempt['attempt']}:{attempt['status']}" for attempt in attempts
                ),
            }
        )
    return ledger_rows


def write_ledger(rows: Sequence[Mapping[str, object]], output_path: Path) -> None:
    """Atomically replace the derived ledger, never an immutable run manifest."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent
    )
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=LEDGER_FIELDS,
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_name, output_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def load_accepted_run_ids(
    ledger_path: Path, *, project_root: Path = PROJECT_ROOT
) -> list[str]:
    """Validate a complete ledger and return accepted run IDs in plan order."""
    ledger_path = _project_path(ledger_path, project_root)
    rows = _read_tsv(ledger_path, LEDGER_FIELDS)
    versions = {row["ledger_schema_version"] for row in rows}
    if versions != {LEDGER_SCHEMA_VERSION}:
        raise AcceptedAttemptsError(
            f"{ledger_path} has unsupported ledger schema versions: {sorted(versions)}"
        )
    plan_paths = {row["plan_path"] for row in rows}
    plan_hashes = {row["plan_sha256"] for row in rows}
    if len(plan_paths) != 1 or len(plan_hashes) != 1:
        raise AcceptedAttemptsError(f"{ledger_path} mixes execution plans")
    plan_path = _project_path(Path(next(iter(plan_paths))), project_root)
    plan_digest = next(iter(plan_hashes))
    if sha256_file(plan_path) != plan_digest:
        raise AcceptedAttemptsError(
            f"execution plan SHA256 no longer matches ledger {ledger_path}"
        )
    plan_rows = _read_tsv(
        plan_path,
        (*PLAN_IDENTITY_FIELDS, "run_namespace", "input_len", "output_len"),
    )
    if len(rows) != len(plan_rows):
        raise AcceptedAttemptsError(
            f"{ledger_path} has {len(rows)} rows; plan has {len(plan_rows)}"
        )
    for index, (ledger_row, plan_row) in enumerate(zip(rows, plan_rows), start=1):
        for field in PLAN_IDENTITY_FIELDS:
            if not _decimal_equal(ledger_row[field], plan_row[field]):
                raise AcceptedAttemptsError(
                    f"ledger row {index} does not match plan field {field!r}"
                )

    missing = [
        f"b{row['block']}/o{row['order_in_block']}"
        for row in rows
        if not row["accepted_run_id"]
    ]
    if missing:
        raise AcceptedAttemptsError(
            "accepted-attempt ledger is incomplete for: " + ", ".join(missing)
        )
    run_ids = [row["accepted_run_id"] for row in rows]
    if len(set(run_ids)) != len(run_ids):
        raise AcceptedAttemptsError(
            f"{ledger_path} contains duplicate accepted run IDs"
        )
    for index, (ledger_row, plan_row) in enumerate(zip(rows, plan_rows), start=1):
        try:
            attempt = int(ledger_row["accepted_attempt"])
        except ValueError as error:
            raise AcceptedAttemptsError(
                f"ledger row {index} has an invalid accepted_attempt"
            ) from error
        if attempt <= 0:
            raise AcceptedAttemptsError(
                f"ledger row {index} has a non-positive accepted_attempt"
            )
        expected_run_id = (
            f"open_loop_steady-{plan_row['run_namespace']}"
            f"-in{plan_row['input_len']}-out{plan_row['output_len']}"
            f"-l{plan_row['request_rate']}-s{plan_row['loadgen_seed']}"
            f"-b{plan_row['block']}-a{attempt}"
        )
        if ledger_row["accepted_run_id"] != expected_run_id:
            raise AcceptedAttemptsError(
                f"ledger row {index} accepted_run_id does not match its plan row: "
                f"expected {expected_run_id!r}, got "
                f"{ledger_row['accepted_run_id']!r}"
            )
        if f"a{attempt}:complete" not in ledger_row["attempt_history"].split(","):
            raise AcceptedAttemptsError(
                f"ledger row {index} attempt_history does not mark the accepted "
                "attempt complete"
            )

    try:
        execution_orders = [int(row["accepted_execution_order"]) for row in rows]
    except ValueError as error:
        raise AcceptedAttemptsError(
            f"{ledger_path} has an invalid accepted_execution_order"
        ) from error
    if set(execution_orders) != set(range(1, len(rows) + 1)):
        raise AcceptedAttemptsError(
            f"{ledger_path} accepted_execution_order is not a complete permutation"
        )
    return run_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Map immutable Phase 3b attempts to accepted plan rows"
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=PROJECT_ROOT / "results" / "manifests",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Write blank accepted fields for plan rows without a complete attempt",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_path = _project_path(args.output, PROJECT_ROOT)
    rows = build_ledger_rows(
        plan_path=args.plan,
        manifest_dir=args.manifest_dir,
        allow_incomplete=args.allow_incomplete,
    )
    write_ledger(rows, output_path)
    accepted = sum(bool(row["accepted_run_id"]) for row in rows)
    print(
        f"Wrote accepted-attempt ledger: {output_path} ({accepted}/{len(rows)} accepted)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
