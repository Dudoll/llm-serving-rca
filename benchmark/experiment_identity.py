"""Stable identity for the fixed factors of an experiment.

Run-specific evidence (timestamps, hashes of generated artifacts, attempt
number) must not change the grouping key.  This module makes that rule
explicit and keeps it out of result parsing.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


DYNAMIC_WORKLOAD_CONFIG_FIELDS = {
    "run_attempt",
    "plan_block",
    "plan_order",
}


def manifest_identity_fields(manifest: dict[str, Any]) -> dict[str, str]:
    """Return CSV identity fields for one manifest.

    The digest describes the ExperimentSpec: fixed workload, server and source
    revision.  It intentionally excludes evidence-tree churn and per-attempt
    fields, so five repetitions remain one comparable group.
    """
    workload = manifest.get("workload")
    server = manifest.get("server")
    git = manifest.get("git")
    if not isinstance(workload, dict) or not isinstance(server, dict):
        raise ValueError("manifest lacks workload/server data for ExperimentSpec")
    if not isinstance(git, dict):
        raise ValueError("manifest lacks Git data for ExperimentSpec")

    config = workload.get("config")
    if not isinstance(config, dict):
        config = {}
    fixed_config = {
        key: value
        for key, value in config.items()
        if key not in DYNAMIC_WORKLOAD_CONFIG_FIELDS
    }
    fixed_workload = {
        key: workload.get(key)
        for key in ("phase", "num_warmups", "random_range_ratio")
    }
    canonical_spec = {
        "schema": "experiment-spec-v1",
        "git": {key: git.get(key) for key in ("sha", "capture_error")},
        "server": server,
        "workload": {**fixed_workload, "config": fixed_config},
    }
    digest = hashlib.sha256(
        json.dumps(
            canonical_spec,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "experiment_spec_sha256": digest,
        "run_namespace": str(config.get("run_namespace", "")),
        "plan_sha256": str(config.get("plan_sha256", "")),
        "nominal_arrival_horizon_seconds": str(
            config.get("nominal_arrival_horizon_seconds", "")
        ),
        "ttft_slo_ms": str(config.get("ttft_slo_ms", "")),
        "e2e_slo_ms": str(config.get("e2e_slo_ms", "")),
        "tpot_slo_ms": str(config.get("tpot_slo_ms", "")),
    }


def legacy_identity_fields() -> dict[str, str]:
    """Identity used only for explicitly admitted historical raw files."""
    return {
        "experiment_spec_sha256": "legacy-unmanifested",
        "run_namespace": "",
        "plan_sha256": "",
        "nominal_arrival_horizon_seconds": "",
        "ttft_slo_ms": "",
        "e2e_slo_ms": "",
        "tpot_slo_ms": "",
    }
