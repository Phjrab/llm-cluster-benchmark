"""Durable child-process experiment job registry and recovery service."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from cluster.infrastructure.process import (
    KILL_SIGNAL,
    TERMINATE_SIGNAL,
    ProcessIdentity,
    ProcessInspector,
    PsutilProcessInspector,
    can_signal,
)
from cluster.infrastructure.storage import FilesystemJobRepository, StorageCorruptionError
from cluster.application.resources import (
    FilesystemResourceCoordinator,
    ResourceConflictError,
    WorkerResource,
)
from cluster.integrations.legacy_inventory_runtime import load_nodes


NONTERMINAL_JOB_STATES = frozenset({"queued", "running"})
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled", "orphaned"})
JOB_STATES = NONTERMINAL_JOB_STATES | TERMINAL_JOB_STATES


def scrub_terminal_prompt(value: Dict[str, Any]) -> None:
    """Remove private recovery input once a job reaches a terminal state."""
    config = value.get("config")
    if not isinstance(config, dict) or config.get("persist_prompt") is not False:
        return
    raw_prompt = config.pop("prompt", None)
    if raw_prompt is None:
        return
    prompt = str(raw_prompt)
    config["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    config["prompt_chars"] = len(prompt)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobProcessSpec:
    job_id: str
    jobs_dir: Path
    inventory_path: Path
    results_dir: Path
    project_root: Path
    python_bin: Path

    @property
    def argv(self) -> tuple[str, ...]:
        return (
            str(self.python_bin),
            "-m",
            "cluster.application.job_process",
            "--job-id",
            self.job_id,
            "--jobs-dir",
            str(self.jobs_dir),
            "--inventory",
            str(self.inventory_path),
            "--results-dir",
            str(self.results_dir),
        )

    @property
    def log_path(self) -> Path:
        return self.jobs_dir / f"{self.job_id}.log"


JobChanged = Callable[[Dict[str, Any]], None]


class JobService:
    """Own job creation/cancellation/recovery, never benchmark execution itself."""

    def __init__(
        self,
        jobs_dir: Path,
        inventory_path: Path,
        results_dir: Path,
        project_root: Path,
        *,
        python_bin: Optional[Path] = None,
        inspector: Optional[ProcessInspector] = None,
        on_change: Optional[JobChanged] = None,
        cancel_grace_s: float = 120.0,
        terminate_grace_s: float = 10.0,
        poll_interval_s: float = 0.25,
        startup_grace_s: float = 5.0,
        identity_retry_s: float = 0.5,
        heartbeat_timeout_s: float = 30.0,
        start_watcher: bool = True,
    ) -> None:
        self.jobs_dir = Path(jobs_dir)
        self.inventory_path = Path(inventory_path)
        self.results_dir = Path(results_dir)
        self.project_root = Path(project_root)
        self.python_bin = Path(python_bin or sys.executable)
        self.repository = FilesystemJobRepository(self.jobs_dir)
        self.resources = FilesystemResourceCoordinator(self.jobs_dir / "_resources")
        self.inspector = inspector or PsutilProcessInspector()
        self.on_change = on_change
        self.cancel_grace_s = cancel_grace_s
        self.terminate_grace_s = terminate_grace_s
        self.poll_interval_s = poll_interval_s
        self.startup_grace_s = startup_grace_s
        self.identity_retry_s = identity_retry_s
        self.heartbeat_timeout_s = max(1.0, heartbeat_timeout_s)
        self._lock = threading.RLock()
        self._watch_stop = threading.Event()
        self._last_change: dict[str, tuple[str, str]] = {}
        self._children: dict[int, subprocess.Popen[bytes]] = {}
        self._cancel_fallbacks: set[str] = set()
        self._watch_thread: Optional[threading.Thread] = None
        self.jobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.jobs_dir.chmod(0o700)
        recovered = self.recover()
        for job in recovered:
            if (
                job.get("status") in NONTERMINAL_JOB_STATES
                and job.get("cancel_requested") is True
            ):
                self._schedule_cancel_fallback(str(job["job_id"]))
        if start_watcher:
            self._watch_thread = threading.Thread(
                target=self._watch,
                name="cluster-job-registry-watch",
                daemon=True,
            )
            self._watch_thread.start()

    def shutdown(self) -> None:
        """Stop only this dashboard instance's registry watcher."""
        self._watch_stop.set()
        thread = self._watch_thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=max(1.0, self.poll_interval_s * 4))

    def _spec(self, job_id: str) -> JobProcessSpec:
        return JobProcessSpec(
            job_id=job_id,
            jobs_dir=self.jobs_dir,
            inventory_path=self.inventory_path,
            results_dir=self.results_dir,
            project_root=self.project_root,
            python_bin=self.python_bin,
        )

    def _expected_process(self, job: Mapping[str, Any]) -> Optional[ProcessIdentity]:
        value = job.get("process")
        if not isinstance(value, Mapping):
            return None
        try:
            identity = ProcessIdentity.from_dict(value)
        except (KeyError, TypeError, ValueError):
            return None
        if not self._identity_matches_spec(identity, str(job.get("job_id") or "")):
            return None
        return identity

    def _identity_matches_spec(self, identity: ProcessIdentity, job_id: str) -> bool:
        spec = self._spec(job_id)
        if not identity.argv:
            return False
        try:
            configured_python = self.python_bin.resolve(strict=True)
            observed_executable = Path(identity.executable).resolve(strict=True)
            observed_argv0 = Path(identity.argv[0]).resolve(strict=True)
        except OSError:
            return False

        allowed_executables = {configured_python}
        # Framework Python on macOS re-execs through Python.app.  psutil then
        # reports both the executable and argv[0] as the app shim rather than
        # the venv/base interpreter used by Popen.  Accept only the exact shim
        # belonging to the configured framework version; every remaining argv
        # element, the cwd, PID, creation time, and user are still protected by
        # the persisted ProcessIdentity and can_signal checks.
        version_root = configured_python.parent.parent
        if (
            configured_python.parent.name == "bin"
            and version_root.parent.name == "Versions"
            and version_root.parent.parent.name == "Python.framework"
        ):
            framework_shim = (
                version_root / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
            )
            try:
                allowed_executables.add(framework_shim.resolve(strict=True))
            except OSError:
                pass
        return (
            identity.argv[1:] == spec.argv[1:]
            and observed_argv0 == observed_executable
            and observed_executable in allowed_executables
            and Path(identity.cwd) == self.project_root
        )

    def _live_identity(self, job: Mapping[str, Any]) -> Optional[ProcessIdentity]:
        expected = self._expected_process(job)
        if expected is not None:
            observed = self.inspector.inspect(expected.pid)
            if observed is None or not can_signal(expected, observed):
                return None
            return observed
        spawned_pid = job.get("spawned_pid")
        if not isinstance(spawned_pid, int) or spawned_pid <= 1:
            return None
        observed = self.inspector.inspect(spawned_pid)
        if observed is None or not self._identity_matches_spec(
            observed, str(job.get("job_id") or "")
        ):
            return None
        self.repository.update(
            str(job["job_id"]),
            lambda value: value.update(
                {"process": observed.to_dict(), "updated_at": utc_now()}
            ),
        )
        return observed

    def _terminal_from_suite(self, job: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        suite_id = str(job.get("suite_id") or "")
        if not suite_id:
            return None
        path = self.results_dir / "_suites" / f"{suite_id}.json"
        try:
            suite = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return None
        if not isinstance(suite, dict) or suite.get("suite_id") != suite_id:
            return None
        status = str(suite.get("status") or "")
        if status not in {"completed", "partial", "failed", "cancelled"}:
            return None
        job_status = (
            "completed"
            if status == "completed"
            else "cancelled"
            if status == "cancelled"
            else "failed"
        )
        return {
            "status": job_status,
            "phase": "finished",
            "suite_status": status,
            "summary": suite,
            "summaries": list(suite.get("summaries") or []),
            "errors": list(suite.get("errors") or []),
            "finished_at": suite.get("finished_at") or utc_now(),
            "updated_at": utc_now(),
        }

    def _worker_resources(self, job: Mapping[str, Any]) -> list[WorkerResource]:
        supplied = job.get("resource_workers")
        if isinstance(supplied, list) and supplied:
            workers = [
                WorkerResource(
                    worker_id=str(item["worker_id"]),
                    host=str(item["host"]),
                    api_port=int(item["api_port"]),
                )
                for item in supplied
                if isinstance(item, Mapping)
            ]
        else:
            config = job.get("config") if isinstance(job.get("config"), Mapping) else {}
            names = [str(item) for item in (job.get("nodes") or config.get("node_names") or [])]
            try:
                inventory = {node.name: node for node in load_nodes(
                    self.inventory_path, include_disabled=True, require_legacy_head=False
                )}
            except (FileNotFoundError, OSError, ValueError):
                inventory = {}
            workers = [
                WorkerResource(
                    worker_id=name,
                    host=(inventory[name].host if name in inventory else f"unknown-{name}"),
                    api_port=(inventory[name].api_port if name in inventory else 1),
                )
                for name in names
            ]
        if not workers:
            raise ValueError("A durable job must reserve at least one Worker")
        return workers

    @staticmethod
    def _start_fingerprint(job: Mapping[str, Any]) -> str:
        stable = {
            key: job.get(key)
            for key in (
                "job_id", "suite_id", "config", "model_ids", "nodes", "resource_workers",
                "continue_on_model_error", "model_cooldown_s", "resource_cooldown_s",
                "max_parallel_jobs", "campaign_attempt_id",
                "sweep_id", "sweep_trial_id", "sweep_attempt_id",
            )
        }
        return hashlib.sha256(
            json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _lease_matches(self, job: Mapping[str, Any]) -> bool:
        reservation = job.get("resource_reservation")
        if not isinstance(reservation, Mapping):
            return False
        return any(
            lease.get("lease_id") == reservation.get("lease_id")
            and lease.get("owner_job_id") == job.get("job_id")
            and lease.get("fencing_epoch") == reservation.get("fencing_epoch")
            and lease.get("status") in {"held", "releasing"}
            for lease in self.resources.active()
        )

    def _quarantine_job_lease(self, job: Mapping[str, Any], reason: str) -> None:
        reservation = job.get("resource_reservation")
        if not isinstance(reservation, Mapping):
            return
        try:
            self.resources.quarantine(
                lease_id=str(reservation["lease_id"]),
                owner_job_id=str(job["job_id"]),
                fencing_epoch=int(reservation["fencing_epoch"]),
                reason=reason,
                evidence={"job_status": job.get("status"), "observed_at": utc_now()},
            )
        except ResourceConflictError:
            pass

    def _live_identity_with_retry(
        self, job: Mapping[str, Any]
    ) -> Optional[ProcessIdentity]:
        """Confirm liveness across a bounded transient inspection window."""
        deadline = time.monotonic() + self.identity_retry_s
        while True:
            try:
                observed = self._live_identity(job)
            except RuntimeError:
                observed = None
            if observed is not None:
                return observed
            if time.monotonic() >= deadline:
                return None
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _within_startup_grace(self, job: Mapping[str, Any]) -> bool:
        """Allow a freshly spawned queued child time to claim its identity.

        On macOS the Python launcher can briefly re-exec between ``Popen`` and
        the child process writing its authoritative identity.  Recovery must
        not turn that bounded hand-off into an orphan.  The child may already
        have persisted ``running`` when the transient inspection gap occurs;
        all identity mismatches after the short grace remain fail-closed.
        """
        if job.get("status") not in {"queued", "running"}:
            return False
        spawned_pid = job.get("spawned_pid")
        if job.get("status") == "running" and (
            not isinstance(spawned_pid, int) or spawned_pid <= 1
        ):
            return False
        created_at = job.get("created_at")
        if not isinstance(created_at, str):
            return False
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
        return 0.0 <= age_s <= self.startup_grace_s

    def recover(self) -> list[Dict[str, Any]]:
        """Reconcile registry state with result artifacts and exact processes."""
        self._reap_children()
        self.resources.quarantine_stale(
            heartbeat_before=(
                datetime.now(timezone.utc) - timedelta(seconds=self.heartbeat_timeout_s)
            ).isoformat()
        )
        recovered: list[Dict[str, Any]] = []
        with self._lock:
            for job in self.repository.list(limit=0):
                job_id = str(job.get("job_id") or "")
                if not job_id:
                    continue
                events = self.repository.read_events(job_id, limit=1)
                if events:
                    latest = events[-1]
                    if int(latest.get("sequence") or 0) > int(job.get("event_sequence") or 0):
                        job = self.repository.update(
                            job_id,
                            lambda value: value.update(
                                {
                                    "latest_event": latest,
                                    "event_sequence": int(latest.get("sequence") or 0),
                                    "updated_at": utc_now(),
                                }
                            ),
                        )
                if job.get("status") not in NONTERMINAL_JOB_STATES:
                    continue
                terminal = self._terminal_from_suite(job)
                if terminal is not None:
                    reservation = job.get("resource_reservation")
                    unresolved = False
                    if isinstance(reservation, Mapping):
                        unresolved = any(
                            lease.get("lease_id") == reservation.get("lease_id")
                            and lease.get("fencing_epoch") == reservation.get("fencing_epoch")
                            for lease in self.resources.active()
                        )
                    if unresolved:
                        terminal.update(
                            {
                                "status": "orphaned",
                                "suite_status": terminal.get("suite_status"),
                                "error": "Suite finished without authoritative resource release",
                                "errors": [
                                    *list(terminal.get("errors") or []),
                                    {
                                        "stage": "resource_recovery",
                                        "error": "RESOURCE_RECONCILIATION_REQUIRED",
                                    },
                                ],
                            }
                        )
                    def finish_recovery(value: Dict[str, Any]) -> None:
                        value.update(terminal)
                        if unresolved and isinstance(value.get("resource_reservation"), dict):
                            value["resource_reservation"].update(
                                {"status": "quarantined", "cleanup_verified": False}
                            )
                        scrub_terminal_prompt(value)

                    updated = self.repository.update(job_id, finish_recovery)
                    if unresolved:
                        self._quarantine_job_lease(
                            updated, "SUITE_FINISHED_WITHOUT_RESOURCE_RELEASE"
                        )
                    recovered.append(updated)
                    continue
                if self._live_identity_with_retry(job) is not None:
                    recovered.append(job)
                    continue
                if self._within_startup_grace(job):
                    recovered.append(job)
                    continue
                error = {
                    "stage": "job_recovery",
                    "error": "Job process identity is missing, stale, or no longer running",
                }

                def orphan(value: Dict[str, Any]) -> None:
                    if isinstance(value.get("resource_reservation"), dict):
                        value["resource_reservation"].update(
                            {"status": "quarantined", "cleanup_verified": False}
                        )
                    value.update(
                        {
                            "status": "orphaned",
                            "phase": "finished",
                            "orphaned_from_status": value.get("status"),
                            "finished_at": utc_now(),
                            "updated_at": utc_now(),
                            "error": error["error"],
                            "errors": [*(value.get("errors") or []), error],
                        }
                    )
                    scrub_terminal_prompt(value)

                updated = self.repository.update(job_id, orphan)
                self._quarantine_job_lease(updated, "JOB_PROCESS_IDENTITY_LOST")
                recovered.append(updated)
        return recovered

    def list(self, limit: int = 100) -> list[Dict[str, Any]]:
        self._reap_children()
        return self.repository.list(limit=limit)

    def resource_registry(self) -> Dict[str, Any]:
        return self.resources.snapshot()

    def reconcile_resource(
        self, lease_id: str, fencing_epoch: int, evidence: Mapping[str, Any]
    ) -> Dict[str, Any]:
        reconciled = self.resources.reconcile(
            lease_id=lease_id, fencing_epoch=fencing_epoch, evidence=evidence
        )
        owner_job_id = str(reconciled.get("owner_job_id") or "")
        if owner_job_id:
            try:
                self.repository.update(
                    owner_job_id,
                    lambda value: (
                        value.get("resource_reservation", {}).update(
                            {"status": "released", "reconciled": True}
                        )
                        if isinstance(value.get("resource_reservation"), dict)
                        else None
                    ),
                )
            except FileNotFoundError:
                pass
        return reconciled

    def get(self, job_id: str) -> Dict[str, Any]:
        self.recover()
        return self.repository.read(job_id)

    def active_jobs(self) -> list[Dict[str, Any]]:
        self.recover()
        return [
            job for job in self.repository.list(limit=0)
            if job.get("status") in NONTERMINAL_JOB_STATES
        ]

    def active(self, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if job_id:
            job = self.get(job_id)
            return job if job.get("status") in NONTERMINAL_JOB_STATES else job
        jobs = self.repository.list(limit=0)
        self.recover()
        jobs = self.repository.list(limit=0)
        running = next((job for job in jobs if job.get("status") in NONTERMINAL_JOB_STATES), None)
        return running or (jobs[0] if jobs else None)

    def start(self, document: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.recover()
            job = dict(document)
            job_id = str(job.get("job_id") or "")
            if job.get("status") != "queued" or not job_id:
                raise ValueError("A new durable job must have a job_id and queued status")
            job.setdefault("schema_version", 1)
            job.setdefault("artifact_type", "experiment_job")
            job.setdefault("cancel_requested", False)
            job.setdefault("pause_requested", False)
            job.setdefault("created_at", utc_now())
            job["updated_at"] = utc_now()
            job["start_fingerprint"] = self._start_fingerprint(job)
            try:
                existing = self.repository.read(job_id)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                if existing.get("start_fingerprint") == job["start_fingerprint"]:
                    return existing
                raise ValueError("DUPLICATE_JOB_ID: job_id already identifies another start")

            legacy_active = [
                item for item in self.repository.list(limit=0)
                if item.get("status") in NONTERMINAL_JOB_STATES
                and not isinstance(item.get("resource_reservation"), Mapping)
            ]
            if legacy_active:
                raise ValueError(
                    "LEGACY_ACTIVE_JOB_EXCLUSIVE: an active legacy job has no durable reservation"
                )

            admission_limit = int(job.get("max_parallel_jobs") or 1)
            if admission_limit not in {1, 2}:
                raise ValueError("max_parallel_jobs must be 1 or 2")
            if admission_limit == 2 and job.get("worker_ownership_capable") is not True:
                raise ValueError(
                    "WORKER_OWNERSHIP_UPGRADE_REQUIRED: parallel jobs require ownership-capable Workers"
                )
            config = job.get("config") if isinstance(job.get("config"), Mapping) else {}
            formal = config.get("experiment_type") == "formal" or bool(job.get("campaign_id"))
            attempt_id = str(
                job.get("campaign_attempt_id")
                or job.get("sweep_attempt_id")
                or config.get("campaign_attempt_id")
                or (
                    config.get("sweep", {}).get("attempt_id")
                    if isinstance(config.get("sweep"), Mapping)
                    else ""
                )
                or job_id
            )
            coordinator_id = (
                str(config.get("rpc_coordinator_node") or "")
                if job.get("strategy") == "model_parallel_rpc"
                else ""
            )
            lease = self.resources.acquire(
                owner_job_id=job_id,
                owner_attempt_id=attempt_id,
                workers=self._worker_resources(job),
                hold_reason="formal_campaign" if formal else "experiment_job",
                admission_limit=admission_limit,
                exclusive=formal,
                rpc_coordinator_id=coordinator_id,
                evidence={"suite_id": job.get("suite_id"), "strategy": job.get("strategy")},
            )
            acquired_new = bool(lease.pop("_acquired_new", False))
            if not acquired_new:
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        existing = self.repository.read(job_id)
                    except FileNotFoundError:
                        time.sleep(0.02)
                        continue
                    if existing.get("start_fingerprint") == job["start_fingerprint"]:
                        return existing
                    break
                raise ValueError("START_IN_PROGRESS: duplicate start is being committed")
            job["max_parallel_jobs"] = admission_limit
            job["resource_reservation"] = lease
            spec = self._spec(job_id)
            job["command"] = list(spec.argv)
            job["log_path"] = str(spec.log_path)
            try:
                self.repository.write(job_id, job)
            except Exception:
                self.resources.finish(
                    lease_id=str(lease["lease_id"]), owner_job_id=job_id,
                    fencing_epoch=int(lease["fencing_epoch"]), cleanup_verified=True,
                    evidence={"reason": "JOB_DOCUMENT_WRITE_FAILED_BEFORE_SPAWN"},
                )
                raise

            try:
                descriptor = os.open(spec.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "ab", buffering=0) as log_handle:
                    child = subprocess.Popen(
                        spec.argv,
                        cwd=self.project_root,
                        env={
                            **os.environ,
                            "PYTHONDONTWRITEBYTECODE": "1",
                            "CLUSTER_RESOURCE_OWNER_ID": job_id,
                            "CLUSTER_RESOURCE_LEASE_ID": str(lease["lease_id"]),
                            "CLUSTER_RESOURCE_FENCING_EPOCH": str(lease["fencing_epoch"]),
                        },
                        stdin=subprocess.DEVNULL,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        close_fds=True,
                    )
                    self._children[child.pid] = child
            except Exception as exc:
                self.resources.finish(
                    lease_id=str(lease["lease_id"]), owner_job_id=job_id,
                    fencing_epoch=int(lease["fencing_epoch"]), cleanup_verified=True,
                    evidence={"reason": "SPAWN_FAILED_BEFORE_CHILD", "error_type": type(exc).__name__},
                )
                self.repository.update(
                    job_id,
                    lambda value: value.update({
                        "status": "failed", "phase": "finished", "finished_at": utc_now(),
                        "updated_at": utc_now(), "error": "Durable job process did not start",
                    }),
                )
                raise

            try:
                observed = self.inspector.inspect(child.pid)
            except RuntimeError:
                observed = None
            if observed is not None and observed.argv == spec.argv:
                self.repository.update(
                    job_id,
                    lambda value: value.update(
                        {"spawned_pid": child.pid, "process": observed.to_dict(), "updated_at": utc_now()}
                    ),
                )
            else:
                self.repository.update(
                    job_id,
                    lambda value: value.update({"spawned_pid": child.pid, "updated_at": utc_now()}),
                )
            return self.repository.read(job_id)

    def _reap_children(self) -> None:
        for pid, child in list(self._children.items()):
            if child.poll() is None:
                continue
            child.wait(timeout=0)
            self._children.pop(pid, None)

    def _target_active(self, job_id: Optional[str]) -> Dict[str, Any]:
        candidates = self.active_jobs()
        if job_id:
            target = next((job for job in candidates if job.get("job_id") == job_id), None)
            if target is None:
                raise ValueError(f"No running experiment with job_id {job_id}")
            return target
        if not candidates:
            raise ValueError("No running experiment")
        if len(candidates) > 1:
            raise ValueError("AMBIGUOUS_ACTIVE_JOB: job_id is required when multiple jobs are active")
        return candidates[0]

    def cancel(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            target = self._target_active(job_id)
            selected_id = str(target["job_id"])

            def request_cancel(value: Dict[str, Any]) -> None:
                value.update(
                    {
                        "cancel_requested": True,
                        "phase": "cancelling",
                        "cancel_requested_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )

            updated = self.repository.update(selected_id, request_cancel)
            self._schedule_cancel_fallback(selected_id)
            return updated

    def pause(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            target = self._target_active(job_id)
            return self.repository.update(
                str(target["job_id"]),
                lambda value: value.update(
                    {"pause_requested": True, "phase": "pausing", "updated_at": utc_now()}
                ),
            )

    def resume(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            target = self._target_active(job_id)
            return self.repository.update(
                str(target["job_id"]),
                lambda value: value.update(
                    {"pause_requested": False, "phase": "suite", "updated_at": utc_now()}
                ),
            )

    def _schedule_cancel_fallback(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._cancel_fallbacks:
                return
            self._cancel_fallbacks.add(job_id)
        threading.Thread(
            target=self._cancel_fallback,
            args=(job_id,),
            name=f"cluster-job-cancel-{job_id}",
            daemon=True,
        ).start()

    def _cancel_fallback(self, job_id: str) -> None:
        try:
            deadline = time.monotonic() + self.cancel_grace_s
            while time.monotonic() < deadline:
                try:
                    job = self.repository.read(job_id)
                except (FileNotFoundError, OSError, StorageCorruptionError):
                    return
                if job.get("status") in TERMINAL_JOB_STATES:
                    return
                time.sleep(self.poll_interval_s)
            job = self.repository.read(job_id)
            if not self._lease_matches(job):
                return
            expected = self._expected_process(job)
            if expected is None or not self.inspector.signal(expected, TERMINATE_SIGNAL):
                return
            deadline = time.monotonic() + self.terminate_grace_s
            while time.monotonic() < deadline:
                if self.inspector.inspect(expected.pid) is None:
                    return
                time.sleep(self.poll_interval_s)
            self.inspector.signal(expected, KILL_SIGNAL)
        finally:
            with self._lock:
                self._cancel_fallbacks.discard(job_id)

    def _watch(self) -> None:
        while not self._watch_stop.wait(self.poll_interval_s):
            try:
                self.recover()
                changed_candidates = self.repository.list(limit=0)
            except Exception:
                continue
            for job in changed_candidates:
                latest = job.get("latest_event") or {}
                job_id = str(job.get("job_id") or "")
                signature = (str(job.get("updated_at") or ""), str(latest.get("at") or ""))
                if signature == self._last_change.get(job_id):
                    continue
                self._last_change[job_id] = signature
                if self.on_change:
                    self.on_change(job)


__all__ = [
    "JOB_STATES",
    "NONTERMINAL_JOB_STATES",
    "TERMINAL_JOB_STATES",
    "JobProcessSpec",
    "JobService",
]
