"""Durable exploratory sweep supervision over the existing JobService.

The supervisor advances explicit lifecycle boundaries when ``tick`` is called.
It does not own a daemon scheduler and it never executes benchmark work in the
Dashboard process.  A claimed attempt is persisted before the durable child is
started, and the child's deterministic job id is the idempotency key used for
restart recovery.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from cluster.application.jobs import JobService
from cluster.application.sweep_models import build_cell_config
from cluster.application.sweep_planner import verify_plan
from cluster.benchmark.planner import strategy_work_units
from cluster.domain.sweep import ResolvedPlan, Trial
from cluster.infrastructure.storage import atomic_write_text, read_json_object


SWEEP_STATES = frozenset(
    {"draft", "ready", "running", "paused", "completed", "partial", "failed", "cancelled"}
)
TRIAL_STATES = frozenset(
    {"pending", "blocked", "running", "completed", "failed", "cancelled", "skipped", "needs_reconciliation"}
)
ATTEMPT_TERMINAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "blocked", "needs_reconciliation"}
)
BACKEND_NONTERMINAL_STATES = frozenset({"queued", "running"})
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class SweepStateError(RuntimeError):
    """A lifecycle transition would violate durable sweep invariants."""


class SweepBackend(Protocol):
    def start(self, plan: ResolvedPlan, trial: Trial, attempt: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def inspect(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def cancel(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]: ...


DriftChecker = Callable[[ResolvedPlan, Trial], Mapping[str, Any]]
PromptProvider = Callable[[ResolvedPlan, Trial], str]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SweepStateError(f"{label} is invalid")
    return value


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "at": utc_now(), **fields}


def _coverage(trials: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    result = {state: 0 for state in sorted(TRIAL_STATES)}
    for trial in trials:
        state = str(trial.get("status") or "")
        if state not in result:
            raise SweepStateError(f"invalid trial status: {state}")
        result[state] += 1
    result["total"] = len(trials)
    result["terminal"] = sum(
        result[state] for state in ("completed", "failed", "cancelled", "skipped")
    )
    return result


def _cell_for(plan: ResolvedPlan, trial: Trial):
    return next(cell for cell in plan.cells if cell.cell_id == trial.cell_id)


def _trial(manifest: Mapping[str, Any], trial_id: str) -> dict[str, Any]:
    for item in manifest.get("trials") or []:
        if isinstance(item, dict) and item.get("trial_id") == trial_id:
            return item
    raise SweepStateError(f"unknown trial: {trial_id}")


def _latest_attempt(trial: Mapping[str, Any]) -> dict[str, Any]:
    attempts = trial.get("attempts") or []
    if not attempts or not isinstance(attempts[-1], dict):
        raise SweepStateError("running trial has no durable attempt")
    return attempts[-1]


def _active_trials(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in manifest.get("trials") or []
        if isinstance(item, dict) and item.get("status") in {"running", "needs_reconciliation"}
    ]


def _resource_keys(plan: ResolvedPlan, trial: Trial) -> list[str]:
    cell = _cell_for(plan, trial)
    keys: set[str] = set()
    for worker in cell.workers:
        keys.add(f"worker:{worker.worker_id}")
        keys.add(f"endpoint:{worker.endpoint_identity}")
    return sorted(keys)


def validate_sweep_manifest(manifest: Mapping[str, Any]) -> None:
    _valid_id(str(manifest.get("sweep_id") or ""), "sweep_id")
    if manifest.get("schema_version") != 1 or manifest.get("artifact_type") != "exploratory_sweep_run":
        raise SweepStateError("invalid sweep manifest schema")
    if manifest.get("status") not in SWEEP_STATES:
        raise SweepStateError("invalid sweep status")
    if not isinstance(manifest.get("pause_requested"), bool) or not isinstance(
        manifest.get("cancel_requested"), bool
    ):
        raise SweepStateError("pause/cancel flags must be booleans")
    plan_raw = manifest.get("plan_snapshot")
    if not isinstance(plan_raw, Mapping):
        raise SweepStateError("plan snapshot is missing")
    plan = verify_plan(json.dumps(plan_raw, ensure_ascii=False))
    if plan.plan_sha256 != manifest.get("plan_sha256"):
        raise SweepStateError("plan hash does not match snapshot")
    trials = manifest.get("trials")
    if not isinstance(trials, list) or len(trials) != len(plan.trials):
        raise SweepStateError("manifest trials do not match plan")
    ids: set[str] = set()
    attempt_ids: set[str] = set()
    for item in trials:
        if not isinstance(item, Mapping) or item.get("trial_id") in ids:
            raise SweepStateError("trial ids must be unique")
        ids.add(str(item.get("trial_id") or ""))
        if item.get("status") not in TRIAL_STATES or not isinstance(item.get("attempts"), list):
            raise SweepStateError("invalid trial record")
        for attempt in item["attempts"]:
            attempt_id = str(attempt.get("attempt_id") or "") if isinstance(attempt, Mapping) else ""
            if not attempt_id or attempt_id in attempt_ids:
                raise SweepStateError("attempt ids must be globally unique")
            attempt_ids.add(attempt_id)
    if manifest.get("coverage") != _coverage(trials):
        raise SweepStateError("coverage does not match trials")
    cap = int((manifest.get("scheduling") or {}).get("max_parallel_jobs") or 0)
    if cap not in {1, 2} or len([t for t in trials if t.get("status") == "running"]) > cap:
        raise SweepStateError("active trial count exceeds scheduling policy")


class SweepRepository:
    """Private atomic manifests and append-only per-sweep event journals."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _dir(self, sweep_id: str) -> Path:
        return self.directory / _valid_id(sweep_id, "sweep_id")

    def _manifest(self, sweep_id: str) -> Path:
        return self._dir(sweep_id) / "manifest.json"

    def _events(self, sweep_id: str) -> Path:
        return self._dir(sweep_id) / "events.jsonl"

    def _lock(self, sweep_id: str):
        class Lock:
            def __init__(inner, outer: SweepRepository) -> None:
                inner.outer, inner.fd = outer, None

            def __enter__(inner):
                if inner.outer.directory.is_symlink():
                    raise SweepStateError("sweep repository must not be a symbolic link")
                inner.outer.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                target = inner.outer._dir(sweep_id)
                if target.is_symlink():
                    raise SweepStateError("sweep path must not be a symbolic link")
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.chmod(0o700)
                inner.fd = os.open(target / ".sweep.lock", os.O_RDWR | os.O_CREAT, 0o600)
                os.fchmod(inner.fd, 0o600)
                fcntl.flock(inner.fd, fcntl.LOCK_EX)

            def __exit__(inner, *_args):
                assert inner.fd is not None
                fcntl.flock(inner.fd, fcntl.LOCK_UN)
                os.close(inner.fd)
        return Lock(self)

    def _write(self, sweep_id: str, value: Mapping[str, Any]) -> None:
        validate_sweep_manifest(value)
        path = self._manifest(sweep_id)
        atomic_write_text(path, json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n", default_mode=0o600)
        path.chmod(0o600)

    def _append(self, sweep_id: str, event: Mapping[str, Any]) -> None:
        path = self._events(sweep_id)
        if path.is_symlink():
            raise SweepStateError("sweep event journal must not be a symbolic link")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def create(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        sweep_id = _valid_id(str(manifest.get("sweep_id") or ""), "sweep_id")
        with self._lock(sweep_id):
            if self._manifest(sweep_id).exists():
                raise SweepStateError(f"sweep already exists: {sweep_id}")
            value = dict(manifest)
            self._write(sweep_id, value)
            self._append(sweep_id, _event("sweep_created", plan_sha256=value["plan_sha256"]))
            return value

    def read(self, sweep_id: str) -> dict[str, Any]:
        path = self._manifest(sweep_id)
        if path.is_symlink() or not path.is_file():
            raise SweepStateError("sweep manifest must be a regular file")
        value = read_json_object(path)
        validate_sweep_manifest(value)
        return value

    def update(
        self, sweep_id: str, mutate: Callable[[dict[str, Any]], None], *, event: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock(sweep_id):
            value = self.read(sweep_id)
            mutate(value)
            value["coverage"] = _coverage(value["trials"])
            value["updated_at"] = utc_now()
            self._write(sweep_id, value)
            if event is not None:
                self._append(sweep_id, event)
            return value

    def append_event(self, sweep_id: str, event: Mapping[str, Any]) -> None:
        with self._lock(sweep_id):
            self._append(sweep_id, event)

    def read_events(self, sweep_id: str) -> list[dict[str, Any]]:
        try:
            lines = self._events(sweep_id).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        result: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                result.append(item)
        return result


def build_sweep_manifest(sweep_id: str, plan: ResolvedPlan) -> dict[str, Any]:
    """Freeze one verified executable plan into a ready durable manifest."""
    _valid_id(sweep_id, "sweep_id")
    # Recompilation in build_cell_config remains the final integrity check; the
    # snapshot here also protects every lifecycle read from accidental mutation.
    if not plan.executable or plan.resolution_state != "resolved":
        raise SweepStateError("only a resolved executable plan can be started")
    now = utc_now()
    trials = [
        {
            **trial.to_dict(),
            "attempts": [],
            "official_attempt_id": None,
            "failure_code": None,
            "retry_reason": None,
            "resource_keys": _resource_keys(plan, trial),
        }
        for trial in plan.trials
    ]
    manifest = {
        "schema_version": 1,
        "artifact_type": "exploratory_sweep_run",
        "sweep_id": sweep_id,
        "revision": plan.spec.revision,
        "plan_sha256": plan.plan_sha256,
        "plan_snapshot": plan.to_dict(),
        "status": "ready",
        "phase": "ready",
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
        "pause_requested": False,
        "pause_reason": None,
        "cancel_requested": False,
        "cancel_reason": None,
        "retry_policy": {"automatic": False, "official_summary": "latest_completed_attempt"},
        "scheduling": {
            "mode": plan.spec.execution.mode,
            "max_parallel_jobs": plan.spec.execution.max_parallel_jobs,
            "generation_order": "plan_generation_order_index",
            "dispatch_order": plan.spec.execution.order,
            "order_seed": plan.spec.execution.order_seed,
            "backfill_policy": plan.spec.execution.backfill_policy,
            "backfill_window": plan.spec.execution.backfill_window,
            "realized_dispatch_order": [],
            "overlap_decisions": [],
        },
        "failure_policy": plan.spec.execution.failure_policy,
        "halt_after_failure": False,
        "trials": trials,
        "coverage": _coverage(trials),
        "snapshots": [{"type": "resolved_plan", "at": now, "sha256": plan.plan_sha256}],
        "last_drift": None,
    }
    validate_sweep_manifest(manifest)
    return manifest


class SweepJobDocumentFactory:
    """Bind one sweep trial to one existing-engine durable job document."""

    def __init__(self, prompt_provider: PromptProvider) -> None:
        self.prompt_provider = prompt_provider

    @staticmethod
    def _resource_workers(plan: ResolvedPlan, trial: Trial) -> list[dict[str, Any]]:
        cell = _cell_for(plan, trial)
        output = []
        for worker in cell.workers:
            endpoint = worker.endpoint_identity
            host, separator, port = endpoint.rpartition(":")
            if not separator or not port.isdigit():
                host, port = endpoint, "1"
            output.append({"worker_id": worker.worker_id, "host": host, "api_port": int(port)})
        return output

    def __call__(self, plan: ResolvedPlan, trial: Trial, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        config = build_cell_config(
            plan,
            trial_id=trial.trial_id,
            sweep_id=str(attempt["sweep_id"]),
            attempt_id=str(attempt["attempt_id"]),
            prompt_text=self.prompt_provider(plan, trial),
        )
        config.suite_id = str(attempt["suite_id"])
        config.model_index = 1
        config.model_count = 1
        config.validate()
        units = strategy_work_units(config, len(config.node_names))
        now = utc_now()
        parallel = plan.spec.execution.mode == "disjoint_parallel"
        return {
            "schema_version": 1,
            "artifact_type": "experiment_job",
            "id": attempt["suite_id"],
            "job_id": attempt["backend_job_id"],
            "suite_id": attempt["suite_id"],
            "experiment_id": config.experiment_id,
            "name": f"exploratory sweep {attempt['sweep_id']}",
            "status": "queued",
            "phase": "queued",
            "completed": 0,
            "total": units,
            "model_completed": 0,
            "model_total": units,
            "strategy": str(config.execution_strategy),
            "started_at": now,
            "nodes": list(config.node_names),
            "model_ids": [config.model_id],
            "current_model": config.model_id,
            "model_index": 0,
            "model_count": 1,
            "completed_models": 0,
            "summaries": [],
            "errors": [],
            "latest": None,
            "error": "",
            "continue_on_model_error": False,
            "model_cooldown_s": 0.0,
            "resource_cooldown_s": plan.spec.execution.cooldown_s,
            "max_parallel_jobs": plan.spec.execution.max_parallel_jobs,
            "worker_ownership_capable": parallel,
            "resource_workers": self._resource_workers(plan, trial),
            "config": asdict(config),
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
            "sweep_id": attempt["sweep_id"],
            "sweep_trial_id": trial.trial_id,
            "sweep_attempt_id": attempt["attempt_id"],
        }


def _job_result(job: Mapping[str, Any]) -> dict[str, Any]:
    raw = str(job.get("status") or "failed")
    reservation = job.get("resource_reservation") if isinstance(job.get("resource_reservation"), Mapping) else {}
    summary = job.get("summary") if isinstance(job.get("summary"), Mapping) else {}
    summaries = summary.get("summaries") if isinstance(summary.get("summaries"), list) else []
    latest = summaries[-1] if summaries and isinstance(summaries[-1], Mapping) else {}
    if raw == "orphaned" or reservation.get("status") == "quarantined":
        status = "needs_reconciliation"
    else:
        status = raw
    return {
        "status": status,
        "backend_job_id": job.get("job_id"),
        "run_id": job.get("current_run_id") or latest.get("run_id"),
        "finished_at": job.get("finished_at"),
        "cleanup_status": reservation.get("status"),
        "failure_code": (
            "RESOURCE_RECONCILIATION_REQUIRED" if status == "needs_reconciliation"
            else "DURABLE_JOB_FAILED" if status == "failed" else None
        ),
    }


class DurableSweepJobBackend:
    """Adapter from a sweep attempt to the existing JobService child path."""

    def __init__(self, service: JobService, factory: SweepJobDocumentFactory) -> None:
        self.service, self.factory = service, factory

    def start(self, plan: ResolvedPlan, trial: Trial, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        return _job_result(self.service.start(dict(self.factory(plan, trial, attempt))))

    def inspect(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        job_id = str(attempt.get("backend_job_id") or "")
        if not job_id:
            raise SweepStateError("attempt has no durable backend job id")
        return _job_result(self.service.get(job_id))

    def cancel(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        job_id = str(attempt.get("backend_job_id") or "")
        try:
            job = self.service.get(job_id)
        except FileNotFoundError:
            return {"status": "cancelled", "backend_job_id": job_id}
        if job.get("status") in {"queued", "running"}:
            job = self.service.cancel(job_id)
        return _job_result(job)


class SweepSupervisor:
    """Tick-driven durable supervisor; all benchmark work remains in JobService."""

    def __init__(self, repository: SweepRepository, backend: SweepBackend, drift_checker: DriftChecker) -> None:
        self.repository, self.backend, self.drift_checker = repository, backend, drift_checker

    def create(self, sweep_id: str, plan: ResolvedPlan) -> dict[str, Any]:
        return self.repository.create(build_sweep_manifest(sweep_id, plan))

    def pause(self, sweep_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise SweepStateError("pause requires a reason")
        def apply(value: dict[str, Any]) -> None:
            if value["status"] not in {"ready", "running"}:
                raise SweepStateError("only ready or running sweeps can be paused")
            value.update({"pause_requested": True, "pause_reason": reason.strip(), "phase": "pausing"})
            if not [t for t in value["trials"] if t["status"] == "running"]:
                value.update({"status": "paused", "phase": "paused"})
        return self.repository.update(sweep_id, apply, event=_event("sweep_pause_requested", reason=reason.strip()))

    def resume(self, sweep_id: str) -> dict[str, Any]:
        def apply(value: dict[str, Any]) -> None:
            if value["status"] != "paused" or any(t["status"] == "needs_reconciliation" for t in value["trials"]):
                raise SweepStateError("paused sweep requires reconciliation before resume")
            value.update({"status": "ready", "phase": "ready", "pause_requested": False, "pause_reason": None})
        return self.repository.update(sweep_id, apply, event=_event("sweep_resumed"))

    def cancel(self, sweep_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise SweepStateError("cancel requires a reason")
        manifest = self.repository.read(sweep_id)
        for active in [t for t in manifest["trials"] if t["status"] == "running"]:
            try:
                self.backend.cancel(_latest_attempt(active))
            except Exception as exc:
                self.repository.append_event(sweep_id, _event("sweep_cancel_backend_error", trial_id=active["trial_id"], error_type=type(exc).__name__))
        def apply(value: dict[str, Any]) -> None:
            if value["status"] in {"completed", "partial", "failed", "cancelled"}:
                return
            value.update({"cancel_requested": True, "cancel_reason": reason.strip(), "phase": "cancelling"})
            if not [t for t in value["trials"] if t["status"] == "running"]:
                for trial in value["trials"]:
                    if trial["status"] == "pending": trial["status"] = "cancelled"
                value.update({"status": "cancelled", "phase": "finished", "finished_at": utc_now()})
        return self.repository.update(sweep_id, apply, event=_event("sweep_cancel_requested", reason=reason.strip()))

    def retry(self, sweep_id: str, trial_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise SweepStateError("manual retry requires a reason")
        def apply(value: dict[str, Any]) -> None:
            if any(t["status"] == "running" for t in value["trials"]):
                raise SweepStateError("cannot retry while sweep jobs are running")
            target = _trial(value, trial_id)
            if target["status"] not in {"failed", "cancelled"}:
                raise SweepStateError("only failed or cancelled trials can be retried")
            target.update({"status": "pending", "failure_code": None, "retry_reason": reason.strip()})
            value.update({"status": "ready", "phase": "ready", "finished_at": None, "cancel_requested": False, "cancel_reason": None})
        return self.repository.update(sweep_id, apply, event=_event("sweep_retry_requested", trial_id=trial_id, reason=reason.strip()))

    def _finalize(self, sweep_id: str, trial_id: str, result: Mapping[str, Any]) -> dict[str, Any]:
        status = str(result.get("status") or "failed")
        if status not in {"completed", "failed", "cancelled", "needs_reconciliation"}:
            raise SweepStateError(f"attempt is not terminal: {status}")
        def apply(value: dict[str, Any]) -> None:
            target = _trial(value, trial_id)
            attempt = _latest_attempt(target)
            if attempt["status"] in ATTEMPT_TERMINAL_STATES:
                return
            cleanup = result.get("cleanup_status")
            effective = status
            if status == "completed" and cleanup not in {"released", "completed"}:
                effective = "needs_reconciliation"
            attempt.update({
                "status": effective, "finished_at": result.get("finished_at") or utc_now(),
                "run_id": result.get("run_id"), "cleanup_status": cleanup,
                "failure_code": result.get("failure_code"),
            })
            target.update({"status": effective, "failure_code": attempt.get("failure_code")})
            if effective == "completed": target["official_attempt_id"] = attempt["attempt_id"]
            if effective == "needs_reconciliation":
                value.update({"status": "paused", "phase": "needs_reconciliation", "pause_requested": True})
            elif value["cancel_requested"]:
                if not any(t["status"] == "running" for t in value["trials"]):
                    for remaining in value["trials"]:
                        if remaining["status"] == "pending": remaining["status"] = "cancelled"
                    value.update({"status": "cancelled", "phase": "finished", "finished_at": utc_now()})
            elif value["pause_requested"]:
                if not any(t["status"] == "running" for t in value["trials"]):
                    value.update({"status": "paused", "phase": "paused"})
            elif effective == "failed" and value["failure_policy"] == "stop":
                for remaining in value["trials"]:
                    if remaining["status"] == "pending": remaining["status"] = "skipped"
                value["halt_after_failure"] = True
                if any(t["status"] == "running" for t in value["trials"]):
                    value.update({"status": "running", "phase": "draining_after_failure"})
                else:
                    value.update({"status": "failed", "phase": "finished", "finished_at": utc_now()})
            elif not any(t["status"] == "running" for t in value["trials"]):
                pending = any(t["status"] == "pending" for t in value["trials"])
                if pending:
                    value.update({"status": "running", "phase": "ready"})
                else:
                    failures = any(t["status"] in {"failed", "cancelled"} for t in value["trials"])
                    terminal = "failed" if value.get("halt_after_failure") else "partial" if failures else "completed"
                    value.update({"status": terminal, "phase": "finished", "finished_at": utc_now()})
        return self.repository.update(sweep_id, apply, event=_event("sweep_attempt_finished", trial_id=trial_id, status=status, run_id=result.get("run_id")))

    def _reconcile_active(self, sweep_id: str, manifest: Mapping[str, Any]) -> dict[str, Any] | None:
        for active in [t for t in manifest["trials"] if t["status"] == "running"]:
            attempt = _latest_attempt(active)
            try:
                result = dict(self.backend.inspect(attempt))
            except FileNotFoundError:
                # No JobService document means no child could have passed its
                # durable-before-spawn boundary. Reuse the same attempt/job id.
                if attempt["status"] not in {"claimed", "starting"}:
                    return self._mark_uncertain(sweep_id, active["trial_id"], "BACKEND_JOB_MISSING")
                plan = verify_plan(json.dumps(manifest["plan_snapshot"], ensure_ascii=False))
                return self._start_claimed(sweep_id, active["trial_id"], plan)
            except Exception as exc:
                return self._mark_uncertain(sweep_id, active["trial_id"], f"INSPECTION_UNCERTAIN:{type(exc).__name__}")
            if result.get("status") in {"completed", "failed", "cancelled", "needs_reconciliation"}:
                return self._finalize(sweep_id, active["trial_id"], result)
            if result.get("status") in BACKEND_NONTERMINAL_STATES and attempt["status"] != result["status"]:
                def observed(value: dict[str, Any], trial_id=active["trial_id"], status=result["status"]):
                    _latest_attempt(_trial(value, trial_id))["status"] = status
                    value["phase"] = status
                return self.repository.update(sweep_id, observed, event=_event("sweep_attempt_observed", trial_id=active["trial_id"], status=result["status"]))
        return None

    def _mark_uncertain(self, sweep_id: str, trial_id: str, reason: str) -> dict[str, Any]:
        def apply(value: dict[str, Any]) -> None:
            target = _trial(value, trial_id)
            attempt = _latest_attempt(target)
            attempt.update({"status": "needs_reconciliation", "failure_code": reason})
            target.update({"status": "needs_reconciliation", "failure_code": reason})
            value.update({"status": "paused", "phase": "needs_reconciliation", "pause_requested": True})
        return self.repository.update(sweep_id, apply, event=_event("sweep_attempt_uncertain", trial_id=trial_id, reason=reason))

    def _select(
        self, manifest: Mapping[str, Any], plan: ResolvedPlan
    ) -> tuple[list[str], list[dict[str, Any]]]:
        if manifest.get("halt_after_failure"):
            return [], []
        active = [t for t in manifest["trials"] if t["status"] == "running"]
        cap = int(manifest["scheduling"]["max_parallel_jobs"])
        slots = cap - len(active)
        if slots <= 0: return [], []
        occupied = {key for item in active for key in item["resource_keys"]}
        pending = sorted((t for t in manifest["trials"] if t["status"] == "pending"), key=lambda t: t["execution_order_index"])
        policy = manifest["scheduling"]["backfill_policy"]
        candidates = pending[:1] if policy == "strict" else pending[: int(manifest["scheduling"]["backfill_window"])]
        selected: list[str] = []
        decisions: list[dict[str, Any]] = []
        for item in candidates:
            overlap = sorted(occupied.intersection(item["resource_keys"]))
            if overlap:
                decisions.append({
                    "trial_id": item["trial_id"],
                    "overlap_resource_ids": overlap,
                    "active_trial_ids": [t["trial_id"] for t in active],
                    "at": utc_now(),
                })
                if policy == "strict": break
                continue
            selected.append(item["trial_id"])
            occupied.update(item["resource_keys"])
            if len(selected) == slots: break
        return selected, decisions

    def _claim(self, sweep_id: str, trial_id: str) -> dict[str, Any]:
        nonce = secrets.token_hex(12)
        attempt_id = f"attempt_{nonce}"
        digest = hashlib.sha256(f"{sweep_id}\0{trial_id}\0{attempt_id}".encode()).hexdigest()[:20]
        now = utc_now()
        def apply(value: dict[str, Any]) -> None:
            target = _trial(value, trial_id)
            if target["status"] != "pending": raise SweepStateError("trial was already claimed")
            prior = target["attempts"][-1]["attempt_id"] if target["attempts"] else None
            attempt = {
                "attempt_id": attempt_id, "sweep_id": sweep_id, "trial_id": trial_id,
                "status": "claimed", "claimed_at": now, "started_at": None, "finished_at": None,
                "backend_job_id": f"job_sweep_{digest}", "suite_id": f"suite_sweep_{digest}",
                "run_id": None, "cleanup_status": None, "failure_code": None,
                "retry_of_attempt_id": prior, "retry_reason": target.get("retry_reason"),
            }
            target["attempts"].append(attempt)
            target["status"] = "running"
            value.update({"status": "running", "phase": "claimed"})
            value["scheduling"]["realized_dispatch_order"].append({
                "index": len(value["scheduling"]["realized_dispatch_order"]),
                "trial_id": trial_id, "attempt_id": attempt_id, "at": now,
            })
            target["retry_reason"] = None
        return self.repository.update(sweep_id, apply, event=_event("sweep_attempt_claimed", trial_id=trial_id, attempt_id=attempt_id))

    def _start_claimed(self, sweep_id: str, trial_id: str, plan: ResolvedPlan) -> dict[str, Any]:
        manifest = self.repository.read(sweep_id)
        target = _trial(manifest, trial_id)
        attempt = _latest_attempt(target)
        plan_trial = next(item for item in plan.trials if item.trial_id == trial_id)
        try:
            existing = dict(self.backend.inspect(attempt))
        except FileNotFoundError:
            existing = {}
        except Exception as exc:
            return self._mark_uncertain(sweep_id, trial_id, f"START_LOOKUP_UNCERTAIN:{type(exc).__name__}")
        try:
            started = existing or dict(self.backend.start(plan, plan_trial, attempt))
        except Exception as exc:
            # A lost Start response is resolved by looking up the same durable
            # job id. Never allocate a replacement attempt here.
            try:
                started = dict(self.backend.inspect(attempt))
            except FileNotFoundError:
                return self._finalize(sweep_id, trial_id, {"status": "failed", "failure_code": f"START_FAILED:{type(exc).__name__}"})
            except Exception as inspect_exc:
                return self._mark_uncertain(sweep_id, trial_id, f"START_RESPONSE_UNCERTAIN:{type(inspect_exc).__name__}")
        status = str(started.get("status") or "running")
        if status in {"completed", "failed", "cancelled", "needs_reconciliation"}:
            return self._finalize(sweep_id, trial_id, started)
        if status not in BACKEND_NONTERMINAL_STATES:
            return self._mark_uncertain(sweep_id, trial_id, "INVALID_BACKEND_STATUS")
        def record(value: dict[str, Any]) -> None:
            current = _latest_attempt(_trial(value, trial_id))
            current.update({"status": status, "started_at": utc_now(), "run_id": started.get("run_id")})
            value["phase"] = status
        return self.repository.update(sweep_id, record, event=_event("sweep_attempt_started", trial_id=trial_id, attempt_id=attempt["attempt_id"]))

    def tick(self, sweep_id: str) -> dict[str, Any]:
        """Advance lifecycle boundaries without launching a competing scheduler."""
        manifest = self.repository.read(sweep_id)
        if manifest["status"] in {"paused", "completed", "partial", "failed", "cancelled"}:
            return manifest
        reconciled = self._reconcile_active(sweep_id, manifest)
        if reconciled is not None: return reconciled
        manifest = self.repository.read(sweep_id)
        if manifest["cancel_requested"]: return self.cancel(sweep_id, reason=manifest.get("cancel_reason") or "cancel requested")
        if manifest["pause_requested"] and not _active_trials(manifest):
            return self.pause(sweep_id, reason=manifest.get("pause_reason") or "pause requested")
        plan = verify_plan(json.dumps(manifest["plan_snapshot"], ensure_ascii=False))
        selected, overlap_decisions = self._select(manifest, plan)
        if overlap_decisions:
            def record_overlap(value: dict[str, Any]) -> None:
                value["scheduling"]["overlap_decisions"].extend(overlap_decisions)
            manifest = self.repository.update(
                sweep_id,
                record_overlap,
                event=_event("sweep_dispatch_overlap", decisions=overlap_decisions),
            )
        if not selected:
            return manifest
        # One tick may fill all allowed disjoint slots; each child still has a
        # separately persisted claim before its JobService start call.
        result = manifest
        for trial_id in selected:
            plan_trial = next(item for item in plan.trials if item.trial_id == trial_id)
            drift = dict(self.drift_checker(plan, plan_trial))
            issues = list(drift.get("blocking_issues") or [])
            if issues:
                def block(value: dict[str, Any]) -> None:
                    target = _trial(value, trial_id)
                    target["drift"] = issues
                    value.update({"status": "paused", "phase": "drift_blocked", "pause_requested": True, "last_drift": {"trial_id": trial_id, "issues": issues, "at": utc_now()}})
                return self.repository.update(sweep_id, block, event=_event("sweep_drift_blocked", trial_id=trial_id, issues=issues))
            self._claim(sweep_id, trial_id)
            result = self._start_claimed(sweep_id, trial_id, plan)
            if result["status"] in {"paused", "failed", "cancelled"}: break
        return result


__all__ = [
    "DurableSweepJobBackend", "SweepBackend", "SweepJobDocumentFactory", "SweepRepository",
    "SweepStateError", "SweepSupervisor", "build_sweep_manifest", "validate_sweep_manifest",
]
