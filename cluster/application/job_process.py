#!/usr/bin/env python3
"""Child process entry point for one durable multi-model experiment job."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import signal
import threading
from pathlib import Path
from typing import Any, Dict, List, Sequence

from cluster.application.suite_runner import ExperimentRunner, filesystem_suite_runner, utc_now
from cluster.benchmark.runner import ExperimentConfig, run_experiment
from cluster.clusterctl import Node, load_nodes, request_json, select_nodes
from cluster.infrastructure.process import PsutilProcessInspector
from cluster.infrastructure.storage import FilesystemJobRepository


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


def run_job(
    job_id: str,
    jobs_dir: Path,
    inventory_path: Path,
    results_dir: Path,
) -> int:
    repository = FilesystemJobRepository(jobs_dir)
    job = repository.read(job_id)
    cancel_event = threading.Event()
    if job.get("cancel_requested") is True:
        cancel_event.set()
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

    try:
        base_config = ExperimentConfig.from_dict(dict(job["config"]))
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
        )
        suite_status = str(summary.get("status") or "failed")
        job_status = (
            "completed"
            if suite_status == "completed"
            else "cancelled"
            if suite_status == "cancelled"
            else "failed"
        )

        def finish(value: Dict[str, Any]) -> None:
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

        repository.update(job_id, finish)
        return 0 if job_status in {"completed", "cancelled"} else 1
    except Exception as exc:
        def fail(value: Dict[str, Any]) -> None:
            error = {"stage": "job_process", "error": str(exc)}
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
