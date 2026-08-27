"""Read-only projections for campaign and cross-run Dashboard screens.

The benchmark and campaign writers remain the source of truth.  This module
only normalizes their durable artifacts for presentation, including summaries
written before formal research identity fields existed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


TERMINAL_CELL_STATES = frozenset({"completed", "failed", "cancelled", "excluded"})
QUALITY_VALUES = frozenset({"clean", "warning", "degraded", "unknown"})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: Any, fallback: str = "unknown") -> str:
    return str(value).strip() if isinstance(value, str) and value.strip() else fallback


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted({value for value in values if value and value != "unknown"})


def campaign_overview(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable, compact progress projection for one campaign."""
    cells = [item for item in manifest.get("cells", []) if isinstance(item, Mapping)]
    coverage = {
        key: int((manifest.get("coverage") or {}).get(key) or 0)
        for key in (
            "planned",
            "pending",
            "running",
            "completed",
            "failed",
            "cancelled",
            "excluded",
        )
    }
    if not coverage["planned"]:
        coverage["planned"] = len(cells)
    total = coverage["planned"]
    terminal = sum(coverage[key] for key in ("completed", "failed", "cancelled", "excluded"))
    run_ids = {
        str(attempt.get("run_id"))
        for cell in cells
        for attempt in cell.get("attempts", [])
        if isinstance(attempt, Mapping) and attempt.get("run_id")
    }
    repeats: dict[int, list[str]] = {}
    for cell in cells:
        repeat = cell.get("repeat_index")
        if isinstance(repeat, int) and not isinstance(repeat, bool):
            repeats.setdefault(repeat, []).append(_text(cell.get("status"), "pending"))
    complete_repeats = sum(
        1 for states in repeats.values() if states and all(state in TERMINAL_CELL_STATES for state in states)
    )
    drift_source = manifest.get("last_drift")
    current_drift: list[dict[str, Any]] = []
    if isinstance(drift_source, Mapping):
        for key in ("blocking_issues", "warnings", "drift"):
            values = drift_source.get(key)
            if isinstance(values, list):
                current_drift.extend(dict(item) for item in values if isinstance(item, Mapping))
    current_id = manifest.get("current_cell_id")
    current_cell = next((cell for cell in cells if cell.get("campaign_cell_id") == current_id), None)
    if current_cell:
        current_drift.extend(
            dict(item) for item in current_cell.get("drift", []) if isinstance(item, Mapping)
        )
    blocking_drift = [item for item in current_drift if item.get("blocking", True)]
    estimates = manifest.get("estimates") if isinstance(manifest.get("estimates"), Mapping) else {}
    nominal = _number(estimates.get("nominal_runtime_seconds")) or 0.0
    remaining_cells = max(0, total - terminal)
    remaining_runtime = (nominal * remaining_cells / total) if total else 0.0
    status = _text(manifest.get("status"), "planned")
    return {
        "campaign_id": _text(manifest.get("campaign_id"), "invalid-campaign"),
        "matrix_id": _text(manifest.get("matrix_id")),
        "status": status,
        "phase": _text(manifest.get("phase"), status),
        "experiment_type": _text(manifest.get("experiment_type"), "formal"),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "coverage": coverage,
        "progress_pct": round(100.0 * terminal / total, 2) if total else 0.0,
        "result_coverage": {"linked_runs": len(run_ids), "planned_runs": total},
        "repeat_progress": {
            "completed": complete_repeats,
            "total": int(manifest.get("repeat_count") or len(repeats)),
        },
        "formal_eligible": not blocking_drift and status not in {"failed", "cancelled"},
        "current_drift": current_drift,
        "blocking_drift_count": len(blocking_drift),
        "current_cell_id": current_id,
        "estimated_remaining": {
            "cells": remaining_cells,
            "runtime_seconds": round(remaining_runtime, 3),
            "storage_bytes": round(
                (_number(estimates.get("storage_bytes")) or 0.0) * remaining_cells / total
            ) if total else 0,
        },
    }


def campaign_detail(manifest: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    overview = campaign_overview(manifest)
    cells = []
    for raw in manifest.get("cells", []):
        if not isinstance(raw, Mapping):
            continue
        attempts = [dict(item) for item in raw.get("attempts", []) if isinstance(item, Mapping)]
        cells.append(
            {
                "campaign_cell_id": raw.get("campaign_cell_id"),
                "order_index": raw.get("order_index"),
                "repeat_index": raw.get("repeat_index"),
                "status": raw.get("status"),
                "platform_cohort": raw.get("platform_cohort"),
                "strategy": raw.get("strategy"),
                "node_set": list(raw.get("node_set") or []),
                "node_count": raw.get("node_count"),
                "model_lock_key": raw.get("model_lock_key"),
                "prompt_id": raw.get("prompt_id"),
                "measurement_quality": raw.get("measurement_quality"),
                "failure_code": raw.get("failure_code"),
                "run_id": raw.get("run_id"),
                "attempts": attempts,
                "warnings": list(raw.get("warnings") or []),
                "drift": list(raw.get("drift") or []),
            }
        )
    return {
        **overview,
        "model_ids": dict(manifest.get("model_ids") or {}),
        "cells": cells,
        "events": [dict(item) for item in events if isinstance(item, Mapping)][-200:],
    }


def _participant_platforms(run: Mapping[str, Any]) -> list[str]:
    identity = run.get("research_identity")
    if isinstance(identity, Mapping):
        cohort = _text(identity.get("platform_cohort"), "")
        if cohort.startswith("jetson"):
            return ["jetson"]
        if cohort.startswith("pi") or "raspberry" in cohort:
            return ["raspberry-pi"]
    values: list[str] = []
    for item in run.get("participant_nodes", []):
        if not isinstance(item, Mapping):
            continue
        value = _text(item.get("detected_platform") or item.get("platform"), "")
        if value:
            values.append(value)
    return _sorted_unique(values)


def _runtime_fingerprints(run: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for item in run.get("participant_nodes", []):
        if not isinstance(item, Mapping):
            continue
        backend = item.get("runtime_backend")
        if isinstance(backend, Mapping):
            value = backend.get("runtime_fingerprint") or backend.get("fingerprint")
            if value:
                values.append(str(value))
        deployment = item.get("deployment")
        if isinstance(deployment, Mapping) and deployment.get("runtime_fingerprint"):
            values.append(str(deployment["runtime_fingerprint"]))
    topology = run.get("topology")
    if not values and isinstance(topology, Mapping) and topology.get("runtime_commit"):
        values.append(f"rpc:{topology['runtime_commit']}")
    return _sorted_unique(values)


def _power_modes(run: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for item in run.get("participant_nodes", []):
        if not isinstance(item, Mapping):
            continue
        value = item.get("power_mode")
        power = item.get("power")
        if not value and isinstance(power, Mapping):
            current = power.get("current")
            value = current.get("name") if isinstance(current, Mapping) else power.get("mode")
        if value and str(value) != "not_applicable":
            values.append(str(value))
    return _sorted_unique(values)


def normalize_compare_run(run: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize new and legacy summary schemas without inventing identity."""
    identity = run.get("research_identity") if isinstance(run.get("research_identity"), Mapping) else {}
    platforms = _participant_platforms(run)
    runtime_fingerprints = _runtime_fingerprints(run)
    power_modes = _power_modes(run)
    nodes = [str(item) for item in run.get("nodes", []) if isinstance(item, str)]
    if not nodes:
        nodes = [
            str(item.get("name") or item.get("node"))
            for item in run.get("participant_nodes", [])
            if isinstance(item, Mapping) and (item.get("name") or item.get("node"))
        ]
    quality = _text(run.get("measurement_quality"))
    if quality not in QUALITY_VALUES:
        quality = "unknown"
    platform = platforms[0] if len(platforms) == 1 else "mixed" if platforms else "unknown"
    runtime = runtime_fingerprints[0] if len(runtime_fingerprints) == 1 else "mixed" if runtime_fingerprints else "unknown"
    power = power_modes[0] if len(power_modes) == 1 else "mixed" if power_modes else "not_applicable"
    return {
        "run_id": _text(run.get("run_id"), "legacy-run"),
        "campaign_id": _text(identity.get("campaign_id") or run.get("campaign_id"), "uncampaigned"),
        "campaign_cell_id": _text(identity.get("campaign_cell_id"), "untracked"),
        "experiment_type": _text(identity.get("experiment_type"), "legacy"),
        "model_lock": _text(identity.get("model_lock_entry"), "unlocked"),
        "model_id": _text(run.get("model_id")),
        "platform": platform,
        "platforms": platforms,
        "node_count": len(nodes),
        "nodes": nodes,
        "strategy": _text(run.get("execution_strategy"), "legacy"),
        "runtime_fingerprint": runtime,
        "runtime_fingerprints": runtime_fingerprints,
        "power_mode": power,
        "power_modes": power_modes,
        "measurement_quality": quality,
        "measurement_quality_reasons": list(run.get("measurement_quality_reasons") or []),
        "status": _text(run.get("status"), "unknown"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "metrics": {
            "throughput_tokens_s": _number(run.get("cluster_tokens_per_s")),
            "requests_s": _number(run.get("requests_per_s")),
            "ttft_p50_s": _number(run.get("ttft_p50_s")),
            "ttft_p95_s": _number(run.get("ttft_p95_s")),
            "e2e_p50_s": _number(run.get("e2e_p50_s")),
            "e2e_p95_s": _number(run.get("e2e_p95_s")),
            "success_rate": _number(run.get("success_rate")),
        },
        "legacy_fallback": not bool(identity),
    }


def compare_payload(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [normalize_compare_run(run) for run in runs if isinstance(run, Mapping)]
    normalized.sort(key=lambda item: str(item.get("finished_at") or item.get("started_at") or ""), reverse=True)
    dimensions = {
        "campaigns": _sorted_unique([item["campaign_id"] for item in normalized]),
        "model_locks": _sorted_unique([item["model_lock"] for item in normalized]),
        "platforms": _sorted_unique([item["platform"] for item in normalized]),
        "node_counts": sorted({item["node_count"] for item in normalized if item["node_count"]}),
        "strategies": _sorted_unique([item["strategy"] for item in normalized]),
        "runtime_fingerprints": _sorted_unique([item["runtime_fingerprint"] for item in normalized]),
        "power_modes": _sorted_unique([item["power_mode"] for item in normalized]),
        "measurement_qualities": _sorted_unique([item["measurement_quality"] for item in normalized]),
    }
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": normalized,
        "filters": dimensions,
        "legacy_run_count": sum(1 for item in normalized if item["legacy_fallback"]),
    }


def research_readiness(
    *,
    model_lock: Mapping[str, Any],
    runtime_lock: Mapping[str, Any],
    matrix: Mapping[str, Any],
    live_status: Sequence[Mapping[str, Any]],
    environment: Sequence[Mapping[str, Any]],
    controller_commit: str,
) -> dict[str, Any]:
    """Project lock state and live Worker drift into one research checklist."""
    models = [item for item in model_lock.get("models", []) if isinstance(item, Mapping)]
    approved_models = [item for item in models if (item.get("verification") or {}).get("status") == "approved"]
    license_blockers = []
    for item in models:
        license_record = item.get("license") if isinstance(item.get("license"), Mapping) else {}
        if license_record.get("acceptance_required") and not license_record.get("accepted_for_this_project"):
            license_blockers.append(
                {
                    "model_key": item.get("model_key"),
                    "display_name": item.get("display_name"),
                    "license": license_record.get("spdx_or_name"),
                }
            )
    status_by_name = {str(item.get("name")): item for item in live_status if isinstance(item, Mapping)}
    environment_by_name = {
        str(item.get("node")): item for item in environment if isinstance(item, Mapping)
    }
    workers = []
    for locked in runtime_lock.get("workers", []):
        if not isinstance(locked, Mapping):
            continue
        node = _text(locked.get("node"))
        live = status_by_name.get(node, {})
        profile = live.get("profile") if isinstance(live.get("profile"), Mapping) else {}
        deployment = profile.get("deployment") if isinstance(profile.get("deployment"), Mapping) else {}
        backend = profile.get("runtime_backend") if isinstance(profile.get("runtime_backend"), Mapping) else {}
        expected_deployment = locked.get("deployment") if isinstance(locked.get("deployment"), Mapping) else {}
        expected_runtime = locked.get("runtime") if isinstance(locked.get("runtime"), Mapping) else {}
        expected_power = locked.get("power") if isinstance(locked.get("power"), Mapping) else {}
        live_power = live.get("power") if isinstance(live.get("power"), Mapping) else {}
        current = live_power.get("current") if isinstance(live_power.get("current"), Mapping) else {}
        observed_commit = deployment.get("git_commit") or profile.get("git_commit")
        observed_runtime = backend.get("runtime_fingerprint") or profile.get("runtime_fingerprint")
        observed_power = current.get("name") or live_power.get("mode") or profile.get("power_mode")
        environment_report = environment_by_name.get(node, {})
        online = live.get("api") is True
        backend_verified = backend.get("verified") is True or environment_report.get("backend", {}).get("verified") is True
        workers.append(
            {
                "node": node,
                "platform": locked.get("platform"),
                "online": online,
                "expected_source_commit": expected_deployment.get("git_commit"),
                "observed_source_commit": observed_commit,
                "source_identity": "match" if observed_commit and observed_commit == expected_deployment.get("git_commit") else "drift" if observed_commit else "unknown",
                "expected_runtime_fingerprint": expected_runtime.get("runtime_fingerprint"),
                "observed_runtime_fingerprint": observed_runtime,
                "runtime_identity": "match" if observed_runtime and observed_runtime == expected_runtime.get("runtime_fingerprint") else "drift" if observed_runtime else "unknown",
                "expected_power_mode": expected_power.get("mode"),
                "observed_power_mode": observed_power,
                "power_identity": "match" if expected_power.get("mode") == "not_applicable" or (observed_power and observed_power == expected_power.get("mode")) else "drift" if observed_power else "unknown",
                "backend_verified": backend_verified,
                "instrumentation_ready": bool(online and backend_verified and environment_report.get("status") == "ready"),
            }
        )
    execution_gate = matrix.get("execution_gate") if isinstance(matrix.get("execution_gate"), Mapping) else {}
    blocking_phases = list(execution_gate.get("blocking_phases") or [])
    blocking_requirements = list(execution_gate.get("blocking_requirements") or [])
    formal_execution_allowed = execution_gate.get("formal_execution_allowed") is True
    worker_blockers = [
        {"node": item["node"], "code": "WORKER_NOT_FORMALLY_READY"}
        for item in workers
        if not item["instrumentation_ready"]
        or item["source_identity"] != "match"
        or item["runtime_identity"] != "match"
        or item["power_identity"] == "drift"
    ]
    controller_expected = (runtime_lock.get("controller") or {}).get("git_commit")
    controller_source = {
        "expected_commit": controller_expected,
        "observed_commit": controller_commit,
        "status": "match" if controller_commit == controller_expected else "drift",
    }
    eligible = (
        formal_execution_allowed
        and not blocking_phases
        and not blocking_requirements
        and not license_blockers
        and not worker_blockers
        and controller_source["status"] == "match"
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eligible": eligible,
        "approved_models": [
            {
                "model_key": item.get("model_key"),
                "display_name": item.get("display_name"),
                "sha256": (item.get("binary") or {}).get("sha256"),
                "verified_workers": list((item.get("verification") or {}).get("verified_workers") or []),
            }
            for item in approved_models
        ],
        "model_counts": {"approved": len(approved_models), "total": len(models)},
        "license_blockers": license_blockers,
        "controller_source": controller_source,
        "workers": workers,
        "execution_gate": {
            "formal_execution_allowed": formal_execution_allowed,
            "blocking_phases": blocking_phases,
            "blocking_requirements": blocking_requirements,
            "reason": execution_gate.get("reason"),
        },
        "blocking_issues": worker_blockers
        + [{"code": "LICENSE_ACCEPTANCE_REQUIRED", **item} for item in license_blockers]
        + ([{"code": "FORMAL_PHASE_GATE", "phases": blocking_phases}] if blocking_phases else [])
        + ([{
            "code": "FORMAL_EXECUTION_GATE",
            "requirements": blocking_requirements,
            "reason": execution_gate.get("reason"),
        }] if not formal_execution_allowed else [])
        + ([{"code": "CONTROLLER_SOURCE_DRIFT"}] if controller_source["status"] != "match" else []),
    }


__all__ = [
    "campaign_detail",
    "campaign_overview",
    "compare_payload",
    "normalize_compare_run",
    "research_readiness",
]
