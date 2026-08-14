#!/usr/bin/env python3

"""Create and update transactional evidence manifests for benchmark runs.

The file is intentionally organised around one lifecycle:

1. ``build_manifest`` records the planned run and artifact names;
2. ``transition_status`` moves ``planned -> running``;
3. collectors write artifacts and ``refresh_artifacts`` records hashes;
4. ``mark_complete`` is allowed only after the validator passes;
5. ``mark_failed`` preserves incomplete evidence for diagnosis.

The manifest is metadata about a RunBundle, not a second copy of benchmark
metrics. Raw result values remain in the raw JSON and are validated there.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
TERMINAL_STATES = {"complete", "failed"}
ALLOWED_TRANSITIONS = {
    "planned": {"running", "failed"},
    "running": {"complete", "failed"},
    "complete": set(),
    "failed": set(),
}
EXPECTED_ARTIFACT_NAMES = (
    "raw_result",
    "benchmark_log",
    "gpu_telemetry",
    "metrics_timeseries",
    "metrics_before",
    "metrics_after",
    "docker_before",
    "docker_after",
)
EVIDENCE_ROOTS = {
    "raw_result": Path("results/raw"),
    "benchmark_log": Path("artifacts/logs"),
    "gpu_telemetry": Path("results/telemetry"),
    "metrics_timeseries": Path("results/telemetry"),
    "metrics_before": Path("results/telemetry"),
    "metrics_after": Path("results/telemetry"),
    "docker_before": Path("results/telemetry"),
    "docker_after": Path("results/telemetry"),
}
ARTIFACT_FILENAME_SUFFIXES = {
    "raw_result": ".json",
    "benchmark_log": ".log",
    "gpu_telemetry": "-gpu.csv",
    "metrics_timeseries": "-metrics.jsonl",
    "metrics_before": "-metrics-before.txt",
    "metrics_after": "-metrics-after.txt",
    "docker_before": "-docker-before.txt",
    "docker_after": "-docker-after.txt",
}


class ManifestError(ValueError):
    """Raised when a manifest operation is invalid or unsafe."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_git_bytes(
    project_root: Path, *args: str
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=False,
        capture_output=True,
    )


def _untracked_tree_hash(
    project_root: Path, *, exclude_top_level: frozenset[str] = frozenset()
) -> tuple[str, int]:
    listing = _run_git_bytes(
        project_root, "ls-files", "--others", "--exclude-standard", "-z"
    )
    if listing.returncode != 0:
        raise ManifestError(
            listing.stderr.decode("utf-8", errors="replace").strip()
            or "could not enumerate untracked files"
        )
    paths = [
        path
        for path in listing.stdout.split(b"\0")
        if path and Path(os.fsdecode(path)).parts[0] not in exclude_top_level
    ]
    digest = hashlib.sha256()
    root = project_root.resolve()
    for raw_relative in paths:
        relative = Path(os.fsdecode(raw_relative))
        unresolved = root / relative
        resolved = unresolved.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ManifestError(
                f"untracked path escapes project root: {relative}"
            ) from error
        digest.update(raw_relative)
        digest.update(b"\0")
        if unresolved.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(unresolved).encode("utf-8"))
        elif resolved.is_file():
            digest.update(b"file\0")
            with resolved.open("rb") as input_file:
                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"other\0")
    return digest.hexdigest(), len(paths)


def git_provenance(project_root: Path) -> dict[str, Any]:
    revision = _run_git(project_root, "rev-parse", "HEAD")
    status = _run_git(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    tracked_diff = _run_git_bytes(project_root, "diff", "--binary", "HEAD", "--")
    if (
        revision.returncode != 0
        or status.returncode != 0
        or tracked_diff.returncode != 0
    ):
        return {
            "sha": None,
            "dirty": None,
            "status_sha256": None,
            "tracked_diff_sha256": None,
            "untracked_tree_sha256": None,
            "untracked_file_count": None,
            "untracked_source_tree_sha256": None,
            "untracked_source_file_count": None,
            "capture_error": (
                revision.stderr
                or status.stderr
                or tracked_diff.stderr.decode("utf-8", errors="replace")
            ).strip()
            or "not a Git worktree",
        }

    status_text = status.stdout
    try:
        untracked_hash, untracked_count = _untracked_tree_hash(project_root)
        source_hash, source_count = _untracked_tree_hash(
            project_root,
            exclude_top_level=frozenset({"artifacts", "charts", "results"}),
        )
    except (ManifestError, OSError) as error:
        untracked_hash = None
        untracked_count = None
        source_hash = None
        source_count = None
        capture_error = str(error)
    else:
        capture_error = None
    return {
        "sha": revision.stdout.strip(),
        "dirty": bool(status_text),
        "status_sha256": hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(tracked_diff.stdout).hexdigest(),
        "untracked_tree_sha256": untracked_hash,
        "untracked_file_count": untracked_count,
        "untracked_source_tree_sha256": source_hash,
        "untracked_source_file_count": source_count,
        "capture_error": capture_error,
    }


def parse_key_value(values: Iterable[str], *, option: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        key, separator, item_value = value.partition("=")
        if not separator or not key:
            raise ManifestError(f"{option} must use NAME=VALUE, got {value!r}")
        if key in parsed:
            raise ManifestError(f"duplicate {option} key: {key}")
        parsed[key] = item_value
    return parsed


def validate_artifact_path(project_root: Path, name: str, path: Path) -> Path:
    if name not in EVIDENCE_ROOTS:
        raise ManifestError(f"unknown artifact name: {name}")
    root = project_root.resolve()
    resolved = path.resolve()
    evidence_root = (root / EVIDENCE_ROOTS[name]).resolve()
    try:
        evidence_root.relative_to(root)
    except ValueError as error:
        raise ManifestError(
            f"evidence root for {name!r} escapes project root: {evidence_root}"
        ) from error
    try:
        resolved.relative_to(evidence_root)
    except ValueError as error:
        raise ManifestError(
            f"artifact {name!r} must be inside {evidence_root}: {path}"
        ) from error
    return resolved


def _relative_artifact_path(project_root: Path, name: str, path: Path) -> str:
    root = project_root.resolve()
    return validate_artifact_path(project_root, name, path).relative_to(root).as_posix()


def artifact_records(
    project_root: Path, artifacts: Mapping[str, Path]
) -> list[dict[str, Any]]:
    missing_names = set(EXPECTED_ARTIFACT_NAMES) - set(artifacts)
    extra_names = set(artifacts) - set(EXPECTED_ARTIFACT_NAMES)
    if missing_names or extra_names:
        details: list[str] = []
        if missing_names:
            details.append(f"missing {sorted(missing_names)}")
        if extra_names:
            details.append(f"unexpected {sorted(extra_names)}")
        raise ManifestError("artifact set mismatch: " + "; ".join(details))

    records: list[dict[str, Any]] = []
    for name in EXPECTED_ARTIFACT_NAMES:
        path = artifacts[name]
        exists = path.is_file()
        size = path.stat().st_size if exists else None
        records.append(
            {
                "name": name,
                "path": _relative_artifact_path(project_root, name, path),
                "required": True,
                "exists": exists,
                "size_bytes": size,
                "sha256": sha256_file(path) if exists and size else None,
            }
        )
    return records


def refresh_artifacts(manifest: dict[str, Any], project_root: Path) -> None:
    artifacts = {
        item["name"]: project_root / item["path"]
        for item in manifest.get("artifacts", [])
    }
    manifest["artifacts"] = artifact_records(project_root, artifacts)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as output_file:
            json.dump(payload, output_file, indent=2, sort_keys=True)
            output_file.write("\n")
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def exclusive_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Claim a run ID exclusively, then publish its manifest atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    claim_path = path.with_name(f".{path.name}.create.lock")
    try:
        claim_descriptor = os.open(
            claim_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
        )
    except FileExistsError as error:
        raise ManifestError(
            f"manifest creation is already claimed: {claim_path}"
        ) from error
    try:
        with os.fdopen(claim_descriptor, "w", encoding="utf-8") as claim_file:
            claim_file.write(f"pid={os.getpid()}\n")
            claim_file.flush()
            os.fsync(claim_file.fileno())
        if path.exists():
            raise ManifestError(f"refusing to overwrite manifest: {path}")
        atomic_write_json(path, payload)
    finally:
        try:
            claim_path.unlink()
        except FileNotFoundError:
            pass


def validate_manifest_path(
    project_root: Path, path: Path, run_id: str | None = None
) -> Path:
    project_root = project_root.resolve()
    manifests_root = (project_root / "results/manifests").resolve()
    try:
        manifests_root.relative_to(project_root)
    except ValueError as error:
        raise ManifestError(
            f"manifest root escapes project root: {manifests_root}"
        ) from error
    resolved = path.resolve()
    try:
        resolved.relative_to(manifests_root)
    except ValueError as error:
        raise ManifestError(
            f"manifest must be inside {manifests_root}: {resolved}"
        ) from error
    if run_id is not None and resolved.name != f"{run_id}.json":
        raise ManifestError(
            f"manifest filename must match run_id {run_id!r}: {resolved.name}"
        )
    return resolved


def validate_manifest_invariants(payload: Mapping[str, Any]) -> None:
    run_id = payload.get("run_id")
    status = payload.get("status")
    if not isinstance(run_id, str) or not run_id:
        raise ManifestError("manifest run_id must be a non-empty string")
    if status not in ALLOWED_TRANSITIONS:
        raise ManifestError(f"manifest status is invalid: {status!r}")

    lifecycle = payload.get("lifecycle")
    if not isinstance(lifecycle, list) or not lifecycle:
        raise ManifestError("manifest lifecycle must be a non-empty array")
    lifecycle_states: list[str] = []
    for event in lifecycle:
        if (
            not isinstance(event, dict)
            or event.get("status") not in ALLOWED_TRANSITIONS
        ):
            raise ManifestError("manifest lifecycle contains an invalid event")
        lifecycle_states.append(event["status"])
    if lifecycle_states[0] != "planned" or lifecycle_states[-1] != status:
        raise ManifestError(
            f"manifest lifecycle does not terminate in status {status!r}"
        )
    for previous, current in zip(lifecycle_states, lifecycle_states[1:]):
        allowed = set(ALLOWED_TRANSITIONS[previous])
        if (
            previous == "planned"
            and current == "complete"
            and payload.get("provenance_capture") == "backfilled"
        ):
            allowed.add("complete")
        if current not in allowed:
            raise ManifestError(
                f"manifest lifecycle has invalid transition {previous!r}->{current!r}"
            )

    validation = payload.get("validation")
    failure = payload.get("failure")
    if not isinstance(validation, dict):
        raise ManifestError("manifest validation must be an object")
    if status == "complete":
        if validation.get("status") != "passed" or not validation.get(
            "validated_at_utc"
        ):
            raise ManifestError("complete manifest must record passed validation")
        if failure is not None:
            raise ManifestError("complete manifest cannot contain a failure")
    elif status == "failed":
        if not isinstance(failure, dict):
            raise ManifestError("failed manifest must record a failure")
        if validation.get("status") == "passed":
            raise ManifestError("failed manifest cannot record passed validation")
    elif failure is not None:
        raise ManifestError(f"{status} manifest cannot contain a failure")

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        raise ManifestError("manifest artifacts must be an array")
    records = {
        record.get("name"): record for record in artifacts if isinstance(record, dict)
    }
    if set(records) != set(EXPECTED_ARTIFACT_NAMES) or len(records) != len(artifacts):
        raise ManifestError("manifest artifact names are incomplete or duplicated")
    for name, suffix in ARTIFACT_FILENAME_SUFFIXES.items():
        record = records[name]
        artifact_path = record.get("path")
        if (
            not isinstance(artifact_path, str)
            or Path(artifact_path).name != f"{run_id}{suffix}"
        ):
            raise ManifestError(
                f"manifest artifact {name!r} filename does not match run_id {run_id!r}"
            )
        if status == "complete" and (
            record.get("exists") is not True
            or not isinstance(record.get("size_bytes"), int)
            or record["size_bytes"] <= 0
            or not isinstance(record.get("sha256"), str)
        ):
            raise ManifestError(
                f"complete manifest artifact {name!r} lacks size/hash evidence"
            )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ManifestError(f"invalid manifest JSON {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ManifestError(f"manifest root must be an object: {path}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported manifest schema_version: {payload.get('schema_version')!r}"
        )
    validate_manifest_invariants(payload)
    return payload


def build_manifest(
    *,
    project_root: Path,
    run_id: str,
    phase: str,
    input_len: int,
    output_len: int,
    concurrency: int,
    repetition: int,
    num_prompts: int,
    num_warmups: int,
    request_rate: str,
    random_range_ratio: str,
    loadgen_seed: int | None,
    image: str,
    model: str,
    model_revision: str,
    tokenizer_snapshot: str,
    server_settings: Mapping[str, str],
    workload_settings: Mapping[str, str],
    artifacts: Mapping[str, Path],
    provenance_capture: str,
) -> dict[str, Any]:
    if provenance_capture not in {"run_start", "backfilled"}:
        raise ManifestError(f"invalid provenance capture mode: {provenance_capture}")
    created_at = utc_now()
    image_name, separator, image_digest = image.partition("@sha256:")
    represents_run_time = provenance_capture == "run_start"
    capture_scope = "run_start" if represents_run_time else "manifest_backfill_time"
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "planned",
        "created_at_utc": created_at,
        "updated_at_utc": created_at,
        "provenance_capture": provenance_capture,
        "provenance": {
            "capture_mode": provenance_capture,
            "captured_at_utc": created_at,
            "represents_run_time": represents_run_time,
            "note": (
                "Captured by the runner before this run started."
                if represents_run_time
                else "Current checkout/config snapshot only; historical run-time provenance is unknown."
            ),
        },
        "git": {**git_provenance(project_root), "capture_scope": capture_scope},
        "server": {
            "capture_scope": capture_scope,
            "image": image,
            "image_name": image_name,
            "image_digest": f"sha256:{image_digest}" if separator else None,
            "model": model,
            "model_revision": model_revision,
            "tokenizer_snapshot": tokenizer_snapshot,
            "config": dict(sorted(server_settings.items())),
        },
        "workload": {
            "capture_scope": capture_scope,
            "phase": phase,
            "input_len": input_len,
            "output_len": output_len,
            "concurrency": concurrency,
            "repetition": repetition,
            "num_prompts": num_prompts,
            "num_warmups": num_warmups,
            "request_rate": request_rate,
            "random_range_ratio": random_range_ratio,
            "loadgen_seed": loadgen_seed,
            "config": dict(sorted(workload_settings.items())),
        },
        "collectors": {
            "gpu": {"exit_code": None, "acceptable": None},
            "metrics": {"exit_code": None, "acceptable": None},
            "benchmark": {"exit_code": None, "acceptable": None},
        },
        "artifacts": artifact_records(project_root, artifacts),
        "validation": {"status": "pending", "validated_at_utc": None},
        "failure": None,
        "lifecycle": [
            {
                "status": "planned",
                "timestamp_utc": created_at,
                "reason": provenance_capture,
            }
        ],
    }


def transition_status(
    manifest: dict[str, Any],
    new_status: str,
    *,
    reason: str | None = None,
    allow_backfill_complete: bool = False,
) -> None:
    current_status = manifest.get("status")
    allowed = set(ALLOWED_TRANSITIONS.get(current_status, set()))
    if (
        allow_backfill_complete
        and current_status == "planned"
        and new_status == "complete"
        and manifest.get("provenance_capture") == "backfilled"
    ):
        allowed.add("complete")
    if new_status not in allowed:
        raise ManifestError(
            f"invalid manifest transition: {current_status!r} -> {new_status!r}"
        )
    timestamp = utc_now()
    manifest["status"] = new_status
    manifest["updated_at_utc"] = timestamp
    manifest.setdefault("lifecycle", []).append(
        {"status": new_status, "timestamp_utc": timestamp, "reason": reason}
    )


def mark_complete(manifest: dict[str, Any], project_root: Path) -> None:
    refresh_artifacts(manifest, project_root)
    timestamp = utc_now()
    manifest["validation"] = {"status": "passed", "validated_at_utc": timestamp}
    transition_status(
        manifest,
        "complete",
        reason="evidence validation passed",
        allow_backfill_complete=True,
    )


def mark_failed(
    manifest: dict[str, Any], project_root: Path, *, exit_code: int, reason: str
) -> None:
    if manifest.get("status") in TERMINAL_STATES:
        raise ManifestError(
            f"cannot fail terminal manifest in state {manifest.get('status')!r}"
        )
    refresh_artifacts(manifest, project_root)
    manifest["failure"] = {
        "exit_code": exit_code,
        "reason": reason,
        "failed_at_utc": utc_now(),
    }
    manifest["validation"] = {
        "status": "failed" if reason.startswith("evidence validation") else "not_run",
        "validated_at_utc": None,
    }
    transition_status(manifest, "failed", reason=reason)


def record_exit_codes(
    manifest: dict[str, Any],
    *,
    gpu: int,
    metrics: int,
    benchmark: int,
    gpu_stop_requested_by_runner: bool = True,
    metrics_stop_requested_by_runner: bool = True,
) -> None:
    if manifest.get("status") in TERMINAL_STATES:
        raise ManifestError("cannot record process exits on a terminal manifest")
    manifest["collectors"] = {
        "gpu": {
            "exit_code": gpu,
            "acceptable": gpu == 0
            or (gpu in {130, 143} and gpu_stop_requested_by_runner),
            "stop_requested_by_runner": gpu_stop_requested_by_runner,
        },
        "metrics": {
            "exit_code": metrics,
            "acceptable": metrics == 0,
            "stop_requested_by_runner": metrics_stop_requested_by_runner,
        },
        "benchmark": {"exit_code": benchmark, "acceptable": benchmark == 0},
    }
    manifest["updated_at_utc"] = utc_now()


def _artifacts_from_args(project_root: Path, values: Sequence[str]) -> dict[str, Path]:
    parsed = parse_key_value(values, option="--artifact")
    return {
        name: Path(value) if Path(value).is_absolute() else project_root / value
        for name, value in parsed.items()
    }


def _add_manifest_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create a planned manifest")
    _add_manifest_path(create)
    create.add_argument("--run-id", required=True)
    create.add_argument("--phase", required=True)
    create.add_argument("--input-len", required=True, type=int)
    create.add_argument("--output-len", required=True, type=int)
    create.add_argument("--concurrency", required=True, type=int)
    create.add_argument("--repetition", required=True, type=int)
    create.add_argument("--num-prompts", required=True, type=int)
    create.add_argument("--num-warmups", required=True, type=int)
    create.add_argument("--request-rate", required=True)
    create.add_argument("--random-range-ratio", required=True)
    create.add_argument("--loadgen-seed", type=int)
    create.add_argument("--image", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--model-revision", required=True)
    create.add_argument("--tokenizer-snapshot", required=True)
    create.add_argument("--server-setting", action="append", default=[])
    create.add_argument("--workload-setting", action="append", default=[])
    create.add_argument("--artifact", action="append", required=True)
    create.add_argument(
        "--provenance-capture", choices=("run_start", "backfilled"), default="run_start"
    )

    set_status = subparsers.add_parser(
        "set-status", help="transition a manifest to running"
    )
    _add_manifest_path(set_status)
    set_status.add_argument("--status", required=True, choices=("running",))
    set_status.add_argument("--reason")

    exits = subparsers.add_parser(
        "record-exits", help="record benchmark and collector exits"
    )
    _add_manifest_path(exits)
    exits.add_argument("--gpu-exit-code", required=True, type=int)
    exits.add_argument("--metrics-exit-code", required=True, type=int)
    exits.add_argument("--benchmark-exit-code", required=True, type=int)
    exits.add_argument("--gpu-stop-requested-by-runner", action="store_true")
    exits.add_argument("--metrics-stop-requested-by-runner", action="store_true")

    failed = subparsers.add_parser("mark-failed", help="mark a non-terminal run failed")
    _add_manifest_path(failed)
    failed.add_argument("--exit-code", required=True, type=int)
    failed.add_argument("--reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        if args.command == "create":
            manifest_path = validate_manifest_path(
                project_root, args.manifest, args.run_id
            )
            server_settings = parse_key_value(
                args.server_setting, option="--server-setting"
            )
            workload_settings = parse_key_value(
                args.workload_setting, option="--workload-setting"
            )
            artifacts = _artifacts_from_args(project_root, args.artifact)
            manifest = build_manifest(
                project_root=project_root,
                run_id=args.run_id,
                phase=args.phase,
                input_len=args.input_len,
                output_len=args.output_len,
                concurrency=args.concurrency,
                repetition=args.repetition,
                num_prompts=args.num_prompts,
                num_warmups=args.num_warmups,
                request_rate=args.request_rate,
                random_range_ratio=args.random_range_ratio,
                loadgen_seed=args.loadgen_seed,
                image=args.image,
                model=args.model,
                model_revision=args.model_revision,
                tokenizer_snapshot=args.tokenizer_snapshot,
                server_settings=server_settings,
                workload_settings=workload_settings,
                artifacts=artifacts,
                provenance_capture=args.provenance_capture,
            )
            validate_manifest_invariants(manifest)
            exclusive_write_json(manifest_path, manifest)
        else:
            manifest_path = validate_manifest_path(project_root, args.manifest)
            manifest = load_manifest(manifest_path)
            validate_manifest_path(project_root, manifest_path, manifest["run_id"])
            if args.command == "set-status":
                transition_status(manifest, args.status, reason=args.reason)
            elif args.command == "record-exits":
                record_exit_codes(
                    manifest,
                    gpu=args.gpu_exit_code,
                    metrics=args.metrics_exit_code,
                    benchmark=args.benchmark_exit_code,
                    gpu_stop_requested_by_runner=args.gpu_stop_requested_by_runner,
                    metrics_stop_requested_by_runner=(
                        args.metrics_stop_requested_by_runner
                    ),
                )
            elif args.command == "mark-failed":
                mark_failed(
                    manifest,
                    project_root,
                    exit_code=args.exit_code,
                    reason=args.reason,
                )
            else:  # pragma: no cover - argparse guarantees the command set.
                raise AssertionError(args.command)
            validate_manifest_invariants(manifest)
            atomic_write_json(manifest_path, manifest)
    except (ManifestError, OSError) as error:
        print(f"evidence manifest error: {error}", file=__import__("sys").stderr)
        return 1
    print(f"manifest {manifest['status']}: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
