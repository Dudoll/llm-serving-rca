"""Admission gates between evidence bundles and analysis tables.

The rule is intentionally simple: a normal analysis consumer may use a raw
result only when its manifest is complete and the full bundle revalidates.  A
historical raw-only file is a separate, explicitly labelled legacy mode.
"""

from __future__ import annotations

import math
from pathlib import Path

from .evidence_manifest import load_manifest
from .experiment_identity import legacy_identity_fields, manifest_identity_fields
from .result_contract import BASE_FIELDS
from .result_normalization import summarize_file
from .validate_evidence import artifact_paths_from_manifest, validate_evidence_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def validate_complete_bundle_for_raw(
    path: Path,
    *,
    project_root: Path = PROJECT_ROOT,
    manifest_dir: Path | None = None,
) -> str:
    """Revalidate the manifest and all eight artifacts bound to ``path``."""
    manifests = manifest_dir or project_root / "results" / "manifests"
    manifest_path = manifests / f"{path.stem}.json"
    manifest = load_manifest(manifest_path)
    if manifest.get("status") != "complete":
        raise ValueError(
            f"{manifest_path} is {manifest.get('status')!r}; only complete bundles "
            "may enter a summary"
        )

    workload = manifest.get("workload")
    server = manifest.get("server")
    if not isinstance(workload, dict) or not isinstance(server, dict):
        raise ValueError(f"{manifest_path} lacks workload/server provenance")
    expected = {
        "run_id": manifest.get("run_id"),
        "phase": workload.get("phase"),
        "input_len": workload.get("input_len"),
        "output_len": workload.get("output_len"),
        "concurrency": workload.get("concurrency"),
        "repetition": workload.get("repetition"),
        "num_prompts": workload.get("num_prompts"),
        "request_rate": workload.get("request_rate"),
        "random_range_ratio": workload.get("random_range_ratio"),
        "loadgen_seed": workload.get("loadgen_seed"),
        "image": server.get("image"),
        "model": server.get("model"),
        "model_revision": server.get("model_revision"),
    }
    artifacts = artifact_paths_from_manifest(manifest, project_root)
    raw_artifact = artifacts.get("raw_result")
    if raw_artifact is None or raw_artifact.resolve() != path.resolve():
        raise ValueError(
            f"{manifest_path} does not bind the requested raw result {path}"
        )
    validate_evidence_bundle(
        project_root=project_root,
        artifacts=artifacts,
        expected=expected,
        manifest=manifest,
    )
    if manifest.get("provenance_capture") == "backfilled":
        return "complete-backfilled"
    return "complete"


def validate_summary_evidence_rows(
    rows: list[dict[str, str]],
    *,
    allow_legacy_unmanifested: bool,
    project_root: Path = PROJECT_ROOT,
    raw_dir: Path | None = None,
    manifest_dir: Path | None = None,
) -> None:
    """Re-admit every row before an aggregate can trust an editable CSV.

    For complete evidence, the row is rebuilt from raw JSON and compared field
    by field.  Thus the CSV is a cache, not a source of truth.
    """
    raw_root = raw_dir or project_root / "results" / "raw"
    manifests = manifest_dir or project_root / "results" / "manifests"
    for row in rows:
        run_id = row.get("run_id", "")
        status = row.get("evidence_status", "")
        if not run_id:
            raise ValueError("Summary row is missing run_id")
        raw_path = raw_root / f"{run_id}.json"

        if status == "legacy-unmanifested":
            if not allow_legacy_unmanifested:
                raise ValueError(
                    f"Run {run_id} is legacy-unmanifested; explicit admission is required"
                )
            identity = legacy_identity_fields()
        else:
            if status not in {"complete", "complete-backfilled"}:
                raise ValueError(
                    f"Run {run_id} has inadmissible evidence_status {status!r}"
                )
            actual_status = validate_complete_bundle_for_raw(
                raw_path,
                project_root=project_root,
                manifest_dir=manifests,
            )
            if actual_status != status:
                raise ValueError(
                    f"Run {run_id} evidence status changed: summary={status}, "
                    f"manifest={actual_status}"
                )
            identity = manifest_identity_fields(
                load_manifest(manifests / f"{run_id}.json")
            )

        canonical = summarize_file(raw_path)
        canonical.update(identity)
        canonical["evidence_status"] = status
        for field in BASE_FIELDS:
            if field not in row:
                raise ValueError(f"Summary row for {run_id} is missing {field!r}")
            if not summary_values_equal(row[field], canonical.get(field, "")):
                raise ValueError(
                    f"Summary row for {run_id} was modified: {field}="
                    f"{row[field]!r}, canonical={canonical.get(field, '')!r}"
                )


def summary_values_equal(actual: object, canonical: object) -> bool:
    if actual in (None, "") or canonical in (None, ""):
        return actual in (None, "") and canonical in (None, "")
    if str(actual) == str(canonical):
        return True
    try:
        actual_number = float(actual)
        canonical_number = float(canonical)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(actual_number)
        and math.isfinite(canonical_number)
        and actual_number == canonical_number
    )
