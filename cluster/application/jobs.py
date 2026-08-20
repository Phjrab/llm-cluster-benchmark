"""Durable child-process experiment job registry and recovery service."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
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


NONTERMINAL_JOB_STATES = frozenset({"queued", "running"})
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled", "orphaned"})
JOB_STATES = NONTERMINAL_JOB_STATES | TERMINAL_JOB_STATES


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
        start_watcher: bool = True,
    ) -> None:
        self.jobs_dir = Path(jobs_dir)
        self.inventory_path = Path(inventory_path)
        self.results_dir = Path(results_dir)
        self.project_root = Path(project_root)
        self.python_bin = Path(python_bin or sys.executable)
        self.repository = FilesystemJobRepository(self.jobs_dir)
        self.inspector = inspector or PsutilProcessInspector()
        self.on_change = on_change
        self.cancel_grace_s = cancel_grace_s
        self.terminate_grace_s = terminate_grace_s
        self.poll_interval_s = poll_interval_s
        self._lock = threading.RLock()
        self._watch_stop = threading.Event()
        self._last_change: tuple[str, str, str] = ("", "", "")
        self._children: dict[int, subprocess.Popen[bytes]] = {}
        self._cancel_fallbacks: set[str] = set()
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
            threading.Thread(
                target=self._watch,
                name="cluster-job-registry-watch",
                daemon=True,
            ).start()

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
        return (
            identity.argv == spec.argv
            and Path(identity.cwd) == self.project_root
            and Path(identity.executable).resolve() == self.python_bin.resolve()
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

    def recover(self) -> list[Dict[str, Any]]:
        """Reconcile registry state with result artifacts and exact processes."""
        self._reap_children()
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
                    updated = self.repository.update(job_id, lambda value: value.update(terminal))
                    recovered.append(updated)
                    continue
                if self._live_identity(job) is not None:
                    recovered.append(job)
                    continue
                error = {
                    "stage": "job_recovery",
                    "error": "Job process identity is missing, stale, or no longer running",
                }

                def orphan(value: Dict[str, Any]) -> None:
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

                recovered.append(self.repository.update(job_id, orphan))
        return recovered

    def list(self, limit: int = 100) -> list[Dict[str, Any]]:
        self._reap_children()
        return self.repository.list(limit=limit)

    def active(self) -> Optional[Dict[str, Any]]:
        self.recover()
        jobs = self.repository.list(limit=0)
        running = next(
            (job for job in jobs if job.get("status") in NONTERMINAL_JOB_STATES),
            None,
        )
        return running or (jobs[0] if jobs else None)

    def start(self, document: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self.recover()
            running = [
                job for job in self.repository.list(limit=0)
                if job.get("status") in NONTERMINAL_JOB_STATES
            ]
            if running:
                raise ValueError("Another experiment is already running")
            job = dict(document)
            job_id = str(job.get("job_id") or "")
            if job.get("status") != "queued" or not job_id:
                raise ValueError("A new durable job must have a job_id and queued status")
            job.setdefault("schema_version", 1)
            job.setdefault("artifact_type", "experiment_job")
            job.setdefault("cancel_requested", False)
            job.setdefault("created_at", utc_now())
            job["updated_at"] = utc_now()
            spec = self._spec(job_id)
            job["command"] = list(spec.argv)
            job["log_path"] = str(spec.log_path)
            self.repository.write(job_id, job)

            descriptor = os.open(spec.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "ab", buffering=0) as log_handle:
                child = subprocess.Popen(
                    spec.argv,
                    cwd=self.project_root,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
                self._children[child.pid] = child

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

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            self.recover()
            candidates = [
                job for job in self.repository.list(limit=0)
                if job.get("status") in NONTERMINAL_JOB_STATES
            ]
            if not candidates:
                raise ValueError("No running experiment")
            job_id = str(candidates[0]["job_id"])

            def request_cancel(value: Dict[str, Any]) -> None:
                value.update(
                    {
                        "cancel_requested": True,
                        "phase": "cancelling",
                        "cancel_requested_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )

            updated = self.repository.update(job_id, request_cancel)
            self._schedule_cancel_fallback(job_id)
            return updated

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
                active = self.active()
            except Exception:
                continue
            if not active:
                continue
            latest = active.get("latest_event") or {}
            signature = (
                str(active.get("job_id") or ""),
                str(active.get("updated_at") or ""),
                str(latest.get("at") or ""),
            )
            if signature == self._last_change:
                continue
            self._last_change = signature
            if self.on_change:
                self.on_change(active)


__all__ = [
    "JOB_STATES",
    "NONTERMINAL_JOB_STATES",
    "TERMINAL_JOB_STATES",
    "JobProcessSpec",
    "JobService",
]
