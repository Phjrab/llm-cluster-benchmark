#!/usr/bin/env python3
"""Child process entry point for one durable multi-model experiment job."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence

from cluster.application.suite_runner import ExperimentRunner, filesystem_suite_runner, utc_now
from cluster.application.jobs import scrub_terminal_prompt
from cluster.benchmark.runner import ExperimentConfig, run_experiment
from cluster.clusterctl import Node, load_nodes, request_json, select_nodes
from cluster.infrastructure.process import PsutilProcessInspector
from cluster.infrastructure.storage import FilesystemJobRepository
from cluster.application.resources import FilesystemResourceCoordinator, ResourceConflictError


def unload_models(node_names: Sequence[str], inventory_path: Path) -> List[str]:
    nodes = select_nodes(
        load_nodes(inventory_path, require_legacy_head=False), node_names
    )
    errors: List[str] = []

    def unload(node: Node) -> None:
        request_json(
            f"{node.api_url}/api/unload-model",
            method="POST",
            payload={},
            timeout=60.0,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(nodes))) as executor:
        futures = {executor.submit(unload, node): node for node in nodes}
        for future in concurrent.futures.as_completed(futures):
            node = futures[future]
            try:
                future.result()
            except Exception as exc:
                errors.append(f"{node.name}: {exc}")
    missing = sorted(set(node_names) - {node.name for node in nodes})
    errors.extend(f"{name}: unavailable" for name in missing)
    return errors


def claim_worker_ownership(
    node_names: Sequence[str], inventory_path: Path, reservation: Dict[str, Any]
) -> List[Node]:
    nodes = select_nodes(load_nodes(inventory_path, require_legacy_head=False), node_names)
    if len(nodes) != len(node_names):
        raise RuntimeError("RESOURCE_OWNERSHIP_WORKER_UNAVAILABLE")
    payload = {
        "owner_id": str(reservation["owner_job_id"]),
        "lease_id": str(reservation["lease_id"]),
        "fencing_epoch": int(reservation["fencing_epoch"]),
        "cleanup_verified": False,
    }
    claimed: List[Node] = []
    try:
        for node in nodes:
            result = request_json(
                f"{node.api_url}/cluster/resource-ownership/acquire",
                method="POST", payload=payload, timeout=10.0,
            )
            if result.get("ok") is not True:
                raise RuntimeError("RESOURCE_OWNERSHIP_REJECTED")
            claimed.append(node)
    except Exception:
        for node in claimed:
            try:
                request_json(
                    f"{node.api_url}/cluster/resource-ownership/release",
                    method="POST", payload={**payload, "cleanup_verified": True}, timeout=10.0,
                )
            except Exception:
                pass
        raise
    return claimed


def release_worker_ownership(
    nodes: Sequence[Node], reservation: Dict[str, Any], *, cleanup_verified: bool
) -> List[str]:
    payload = {
        "owner_id": str(reservation["owner_job_id"]),
        "lease_id": str(reservation["lease_id"]),
        "fencing_epoch": int(reservation["fencing_epoch"]),
        "cleanup_verified": cleanup_verified,
    }
    errors: List[str] = []
    for node in nodes:
        try:
            result = request_json(
                f"{node.api_url}/cluster/resource-ownership/release",
                method="POST", payload=payload, timeout=10.0,
            )
            expected = "released" if cleanup_verified else "quarantined"
            if result.get("ok") is not True or result.get("status") != expected:
                raise RuntimeError("RESOURCE_OWNERSHIP_RELEASE_REJECTED")
        except Exception as exc:
            errors.append(f"{node.name}: {type(exc).__name__}")
    return errors


def run_job(
    job_id: str,
    jobs_dir: Path,
    inventory_path: Path,
    results_dir: Path,
) -> int:
    repository = FilesystemJobRepository(jobs_dir)
    resource_coordinator = FilesystemResourceCoordinator(jobs_dir / "_resources")
    job = repository.read(job_id)
    cancel_event = threading.Event()
    pause_event = threading.Event()
    if job.get("cancel_requested") is True:
        cancel_event.set()
    if job.get("pause_requested") is True:
        pause_event.set()
    monitor_stop = threading.Event()

    def request_cancel(_signum: int, _frame: Any) -> None:
        cancel_event.set()

    signal.signal(signal.SIGTERM, request_cancel)
    signal.signal(signal.SIGINT, request_cancel)

    identity = PsutilProcessInspector().inspect(os.getpid())
    if identity is None:
        raise RuntimeError("Cannot inspect durable job process identity")

    def mark_running(value: Dict[str, Any]) -> None:
        if value.get("status") not in {"queued", "running"}:
            raise RuntimeError(
                f"Job {job_id} cannot start from state {value.get('status')}"
            )
        value.update(
            {
                "status": "running",
                "phase": "suite",
                "process": identity.to_dict(),
                "started_at": value.get("started_at") or utc_now(),
                "updated_at": utc_now(),
            }
        )

    repository.update(job_id, mark_running)

    def monitor_cancel() -> None:
        while not monitor_stop.wait(0.2):
            try:
                current = repository.read(job_id)
            except (FileNotFoundError, OSError, ValueError):
                cancel_event.set()
                return
            if current.get("cancel_requested") is True:
                cancel_event.set()
                return
            if current.get("pause_requested") is True:
                pause_event.set()
            else:
                pause_event.clear()
            reservation = current.get("resource_reservation")
            if isinstance(reservation, dict):
                try:
                    resource_coordinator.heartbeat(
                        lease_id=str(reservation["lease_id"]),
                        owner_job_id=job_id,
                        fencing_epoch=int(reservation["fencing_epoch"]),
                        evidence={"pid": os.getpid(), "phase": current.get("phase")},
                    )
                except (KeyError, TypeError, ValueError, ResourceConflictError):
                    cancel_event.set()
                    return

    threading.Thread(
        target=monitor_cancel,
        name=f"cluster-job-cancel-watch-{job_id}",
        daemon=True,
    ).start()

    event_sequence = int(job.get("event_sequence") or 0)

    def emit(event: Dict[str, Any]) -> None:
        nonlocal event_sequence
        event_sequence += 1
        durable_event = {"sequence": event_sequence, **event}
        repository.append_event(job_id, durable_event)

        def apply(value: Dict[str, Any]) -> None:
            value.update(
                {
                    "latest_event": durable_event,
                    "event_sequence": event_sequence,
                    "updated_at": utc_now(),
                }
            )
            event_type = event.get("type")
            if event.get("run_id"):
                value["current_run_id"] = event["run_id"]
            if event_type == "phase":
                value["phase"] = event.get("phase")
            elif event_type == "request_completed":
                value["model_completed"] = int(event.get("completed", 0))
                value["latest"] = event.get("result")
            elif event_type in {"run_finished", "run_failed"}:
                value["current_summary"] = event.get("summary")
                if event_type == "run_failed":
                    value["error"] = event.get("error", "")

        repository.update(job_id, apply)

    def progress(fields: Dict[str, Any]) -> None:
        durable_fields = dict(fields)
        suite_status = durable_fields.get("status")
        if suite_status not in {None, "running"}:
            # The registry has no partial/cancelling state.  Keep the job
            # running until the child atomically records its terminal mapping.
            durable_fields["suite_status_preview"] = suite_status
            durable_fields.pop("status", None)

        def apply(value: Dict[str, Any]) -> None:
            value.update(durable_fields)
            value["updated_at"] = utc_now()

        repository.update(job_id, apply)

    summary: Dict[str, Any] | None = None
    resource_finished = False
    claimed_workers: List[Node] = []
    try:
        reservation = job.get("resource_reservation")
        if job.get("worker_ownership_capable") is True and isinstance(reservation, dict):
            claimed_workers = claim_worker_ownership(
                [str(item) for item in job.get("nodes") or []], inventory_path, reservation
            )
        base_config = ExperimentConfig.from_dict(dict(job["config"]), strict=True)
        base_config.validate()
        model_ids = [str(item) for item in job["model_ids"]]
        experiment_runner = ExperimentRunner(run_experiment, inventory_path, results_dir)
        suite_runner = filesystem_suite_runner(
            experiment_runner,
            results_dir,
            lambda names: unload_models(names, inventory_path),
            emit,
            progress,
        )
        summary = suite_runner.run(
            base_config=base_config,
            model_ids=model_ids,
            suite_id=str(job["suite_id"]),
            continue_on_model_error=bool(job["continue_on_model_error"]),
            model_cooldown_s=float(job["model_cooldown_s"]),
            cancel_event=cancel_event,
            total_work_units=int(job["total"]),
            per_model_work_units=int(job["model_total"]),
            started_at=str(job["started_at"]),
            pause_event=pause_event,
        )
        suite_status = str(summary.get("status") or "failed")
        job_status = (
            "completed"
            if suite_status == "completed"
            else "cancelled"
            if suite_status == "cancelled"
            else "failed"
        )

        reservation = job.get("resource_reservation")
        cleanup_verified = all(
            isinstance(model, dict) and model.get("cleanup_status") == "completed"
            for model in summary.get("models") or []
            if isinstance(model, dict) and model.get("attempted") is True
        )
        resource_cooldown_s = float(job.get("resource_cooldown_s") or 0)
        if cleanup_verified and resource_cooldown_s > 0:
            progress({"phase": "resource_cooldown"})
            # Cancellation cannot shorten the safety cooldown.  The lease stays
            # held until this boundary completes.
            time.sleep(resource_cooldown_s)
        if isinstance(reservation, dict):
            ownership_errors = release_worker_ownership(
                claimed_workers, reservation, cleanup_verified=cleanup_verified
            )
            if ownership_errors:
                cleanup_verified = False
            resource_coordinator.finish(
                lease_id=str(reservation["lease_id"]),
                owner_job_id=job_id,
                fencing_epoch=int(reservation["fencing_epoch"]),
                cleanup_verified=cleanup_verified,
                evidence={
                    "suite_id": job.get("suite_id"),
                    "suite_status": suite_status,
                    "cleanup_verified": cleanup_verified,
                    "cooldown_s": resource_cooldown_s,
                    "worker_ownership_release_errors": ownership_errors,
                },
            )
            resource_finished = True
            if not cleanup_verified:
                job_status = "failed"

        def finish(value: Dict[str, Any]) -> None:
            saved_reservation = value.get("resource_reservation")
            if isinstance(saved_reservation, dict):
                saved_reservation.update(
                    {
                        "status": "released" if cleanup_verified else "quarantined",
                        "cleanup_verified": cleanup_verified,
                    }
                )
            value.update(
                {
                    "status": job_status,
                    "phase": "finished",
                    "suite_status": suite_status,
                    "summary": summary,
                    "summaries": list(summary.get("summaries") or []),
                    "errors": list(summary.get("errors") or []),
                    "completed_models": int(summary.get("completed_models") or 0),
                    "completed": int(summary.get("completed_work_units") or 0),
                    "finished_at": summary.get("finished_at") or utc_now(),
                    "updated_at": utc_now(),
                    "error": (
                        str((summary.get("errors") or [{}])[-1].get("error") or "")
                        if summary.get("errors")
                        else ""
                    ),
                }
            )
            scrub_terminal_prompt(value)

        repository.update(job_id, finish)
        return 0 if job_status in {"completed", "cancelled"} else 1
    except Exception as exc:
        reservation = job.get("resource_reservation")
        if isinstance(reservation, dict) and not resource_finished:
            release_worker_ownership(claimed_workers, reservation, cleanup_verified=False)
            try:
                resource_coordinator.quarantine(
                    lease_id=str(reservation["lease_id"]),
                    owner_job_id=job_id,
                    fencing_epoch=int(reservation["fencing_epoch"]),
                    reason="CHILD_FAILURE_WITHOUT_CLEANUP_EVIDENCE",
                    evidence={"error_type": type(exc).__name__},
                )
            except (KeyError, TypeError, ValueError, ResourceConflictError):
                pass
        def fail(value: Dict[str, Any]) -> None:
            error = {"stage": "job_process", "error": str(exc)}
            saved_reservation = value.get("resource_reservation")
            if isinstance(saved_reservation, dict):
                saved_reservation.update(
                    {"status": "quarantined", "cleanup_verified": False}
                )
            value.update(
                {
                    "status": "cancelled" if cancel_event.is_set() else "failed",
                    "phase": "finished",
                    "finished_at": utc_now(),
                    "updated_at": utc_now(),
                    "error": str(exc),
                    "errors": [*(value.get("errors") or []), error],
                }
            )
            scrub_terminal_prompt(value)

        repository.update(job_id, fail)
        return 1
    finally:
        monitor_stop.set()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--jobs-dir", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args()
    return run_job(args.job_id, args.jobs_dir, args.inventory, args.results_dir)


if __name__ == "__main__":
    raise SystemExit(main())
