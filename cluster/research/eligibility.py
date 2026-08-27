"""Fresh per-cell drift and quality gates for formal campaigns."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .locks import assess_formal_eligibility


DRIFT_CODES = frozenset(
    {
        "MODEL_SHA_MISMATCH",
        "MODEL_SHA_MISSING",
        "MODEL_WORKER_NOT_VERIFIED",
        "SOURCE_FINGERPRINT_MISMATCH",
        "SOURCE_FINGERPRINT_MISSING",
        "SOURCE_FINGERPRINT_UNVERIFIED",
        "SOURCE_FINGERPRINT_INVALID",
        "RUNTIME_LOCK_MISMATCH",
        "RUNTIME_COMMIT_MISMATCH",
        "RUNTIME_FINGERPRINT_MISMATCH",
        "RUNTIME_VERSION_MISMATCH",
        "RPC_COMMIT_MISMATCH",
        "JETSON_POWER_MODE_MISMATCH",
        "JETSON_CLOCKS_MISMATCH",
        "PROMPT_SET_MISMATCH",
        "CONDITION_PROFILE_MISMATCH",
        "BACKEND_NOT_VERIFIED",
        "NTP_NOT_SYNCHRONIZED",
        "STORAGE_INSUFFICIENT",
        "PREFLIGHT_SNAPSHOT_MISSING",
        "PI_POWER_ACTIVE",
    }
)


def _issue(code: str, *, node: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "blocking": True}
    if node:
        value["node"] = node
    return value


def _warning(code: str, *, node: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "blocking": False}
    if node:
        value["node"] = node
    return value


def _power_mode(snapshot: Mapping[str, Any]) -> str | None:
    power = snapshot.get("power")
    if isinstance(power, Mapping):
        current = power.get("current")
        if isinstance(current, Mapping) and current.get("name"):
            return str(current["name"])
        if power.get("mode"):
            return str(power["mode"])
    for key in ("power_mode", "nvpmodel_mode"):
        if snapshot.get(key):
            return str(snapshot[key])
    return None


def _jetson_clocks(snapshot: Mapping[str, Any]) -> str | None:
    power = snapshot.get("power")
    value: Any = power.get("jetson_clocks") if isinstance(power, Mapping) else None
    if isinstance(value, Mapping):
        if isinstance(value.get("active"), bool):
            return "ON" if value["active"] else "OFF"
        value = value.get("status")
    if value is None:
        value = snapshot.get("jetson_clocks")
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return str(value).upper() if value is not None else None


def _pi_power_state(snapshot: Mapping[str, Any]) -> tuple[bool, bool]:
    """Return ``(active_fault, history_warning)`` from accepted snapshots."""
    candidate = snapshot.get("power_integrity")
    if not isinstance(candidate, Mapping):
        power = snapshot.get("power")
        candidate = power if isinstance(power, Mapping) else {}
    status = str(candidate.get("status") or candidate.get("quality") or "").lower()
    active = bool(candidate.get("active_fault_bits")) or status in {
        "active",
        "active_degraded",
        "degraded_active",
    }
    history = bool(candidate.get("history_bits")) or status in {
        "warning",
        "history_warning",
    }
    return active, history


def _deduplicate(issues: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for raw in issues:
        issue = dict(raw)
        key = (
            str(issue.get("code") or ""),
            str(issue.get("node") or ""),
            str(issue.get("model_key") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(issue)
    return output


def assess_campaign_cell(
    *,
    cell: Mapping[str, Any],
    manifest: Mapping[str, Any],
    experiment_conditions: Mapping[str, Any],
    model_lock: Mapping[str, Any],
    prompt_lock: Mapping[str, Any],
    runtime_lock: Mapping[str, Any],
    live_preflight_snapshot: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Fail closed on fresh identity drift while preserving Pi history warnings."""
    selected_workers = [str(item) for item in cell.get("node_set") or []]
    model_key = str(cell.get("model_lock_key") or "")
    prompt_id = str(cell.get("prompt_id") or "")
    trace = {
        "experiment_lock_id": experiment_conditions.get("lock_id"),
        "experiment_lock_sha256": experiment_conditions.get("lock_sha256"),
        "model_lock_entry": model_key,
        "prompt_set_version": prompt_lock.get("prompt_set_version"),
        "runtime_lock_version": runtime_lock.get("lock_version"),
        "condition_profile_id": (experiment_conditions.get("fixed_profile") or {}).get(
            "profile_id"
        ),
    }
    base = assess_formal_eligibility(
        experiment_config=trace,
        experiment_conditions=experiment_conditions,
        model_lock=model_lock,
        prompt_lock=prompt_lock,
        runtime_lock=runtime_lock,
        model_key=model_key,
        prompt_ids=[prompt_id],
        selected_workers=selected_workers,
        live_preflight_snapshot=live_preflight_snapshot or {"_missing": {}},
    )
    blocking: list[dict[str, Any]] = [dict(item) for item in base["blocking_issues"]]
    warnings: list[dict[str, Any]] = [dict(item) for item in base["warnings"]]

    lock_ref = manifest.get("lock_ref") or {}
    if lock_ref.get("lock_sha256") != model_lock.get("lock_sha256"):
        blocking.append(_issue("RUNTIME_LOCK_MISMATCH"))
    if cell.get("prompt_set_version") != prompt_lock.get("prompt_set_version"):
        blocking.append(_issue("PROMPT_SET_MISMATCH"))

    locked_workers = {str(item.get("node")): item for item in runtime_lock.get("workers", [])}
    model_entries = {str(item.get("model_key")): item for item in model_lock.get("models", [])}
    expected_model_sha = (
        (model_entries.get(model_key) or {}).get("binary") or {}
    ).get("sha256")
    expected_condition_profile = str(
        (experiment_conditions.get("fixed_profile") or {}).get("profile_id") or ""
    )
    expected_parameter_profile = str(cell.get("parameter_profile") or "")
    for node in selected_workers:
        snapshot = live_preflight_snapshot.get(node)
        if not isinstance(snapshot, Mapping) or not snapshot:
            blocking.append(_issue("PREFLIGHT_SNAPSHOT_MISSING", node=node))
            continue
        observed_sha = snapshot.get("model_sha256")
        if not observed_sha:
            blocking.append(_issue("MODEL_SHA_MISSING", node=node))
        elif observed_sha != expected_model_sha:
            blocking.append(_issue("MODEL_SHA_MISMATCH", node=node))
        if snapshot.get("backend_verified") is False:
            blocking.append(_issue("BACKEND_NOT_VERIFIED", node=node))
        if snapshot.get("ntp_synchronized") is False:
            blocking.append(_issue("NTP_NOT_SYNCHRONIZED", node=node))
        if snapshot.get("storage_sufficient") is False:
            blocking.append(_issue("STORAGE_INSUFFICIENT", node=node))
        observed_profile = snapshot.get("condition_profile_id")
        if observed_profile is not None and observed_profile != expected_condition_profile:
            blocking.append(_issue("CONDITION_PROFILE_MISMATCH", node=node))
        observed_parameter_profile = snapshot.get("parameter_profile")
        if (
            observed_parameter_profile is not None
            and observed_parameter_profile != expected_parameter_profile
        ):
            blocking.append(_issue("CONDITION_PROFILE_MISMATCH", node=node))
        observed_prompt_version = snapshot.get("prompt_set_version")
        if (
            observed_prompt_version is not None
            and observed_prompt_version != prompt_lock.get("prompt_set_version")
        ):
            blocking.append(_issue("PROMPT_SET_MISMATCH", node=node))

        locked = locked_workers.get(node) or {}
        if locked.get("platform") == "jetson":
            locked_power = locked.get("power") or {}
            mode = _power_mode(snapshot)
            if mode is None or mode != locked_power.get("mode"):
                blocking.append(_issue("JETSON_POWER_MODE_MISMATCH", node=node))
            clocks = _jetson_clocks(snapshot)
            if clocks is None or clocks != str(locked_power.get("jetson_clocks")).upper():
                blocking.append(_issue("JETSON_CLOCKS_MISMATCH", node=node))
        elif locked.get("platform") == "raspberry-pi":
            active, history = _pi_power_state(snapshot)
            if active:
                blocking.append(_issue("PI_POWER_ACTIVE", node=node))
            elif history:
                warnings.append(_warning("PI_POWER_HISTORY", node=node))

    blocking = _deduplicate(blocking)
    warnings = _deduplicate(warnings)
    return {
        "eligible": not blocking,
        "blocking_issues": blocking,
        "warnings": warnings,
        "runtime_cohort_id": base.get("runtime_cohort_id"),
    }


__all__ = ["DRIFT_CODES", "assess_campaign_cell"]
