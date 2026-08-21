"""Pure validation and fingerprinting for formal experiment identity locks.

These helpers do not read files, contact Workers, or alter normal benchmark
admission.  They are an explicit research-only gate for a future formal run.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence


PINNED_RPC_COMMIT = "f49e9178767d557a522618b16ce8694f9ddac628"
LOCK_STATUSES = {"draft", "source_locked", "worker_verified", "approved", "rejected"}
NONDETERMINISTIC_KEYS = {
    "created_at",
    "checked_at",
    "observed_at",
    "received_at",
    "verified_at",
    "lock_sha256",
}
HEX_40 = re.compile(r"^[0-9a-f]{40}$")
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
EXPLICIT_BACKEND_MARKERS = {"unsupported", "unsupported_by_worker_api", "not_applicable", "backend_default"}


class LockValidationError(ValueError):
    """A formal lock is internally inconsistent or incomplete."""


def _without_nondeterministic(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_nondeterministic(item)
            for key, item in value.items()
            if str(key) not in NONDETERMINISTIC_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_nondeterministic(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a lock deterministically while excluding observation times."""
    return json.dumps(
        _without_nondeterministic(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def lock_set_sha256(locks: Mapping[str, Mapping[str, Any]]) -> str:
    """Return one deterministic fingerprint for the complete named lock set."""
    normalized = {str(name): dict(value) for name, value in locks.items()}
    return hashlib.sha256(canonical_json_bytes(normalized)).hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LockValidationError(f"{label} must be an object")
    return value


def _require_nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LockValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_common_header(lock: Mapping[str, Any], label: str) -> None:
    if lock.get("schema_version") != 1 or lock.get("lock_version") != 1:
        raise LockValidationError(f"{label} schema_version and lock_version must be 1")
    _require_nonempty(lock.get("lock_id"), f"{label}.lock_id")
    source_commit = _require_nonempty(lock.get("source_commit"), f"{label}.source_commit")
    if not HEX_40.fullmatch(source_commit):
        raise LockValidationError(f"{label}.source_commit must be an exact Git commit")
    fingerprint = _require_nonempty(lock.get("lock_sha256"), f"{label}.lock_sha256")
    if not HEX_64.fullmatch(fingerprint):
        raise LockValidationError(f"{label}.lock_sha256 must be SHA-256")


def validate_model_lock(lock: Mapping[str, Any]) -> None:
    _validate_common_header(lock, "model_lock")
    models = lock.get("models")
    if not isinstance(models, list) or not models:
        raise LockValidationError("model_lock.models must be a non-empty list")
    seen: set[str] = set()
    for index, raw in enumerate(models):
        model = _require_mapping(raw, f"models[{index}]")
        key = _require_nonempty(model.get("model_key"), f"models[{index}].model_key")
        if key in seen:
            raise LockValidationError(f"duplicate model_key: {key}")
        seen.add(key)
        source = _require_mapping(model.get("source"), f"{key}.source")
        binary = _require_mapping(model.get("binary"), f"{key}.binary")
        runtime = _require_mapping(model.get("runtime_contract"), f"{key}.runtime_contract")
        license_record = _require_mapping(model.get("license"), f"{key}.license")
        verification = _require_mapping(model.get("verification"), f"{key}.verification")
        _require_nonempty(source.get("repository"), f"{key}.source.repository")
        revision = _require_nonempty(source.get("revision"), f"{key}.source.revision")
        if source.get("revision_type") != "commit" or not HEX_40.fullmatch(revision):
            raise LockValidationError(f"{key} requires an exact 40-character source commit")
        filename = _require_nonempty(binary.get("filename"), f"{key}.binary.filename")
        if not filename.lower().endswith(".gguf") or "/" in filename or "\\" in filename:
            raise LockValidationError(f"{key} requires one exact GGUF basename")
        sha256 = _require_nonempty(binary.get("sha256"), f"{key}.binary.sha256")
        if not HEX_64.fullmatch(sha256):
            raise LockValidationError(f"{key} binary SHA-256 is invalid")
        _require_nonempty(binary.get("quantization"), f"{key}.binary.quantization")
        _require_nonempty(license_record.get("spdx_or_name"), f"{key}.license.spdx_or_name")
        _require_nonempty(
            license_record.get("source_url_or_identifier"),
            f"{key}.license.source_url_or_identifier",
        )
        status = verification.get("status")
        if status not in LOCK_STATUSES:
            raise LockValidationError(f"{key} has unknown verification status")
        if not source.get("official_gguf"):
            converter = _require_nonempty(binary.get("converter"), f"{key}.binary.converter")
            converter_revision = _require_nonempty(
                binary.get("converter_revision"), f"{key}.binary.converter_revision"
            )
            if "unverified" in {converter.lower(), converter_revision.lower()}:
                raise LockValidationError(f"{key} community GGUF provenance is incomplete")
        if status == "approved":
            if license_record.get("acceptance_required") and not license_record.get(
                "accepted_for_this_project"
            ):
                raise LockValidationError(f"{key} license acceptance is required")
            for field in ("chat_template_hash", "tokenizer_metadata_hash"):
                value = _require_nonempty(runtime.get(field), f"{key}.{field}")
                if not HEX_64.fullmatch(value):
                    raise LockValidationError(f"{key} approved runtime metadata is not locked")
            workers = verification.get("verified_workers")
            observed = verification.get("observed_worker_checksums")
            if not isinstance(workers, list) or not workers or not isinstance(observed, Mapping):
                raise LockValidationError(f"{key} approved model lacks Worker verification")
            for worker in workers:
                if observed.get(worker) != sha256:
                    raise LockValidationError(f"{key} Worker checksum mismatch on {worker}")


def validate_prompt_lock(lock: Mapping[str, Any]) -> None:
    _validate_common_header(lock, "prompt_set")
    version = lock.get("prompt_set_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise LockValidationError("prompt_set_version must be a positive integer")
    prompts = lock.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise LockValidationError("prompts must be a non-empty list")
    seen: set[str] = set()
    for index, raw in enumerate(prompts):
        prompt = _require_mapping(raw, f"prompts[{index}]")
        prompt_id = _require_nonempty(prompt.get("prompt_id"), f"prompts[{index}].prompt_id")
        if prompt_id in seen:
            raise LockValidationError(f"duplicate prompt_id: {prompt_id}")
        seen.add(prompt_id)
        text = _require_nonempty(prompt.get("text"), f"{prompt_id}.text")
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if prompt.get("sha256") != expected:
            raise LockValidationError(f"prompt hash mismatch: {prompt_id}")


def validate_condition_lock(lock: Mapping[str, Any]) -> None:
    _validate_common_header(lock, "experiment_conditions")
    profile = _require_mapping(lock.get("fixed_profile"), "fixed_profile")
    required = {
        "profile_id",
        "n_ctx",
        "n_batch",
        "n_threads",
        "n_gpu_layers",
        "max_tokens",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "repeat_penalty",
        "seed",
        "reasoning_mode",
        "chat_template",
        "streaming",
        "stop_sequences",
    }
    missing = sorted(required.difference(profile))
    if missing:
        raise LockValidationError("missing inference parameters: " + ", ".join(missing))
    for field in ("n_batch", "n_threads", "n_gpu_layers"):
        value = profile[field]
        if isinstance(value, str) and value not in EXPLICIT_BACKEND_MARKERS | {
            "platform_profile",
            "worker_fixed",
            "platform_fixed",
        }:
            raise LockValidationError(f"{field} uses an implicit backend default")
    for field in ("top_k", "min_p", "repeat_penalty", "stop_sequences"):
        value = profile[field]
        if value is None or value == "":
            raise LockValidationError(f"{field} must be a value or an explicit support marker")
    profiles = _require_mapping(lock.get("platform_profiles"), "platform_profiles")
    for name in ("jetson", "raspberry_pi", "rpc"):
        candidate = _require_mapping(profiles.get(name), f"platform_profiles.{name}")
        _require_nonempty(candidate.get("profile_id"), f"platform_profiles.{name}.profile_id")


def validate_runtime_lock(lock: Mapping[str, Any]) -> None:
    _validate_common_header(lock, "runtime_lock")
    controller = _require_mapping(lock.get("controller"), "controller")
    if not HEX_40.fullmatch(str(controller.get("git_commit", ""))):
        raise LockValidationError("controller.git_commit must be exact")
    rpc = _require_mapping(lock.get("native_llama_cpp_rpc"), "native_llama_cpp_rpc")
    if rpc.get("pinned_commit") != PINNED_RPC_COMMIT:
        raise LockValidationError("native llama.cpp RPC commit does not match the product pin")
    workers = lock.get("workers")
    if not isinstance(workers, list) or not workers:
        raise LockValidationError("runtime_lock.workers must be a non-empty list")
    seen: set[str] = set()
    for raw in workers:
        worker = _require_mapping(raw, "worker")
        node = _require_nonempty(worker.get("node"), "worker.node")
        if node in seen:
            raise LockValidationError(f"duplicate runtime worker: {node}")
        seen.add(node)
        runtime = _require_mapping(worker.get("runtime"), f"{node}.runtime")
        if runtime.get("backend_verified") is not True:
            raise LockValidationError(f"approved platform backend is not verified: {node}")
        deployment = _require_mapping(worker.get("deployment"), f"{node}.deployment")
        commit = deployment.get("git_commit")
        if commit != "unverified" and not HEX_40.fullmatch(str(commit)):
            raise LockValidationError(f"invalid Worker deployment commit: {node}")


def _issue(code: str, *, node: str | None = None, model_key: str | None = None) -> dict[str, str]:
    value = {"code": code}
    if node:
        value["node"] = node
    if model_key:
        value["model_key"] = model_key
    return value


def assess_formal_eligibility(
    *,
    experiment_config: Mapping[str, Any],
    experiment_conditions: Mapping[str, Any],
    model_lock: Mapping[str, Any],
    prompt_lock: Mapping[str, Any],
    runtime_lock: Mapping[str, Any],
    model_key: str,
    prompt_ids: Sequence[str],
    selected_workers: Sequence[str],
    live_preflight_snapshot: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess only the formal research path; normal experiments are unaffected."""
    validate_condition_lock(experiment_conditions)
    validate_model_lock(model_lock)
    validate_prompt_lock(prompt_lock)
    validate_runtime_lock(runtime_lock)
    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    required_trace = {
        "experiment_lock_id": experiment_conditions["lock_id"],
        "experiment_lock_sha256": experiment_conditions["lock_sha256"],
        "model_lock_entry": model_key,
        "prompt_set_version": prompt_lock["prompt_set_version"],
        "runtime_lock_version": runtime_lock["lock_version"],
        "condition_profile_id": experiment_conditions["fixed_profile"]["profile_id"],
    }
    if any(experiment_config.get(key) != value for key, value in required_trace.items()):
        blocking.append(_issue("EXPERIMENT_CONDITION_MISMATCH"))
    models = {item["model_key"]: item for item in model_lock["models"]}
    model = models.get(model_key)
    if model is None or model["verification"]["status"] != "approved":
        blocking.append(_issue("MODEL_NOT_APPROVED", model_key=model_key))
    locked_prompts = {item["prompt_id"] for item in prompt_lock["prompts"]}
    for prompt_id in prompt_ids:
        if prompt_id not in locked_prompts:
            blocking.append({"code": "PROMPT_NOT_LOCKED", "prompt_id": prompt_id})
    workers = {item["node"]: item for item in runtime_lock["workers"]}
    selected = []
    for node in selected_workers:
        worker = workers.get(node)
        if worker is None:
            blocking.append(_issue("RUNTIME_COMMIT_MISMATCH", node=node))
            continue
        selected.append(worker)
        if worker["runtime"].get("backend_verified") is not True:
            blocking.append(_issue("BACKEND_NOT_VERIFIED", node=node))
        if worker["deployment"].get("git_commit") == "unverified":
            blocking.append(_issue("RUNTIME_COMMIT_MISMATCH", node=node))
        power = worker.get("power", {})
        if worker.get("platform") == "raspberry-pi":
            quality = power.get("power_integrity_status")
            if quality == "warning":
                warnings.append(_issue("PI_POWER_HISTORY", node=node))
            elif quality == "degraded":
                warnings.append(_issue("PI_POWER_ACTIVE", node=node))
            elif quality not in {"clean"}:
                warnings.append(_issue("POWER_STATUS_UNKNOWN", node=node))
    jetson_modes = {
        worker.get("power", {}).get("mode")
        for worker in selected
        if worker.get("platform") == "jetson"
    }
    if len(jetson_modes) > 1:
        blocking.append(_issue("JETSON_POWER_MODE_MISMATCH"))
    if model is not None and live_preflight_snapshot:
        expected = model["binary"]["sha256"]
        for node in selected_workers:
            observed = live_preflight_snapshot.get(node, {}).get("model_sha256")
            if observed is not None and observed != expected:
                blocking.append(_issue("MODEL_SHA_MISMATCH", node=node, model_key=model_key))
    return {"eligible": not blocking, "blocking_issues": blocking, "warnings": warnings}


__all__ = [
    "PINNED_RPC_COMMIT",
    "LockValidationError",
    "assess_formal_eligibility",
    "canonical_json_bytes",
    "lock_set_sha256",
    "validate_condition_lock",
    "validate_model_lock",
    "validate_prompt_lock",
    "validate_runtime_lock",
]
