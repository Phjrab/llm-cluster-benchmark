"""Durable lifecycle for serialized formal research campaigns.

The service claims and persists one cell before invoking an external durable
run backend.  Re-instantiating it after a Dashboard restart resumes inspection
of the same attempt; it never silently launches a replacement for a completed
or uncertain attempt.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Sequence

from cluster.infrastructure.storage import (
    StorageCorruptionError,
    atomic_write_text,
    read_json_object,
)
from cluster.domain.identifiers import validate_campaign_id as validate_domain_campaign_id

from .matrix import (
    MatrixValidationError,
    compute_matrix_volume,
    expand_formal_matrix,
    validate_experiment_protocol,
    validate_formal_matrix,
)
from .protocol import CampaignGate, CampaignRunBackend, RUN_NONTERMINAL_STATES
from .scheduler import coverage_from_cells, seeded_randomized_block_order


CAMPAIGN_STATES = frozenset(
    {"planned", "ready", "running", "paused", "completed", "failed", "cancelled"}
)
CELL_STATES = frozenset(
    {"pending", "running", "completed", "failed", "cancelled", "excluded"}
)
TERMINAL_RUN_STATES = frozenset({"completed", "failed", "cancelled"})


class CampaignValidationError(ValueError):
    """A formal campaign request violates the frozen research protocol."""


class CampaignStateError(RuntimeError):
    """A requested lifecycle transition is unsafe or not applicable."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_campaign_id(value: str) -> str:
    try:
        return validate_domain_campaign_id(value)
    except ValueError as exc:
        raise CampaignValidationError(str(exc)) from exc


def _coverage(cells: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return coverage_from_cells(cells)


def _event(event_type: str, **fields: Any) -> dict[str, Any]:
    return {"type": event_type, "at": utc_now(), **fields}


def validate_campaign_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate durable lifecycle invariants independently of JSON Schema."""
    validate_campaign_id(str(manifest.get("campaign_id") or ""))
    if manifest.get("schema_version") != 1 or manifest.get("campaign_version") != 1:
        raise CampaignValidationError("campaign schema_version and campaign_version must be 1")
    if manifest.get("artifact_type") != "formal_campaign":
        raise CampaignValidationError("campaign artifact_type must be formal_campaign")
    if manifest.get("controller_participant_policy") != "forbidden":
        raise CampaignValidationError("Controller participation must be forbidden")
    if manifest.get("status") not in CAMPAIGN_STATES:
        raise CampaignValidationError("campaign status is invalid")
    if not isinstance(manifest.get("pause_requested"), bool) or not isinstance(
        manifest.get("cancel_requested"), bool
    ):
        raise CampaignValidationError("campaign pause/cancel flags must be booleans")
    retry = manifest.get("retry_policy")
    if not isinstance(retry, Mapping) or retry.get("automatic") is not False:
        raise CampaignValidationError("formal campaigns must never retry automatically")
    model_ids = manifest.get("model_ids")
    if not isinstance(model_ids, Mapping) or not model_ids:
        raise CampaignValidationError("campaign model_ids must freeze exact Worker paths")
    for model_key, model_id in model_ids.items():
        if (
            not isinstance(model_key, str)
            or not model_key
            or not isinstance(model_id, str)
            or not model_id.endswith(".gguf")
            or model_id.startswith("/")
            or "\\" in model_id
            or ".." in PurePosixPath(model_id).parts
            or str(PurePosixPath(model_id)) != model_id
        ):
            raise CampaignValidationError("campaign model_ids contains an unsafe mapping")
    cells = manifest.get("cells")
    if not isinstance(cells, list) or not cells:
        raise CampaignValidationError("campaign cells must be a non-empty list")
    cell_ids: set[str] = set()
    order_indices: set[int] = set()
    running_ids: list[str] = []
    attempt_ids: set[str] = set()
    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            raise CampaignValidationError(f"cells[{index}] must be an object")
        cell_id = str(cell.get("campaign_cell_id") or "")
        if not cell_id or cell_id in cell_ids:
            raise CampaignValidationError("campaign_cell_id values must be unique and non-empty")
        cell_ids.add(cell_id)
        order_index = cell.get("order_index")
        repeat_index = cell.get("repeat_index")
        if (
            isinstance(order_index, bool)
            or not isinstance(order_index, int)
            or order_index < 1
            or order_index in order_indices
        ):
            raise CampaignValidationError("order_index values must be unique positive integers")
        order_indices.add(order_index)
        if isinstance(repeat_index, bool) or not isinstance(repeat_index, int) or repeat_index < 1:
            raise CampaignValidationError("repeat_index must be a positive integer")
        status = cell.get("status")
        if status not in CELL_STATES:
            raise CampaignValidationError(f"invalid campaign cell status: {status}")
        if status == "running":
            running_ids.append(cell_id)
        if cell.get("model_lock_key") not in model_ids:
            raise CampaignValidationError("every campaign cell requires a frozen model id")
        attempts = cell.get("attempts")
        if not isinstance(attempts, list):
            raise CampaignValidationError("cell attempts must be a list")
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                raise CampaignValidationError("campaign attempts must be objects")
            attempt_id = str(attempt.get("attempt_id") or "")
            if not attempt_id or attempt_id in attempt_ids:
                raise CampaignValidationError("attempt_id values must be globally unique")
            attempt_ids.add(attempt_id)
    if len(running_ids) > 1:
        raise CampaignValidationError("formal campaigns serialize running cells")
    current = manifest.get("current_cell_id")
    if running_ids != ([current] if current else []):
        raise CampaignValidationError("current_cell_id must identify the only running cell")
    expected_coverage = _coverage(cells)
    if manifest.get("coverage") != expected_coverage:
        raise CampaignValidationError("campaign coverage does not match cell states")


class CampaignRepository:
    """Private atomic campaign manifests and append-only event journals."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _campaign_dir(self, campaign_id: str) -> Path:
        return self.directory / validate_campaign_id(campaign_id)

    def _manifest_path(self, campaign_id: str) -> Path:
        return self._campaign_dir(campaign_id) / "manifest.json"

    def _events_path(self, campaign_id: str) -> Path:
        return self._campaign_dir(campaign_id) / "events.jsonl"

    @contextmanager
    def locked(self, campaign_id: str) -> Iterator[None]:
        if self.directory.is_symlink():
            raise CampaignStateError("campaign directory must not be a symbolic link")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.directory.is_dir():
            raise CampaignStateError("campaign repository path is not a directory")
        self.directory.chmod(0o700)
        target = self._campaign_dir(campaign_id)
        if target.is_symlink():
            raise CampaignStateError("campaign path must not be a symbolic link")
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.is_dir():
            raise CampaignStateError("campaign path is not a directory")
        target.chmod(0o700)
        descriptor = os.open(target / ".campaign.lock", os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def create(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        campaign_id = validate_campaign_id(str(manifest.get("campaign_id") or ""))
        with self.locked(campaign_id):
            path = self._manifest_path(campaign_id)
            if path.exists():
                raise CampaignStateError(f"campaign already exists: {campaign_id}")
            value = dict(manifest)
            validate_campaign_manifest(value)
            atomic_write_text(
                path,
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                default_mode=0o600,
            )
            path.chmod(0o600)
            self._append_event_unlocked(campaign_id, _event("campaign_created"))
            return value

    def read(self, campaign_id: str) -> dict[str, Any]:
        path = self._manifest_path(campaign_id)
        if path.is_symlink() or not path.is_file():
            raise CampaignStateError("campaign manifest must be a regular file")
        value = read_json_object(path)
        validate_campaign_manifest(value)
        return value

    def update(
        self,
        campaign_id: str,
        mutate: Callable[[dict[str, Any]], None],
        *,
        event: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.locked(campaign_id):
            value = self.read(campaign_id)
            mutate(value)
            value["coverage"] = _coverage(value.get("cells") or [])
            value["updated_at"] = utc_now()
            validate_campaign_manifest(value)
            path = self._manifest_path(campaign_id)
            atomic_write_text(
                path,
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                default_mode=0o600,
            )
            path.chmod(0o600)
            if event is not None:
                self._append_event_unlocked(campaign_id, event)
            return value

    def _append_event_unlocked(self, campaign_id: str, event: Mapping[str, Any]) -> None:
        path = self._events_path(campaign_id)
        if path.is_symlink():
            raise CampaignStateError("campaign event journal must not be a symbolic link")
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_event(self, campaign_id: str, event: Mapping[str, Any]) -> None:
        with self.locked(campaign_id):
            self._append_event_unlocked(campaign_id, event)

    def read_events(self, campaign_id: str) -> list[dict[str, Any]]:
        try:
            lines = self._events_path(campaign_id).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        output: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                output.append(value)
        return output

    def list(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*/manifest.json"), reverse=True):
            try:
                value = read_json_object(path)
                validate_campaign_manifest(value)
                values.append(value)
            except (OSError, StorageCorruptionError, CampaignValidationError):
                continue
        return values


def build_campaign_manifest(
    *,
    campaign_id: str,
    matrix: Mapping[str, Any],
    protocol: Mapping[str, Any],
    model_lock: Mapping[str, Any],
    prompt_lock: Mapping[str, Any],
    runtime_lock: Mapping[str, Any],
    experiment_conditions: Mapping[str, Any],
    repeat_count: int,
    repeat_count_decision_evidence: str,
    model_ids: Mapping[str, str],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Freeze a complete executable manifest after all formal gates are open."""
    campaign_id = validate_campaign_id(campaign_id)
    validate_formal_matrix(
        matrix,
        model_lock=model_lock,
        prompt_lock=prompt_lock,
        runtime_lock=runtime_lock,
        experiment_conditions=experiment_conditions,
    )
    validate_experiment_protocol(protocol)
    repeat_axis = matrix.get("repeat_axis") or {}
    if (
        isinstance(repeat_count, bool)
        or not isinstance(repeat_count, int)
        or repeat_count < int(repeat_axis.get("minimum") or 0)
        or repeat_count > int(repeat_axis.get("maximum") or 0)
    ):
        raise CampaignValidationError("repeat_count must be within the frozen matrix range")
    if not isinstance(repeat_count_decision_evidence, str) or not repeat_count_decision_evidence.strip():
        raise CampaignValidationError("pilot-derived repeat count evidence is required")
    execution_gate = matrix.get("execution_gate") or {}
    if execution_gate.get("formal_execution_allowed") is not True:
        phases = ", ".join(str(item) for item in execution_gate.get("blocking_phases") or [])
        raise CampaignValidationError(
            "formal campaign remains blocked" + (f" by phase(s) {phases}" if phases else "")
        )
    thermal = protocol.get("thermal_policy") or {}
    if thermal.get("formal_start_blocked_until_frozen") is not False:
        raise CampaignValidationError("pilot-derived cooldown and thermal policy is not frozen")
    order = protocol.get("execution_order") or {}
    seed = order.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise CampaignValidationError("execution order seed must be an integer")

    scheduled = seeded_randomized_block_order(
        expand_formal_matrix(matrix), repeat_count=repeat_count, seed=seed
    )
    approved_models = {
        str(item.get("model_key")): item
        for item in model_lock.get("models", [])
        if isinstance(item, Mapping)
        and (item.get("verification") or {}).get("status") == "approved"
    }
    active_model_keys = {str(cell["model_lock_key"]) for cell in scheduled}
    if set(model_ids) != active_model_keys:
        raise CampaignValidationError("model_ids must exactly cover active formal model keys")
    frozen_model_ids: dict[str, str] = {}
    for model_key in sorted(active_model_keys):
        model_id = model_ids.get(model_key)
        expected = str((approved_models.get(model_key, {}).get("binary") or {}).get("filename") or "")
        if not isinstance(model_id, str) or PurePosixPath(model_id).name != expected:
            raise CampaignValidationError(
                f"model_ids does not match the locked GGUF filename for {model_key}"
            )
        frozen_model_ids[model_key] = model_id
    cells: list[dict[str, Any]] = []
    for cell in scheduled:
        cells.append(
            {
                **cell,
                "status": "pending",
                "attempt_id": None,
                "run_id": None,
                "failure_code": None,
                "measurement_quality": None,
                "attempts": [],
                "warnings": [],
                "drift": [],
            }
        )
    volume = compute_matrix_volume(matrix)
    cooldown_s = float(thermal.get("minimum_cooldown_s") or 0.0)
    timestamp = created_at or utc_now()
    manifest = {
        "schema_version": 1,
        "campaign_version": 1,
        "artifact_type": "formal_campaign",
        "campaign_id": campaign_id,
        "matrix_id": matrix.get("matrix_id"),
        "matrix_version": matrix.get("matrix_version"),
        "lock_ref": dict(matrix.get("lock_ref") or {}),
        "experiment_type": "formal",
        "status": "ready",
        "phase": "ready",
        "created_at": timestamp,
        "updated_at": timestamp,
        "order_seed": seed,
        "order_policy": "randomized",
        "repeat_count": repeat_count,
        "repeat_count_decision_evidence": repeat_count_decision_evidence.strip(),
        "controller_participant_policy": "forbidden",
        "model_ids": frozen_model_ids,
        "retry_policy": {
            "automatic": False,
            "manual_retry_requires_reason": True,
            "preserve_every_attempt": True,
        },
        "cooldown_policy": {
            "minimum_cooldown_s": cooldown_s,
            "stabilization_rule_source": thermal.get("final_stabilization_rule_source"),
        },
        "estimates": {
            "runs": len(cells),
            "nominal_runtime_seconds": volume["nominal_runtime_seconds_per_matrix_repeat"]
            * repeat_count,
            "timeout_envelope_seconds": volume["timeout_envelope_seconds_per_run"]
            * len(cells),
            "storage_bytes": volume["estimated_storage_bytes_per_matrix_repeat"]
            * repeat_count,
        },
        "coverage": _coverage(cells),
        "current_cell_id": None,
        "pause_requested": False,
        "cancel_requested": False,
        "cooldown_not_before": None,
        "last_drift": None,
        "cells": cells,
    }
    validate_campaign_manifest(manifest)
    return manifest


def _cell(manifest: MutableMapping[str, Any], campaign_cell_id: str) -> dict[str, Any]:
    for value in manifest.get("cells") or []:
        if value.get("campaign_cell_id") == campaign_cell_id:
            return value
    raise CampaignStateError(f"unknown campaign cell: {campaign_cell_id}")


def _active_cell(manifest: Mapping[str, Any]) -> dict[str, Any] | None:
    running = [cell for cell in manifest.get("cells") or [] if cell.get("status") == "running"]
    if len(running) > 1:
        raise CampaignStateError("campaign contains multiple running cells")
    return running[0] if running else None


def _latest_attempt(cell: MutableMapping[str, Any]) -> dict[str, Any]:
    attempts = cell.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise CampaignStateError("running cell has no durable attempt")
    attempt = attempts[-1]
    if not isinstance(attempt, dict):
        raise CampaignStateError("campaign attempt is corrupted")
    return attempt


class CampaignRunner:
    """One-at-a-time campaign state machine backed by durable run jobs."""

    def __init__(
        self,
        repository: CampaignRepository,
        backend: CampaignRunBackend,
        preflight_gate: CampaignGate,
        thermal_gate: CampaignGate,
        *,
        start_recovery_grace_s: float = 30.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.backend = backend
        self.preflight_gate = preflight_gate
        self.thermal_gate = thermal_gate
        self.start_recovery_grace_s = max(0.0, float(start_recovery_grace_s))
        self.now = now or (lambda: datetime.now(timezone.utc))

    def pause(self, campaign_id: str) -> dict[str, Any]:
        def apply(manifest: dict[str, Any]) -> None:
            if manifest.get("status") not in {"ready", "running"}:
                raise CampaignStateError("only a ready or running campaign can be paused")
            if _active_cell(manifest):
                manifest["pause_requested"] = True
                manifest["phase"] = "pausing"
            else:
                manifest["status"] = "paused"
                manifest["phase"] = "paused"

        return self.repository.update(campaign_id, apply, event=_event("campaign_pause_requested"))

    def resume(self, campaign_id: str) -> dict[str, Any]:
        def apply(manifest: dict[str, Any]) -> None:
            if manifest.get("status") != "paused":
                raise CampaignStateError("only a paused campaign can be resumed")
            manifest.update({"status": "ready", "phase": "ready", "pause_requested": False})

        return self.repository.update(campaign_id, apply, event=_event("campaign_resumed"))

    def cancel(self, campaign_id: str) -> dict[str, Any]:
        manifest = self.repository.read(campaign_id)
        active = _active_cell(manifest)
        if manifest.get("status") in {"completed", "failed", "cancelled"}:
            return manifest
        if active is not None:
            attempt = _latest_attempt(active)
            try:
                self.backend.cancel(attempt)
            except Exception as exc:
                self.repository.append_event(
                    campaign_id,
                    _event("campaign_cancel_backend_error", error_type=type(exc).__name__),
                )

        def apply(value: dict[str, Any]) -> None:
            value["cancel_requested"] = True
            value["phase"] = "cancelling" if _active_cell(value) else "cancelled"
            if not _active_cell(value):
                for cell in value.get("cells") or []:
                    if cell.get("status") == "pending":
                        cell["status"] = "cancelled"
                value["status"] = "cancelled"
                value["finished_at"] = utc_now()

        return self.repository.update(campaign_id, apply, event=_event("campaign_cancel_requested"))

    def retry_cell(self, campaign_id: str, campaign_cell_id: str, *, reason: str) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip():
            raise CampaignStateError("manual retry requires a reason")

        def apply(manifest: dict[str, Any]) -> None:
            if _active_cell(manifest):
                raise CampaignStateError("cannot retry while another cell is running")
            cell = _cell(manifest, campaign_cell_id)
            if cell.get("status") not in {"failed", "cancelled"}:
                raise CampaignStateError("only failed or cancelled cells can be retried")
            cell.update(
                {
                    "status": "pending",
                    "failure_code": None,
                    "attempt_id": None,
                    "run_id": None,
                    "retry_reason": reason.strip(),
                }
            )
            manifest.update(
                {
                    "status": "ready",
                    "phase": "ready",
                    "cancel_requested": False,
                    "pause_requested": False,
                    "finished_at": None,
                }
            )

        return self.repository.update(
            campaign_id,
            apply,
            event=_event("campaign_cell_retry_requested", campaign_cell_id=campaign_cell_id),
        )

    def _finalize_attempt(
        self,
        campaign_id: str,
        campaign_cell_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        status = str(result.get("status") or "failed")
        if status not in TERMINAL_RUN_STATES:
            raise CampaignStateError(f"attempt is not terminal: {status}")
        finished_at = str(result.get("finished_at") or utc_now())

        def apply(manifest: dict[str, Any]) -> None:
            cell = _cell(manifest, campaign_cell_id)
            attempt = _latest_attempt(cell)
            if attempt.get("status") in TERMINAL_RUN_STATES:
                return
            cleanup_status = str(result.get("cleanup_status") or "completed")
            effective = "failed" if cleanup_status == "failed" else status
            attempt.update(
                {
                    "status": effective,
                    "finished_at": finished_at,
                    "run_id": result.get("run_id") or attempt.get("run_id"),
                    "backend_job_id": result.get("backend_job_id")
                    or attempt.get("backend_job_id"),
                    "failure_code": (
                        "CLEANUP_FAILED"
                        if cleanup_status == "failed"
                        else result.get("failure_code")
                    ),
                    "cleanup_status": cleanup_status,
                    "measurement_quality": result.get("measurement_quality"),
                }
            )
            cell.update(
                {
                    "status": effective,
                    "attempt_id": attempt["attempt_id"],
                    "run_id": attempt.get("run_id"),
                    "failure_code": attempt.get("failure_code"),
                    "measurement_quality": attempt.get("measurement_quality"),
                    "finished_at": finished_at,
                }
            )
            manifest["current_cell_id"] = None
            cooldown_s = float((manifest.get("cooldown_policy") or {}).get("minimum_cooldown_s") or 0)
            manifest["cooldown_not_before"] = (
                self.now() + timedelta(seconds=cooldown_s)
            ).isoformat()
            if manifest.get("cancel_requested"):
                for remaining in manifest.get("cells") or []:
                    if remaining.get("status") == "pending":
                        remaining["status"] = "cancelled"
                manifest.update(
                    {"status": "cancelled", "phase": "cancelled", "finished_at": finished_at}
                )
            elif manifest.get("pause_requested"):
                manifest.update({"status": "paused", "phase": "paused"})
            else:
                pending = [item for item in manifest.get("cells") or [] if item.get("status") == "pending"]
                if pending:
                    manifest.update({"status": "running", "phase": "ready"})
                else:
                    failed = [item for item in manifest.get("cells") or [] if item.get("status") in {"failed", "cancelled"}]
                    manifest.update(
                        {
                            "status": "failed" if failed else "completed",
                            "phase": "finished",
                            "finished_at": finished_at,
                        }
                    )

        return self.repository.update(
            campaign_id,
            apply,
            event=_event(
                "campaign_attempt_finished",
                campaign_cell_id=campaign_cell_id,
                status=status,
                run_id=result.get("run_id"),
            ),
        )

    def _inspect_active(self, campaign_id: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
        active = _active_cell(manifest)
        if active is None:
            return dict(manifest)
        attempt = _latest_attempt(active)
        if not attempt.get("backend_job_id"):
            claimed = _parse_time(attempt.get("started_at"))
            age_s = (self.now() - claimed).total_seconds() if claimed else float("inf")
            if age_s <= self.start_recovery_grace_s:
                return dict(manifest)
            return self._finalize_attempt(
                campaign_id,
                str(active["campaign_cell_id"]),
                {"status": "failed", "failure_code": "CAMPAIGN_INTERRUPTED"},
            )
        try:
            result = dict(self.backend.inspect(attempt))
        except Exception as exc:
            self.repository.append_event(
                campaign_id,
                _event("campaign_attempt_inspection_uncertain", error_type=type(exc).__name__),
            )
            return self.repository.read(campaign_id)
        if result.get("status") in TERMINAL_RUN_STATES:
            return self._finalize_attempt(
                campaign_id, str(active["campaign_cell_id"]), result
            )
        return dict(manifest)

    def tick(self, campaign_id: str) -> dict[str, Any]:
        """Advance at most one lifecycle boundary and return durable state."""
        manifest = self.repository.read(campaign_id)
        if manifest.get("status") in {"paused", "completed", "failed", "cancelled"}:
            return manifest
        if _active_cell(manifest):
            return self._inspect_active(campaign_id, manifest)
        if manifest.get("cancel_requested"):
            return self.cancel(campaign_id)
        if manifest.get("pause_requested"):
            return self.pause(campaign_id)
        not_before = _parse_time(manifest.get("cooldown_not_before"))
        if not_before is not None and self.now() < not_before:
            return manifest
        pending = next(
            (cell for cell in manifest.get("cells") or [] if cell.get("status") == "pending"),
            None,
        )
        if pending is None:
            def finish(value: dict[str, Any]) -> None:
                failed = [cell for cell in value.get("cells") or [] if cell.get("status") in {"failed", "cancelled"}]
                value.update(
                    {
                        "status": "failed" if failed else "completed",
                        "phase": "finished",
                        "finished_at": utc_now(),
                    }
                )
            return self.repository.update(campaign_id, finish, event=_event("campaign_finished"))

        campaign_cell_id = str(pending["campaign_cell_id"])
        attempt_id = "attempt_" + secrets.token_hex(12)

        def claim(value: dict[str, Any]) -> None:
            if _active_cell(value):
                raise CampaignStateError("another campaign runner already claimed a cell")
            target = _cell(value, campaign_cell_id)
            if target.get("status") != "pending":
                raise CampaignStateError("campaign cell was already claimed")
            attempt = {
                "attempt_id": attempt_id,
                "status": "preflight",
                "started_at": self.now().isoformat(),
                "backend_job_id": None,
                "run_id": None,
                "failure_code": None,
            }
            target.setdefault("attempts", []).append(attempt)
            target.update({"status": "running", "attempt_id": attempt_id, "started_at": attempt["started_at"]})
            value.update(
                {"status": "running", "phase": "preflight", "current_cell_id": campaign_cell_id}
            )

        try:
            claimed = self.repository.update(
                campaign_id,
                claim,
                event=_event(
                    "campaign_cell_claimed",
                    campaign_cell_id=campaign_cell_id,
                    attempt_id=attempt_id,
                ),
            )
        except CampaignStateError:
            # Another Dashboard/runner instance won the durable claim.  The
            # loser observes the manifest instead of launching a duplicate.
            return self.repository.read(campaign_id)
        cell = _cell(claimed, campaign_cell_id)
        try:
            preflight = dict(self.preflight_gate(cell))
            thermal = dict(self.thermal_gate(cell))
        except Exception as exc:
            return self._finalize_attempt(
                campaign_id,
                campaign_cell_id,
                {"status": "failed", "failure_code": f"GATE_ERROR:{type(exc).__name__}"},
            )
        blocking = [
            *list(preflight.get("blocking_issues") or []),
            *list(thermal.get("blocking_issues") or []),
        ]
        warnings = [
            *list(preflight.get("warnings") or []),
            *list(thermal.get("warnings") or []),
        ]
        if blocking:
            def block(value: dict[str, Any]) -> None:
                target = _cell(value, campaign_cell_id)
                attempt = _latest_attempt(target)
                attempt.update({"status": "blocked", "finished_at": utc_now(), "drift": blocking})
                target.update({"status": "pending", "attempt_id": None, "drift": blocking, "warnings": warnings})
                value.update(
                    {
                        "status": "paused",
                        "phase": "drift_blocked",
                        "current_cell_id": None,
                        "last_drift": {"campaign_cell_id": campaign_cell_id, "issues": blocking},
                    }
                )
            return self.repository.update(
                campaign_id,
                block,
                event=_event("campaign_drift_blocked", campaign_cell_id=campaign_cell_id, issues=blocking),
            )

        def starting(value: dict[str, Any]) -> None:
            target = _cell(value, campaign_cell_id)
            attempt = _latest_attempt(target)
            attempt["status"] = "starting"
            target["warnings"] = warnings
            value["phase"] = "starting"

        prepared = self.repository.update(campaign_id, starting)
        cell = _cell(prepared, campaign_cell_id)
        attempt = _latest_attempt(cell)
        try:
            started = dict(self.backend.start(cell, attempt))
        except Exception as exc:
            return self._finalize_attempt(
                campaign_id,
                campaign_cell_id,
                {"status": "failed", "failure_code": f"START_FAILED:{type(exc).__name__}"},
            )
        status = str(started.get("status") or "running")
        if status in TERMINAL_RUN_STATES:
            return self._finalize_attempt(campaign_id, campaign_cell_id, started)
        if status not in RUN_NONTERMINAL_STATES:
            return self._finalize_attempt(
                campaign_id,
                campaign_cell_id,
                {"status": "failed", "failure_code": "INVALID_BACKEND_STATUS"},
            )

        def record_start(value: dict[str, Any]) -> None:
            target = _cell(value, campaign_cell_id)
            current = _latest_attempt(target)
            current.update(
                {
                    "status": status,
                    "backend_job_id": started.get("backend_job_id"),
                    "run_id": started.get("run_id"),
                }
            )
            target["run_id"] = started.get("run_id")
            value["phase"] = status

        return self.repository.update(
            campaign_id,
            record_start,
            event=_event("campaign_attempt_started", campaign_cell_id=campaign_cell_id, attempt_id=attempt_id),
        )


__all__ = [
    "CAMPAIGN_STATES",
    "CELL_STATES",
    "CampaignRepository",
    "CampaignRunner",
    "CampaignStateError",
    "CampaignValidationError",
    "build_campaign_manifest",
    "validate_campaign_id",
    "validate_campaign_manifest",
]
