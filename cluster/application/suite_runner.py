"""One-run and multi-model suite lifecycle services.

The Dashboard may start these services in a separate process.  They contain no
FastAPI state and persist every suite transition before exposing it to callers.
"""

from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.storage import FilesystemSuiteRepository, SuiteRepository


EventCallback = Callable[[Dict[str, Any]], None]
ProgressCallback = Callable[[Dict[str, Any]], None]
BenchmarkCallable = Callable[..., Dict[str, Any]]
UnloadCallable = Callable[[Sequence[str]], List[str]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def suite_model_records(
    model_ids: Sequence[str],
    summaries: Sequence[Dict[str, Any]],
    errors: Sequence[Dict[str, Any]],
    attempted_models: int,
    suite_status: str,
    cleanup_statuses: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
    summaries_by_index = {
        int(summary.get("model_index", 0)): summary
        for summary in summaries
        if str(summary.get("model_index", "")).isdigit()
    }
    terminal = suite_status in {"completed", "partial", "failed", "cancelled"}
    records: List[Dict[str, Any]] = []
    for index, model_id in enumerate(model_ids, start=1):
        summary = summaries_by_index.get(index)
        model_errors = [
            error
            for error in errors
            if error.get("model_index") == index or error.get("model_id") == model_id
        ]
        cleanup_errors = [error for error in model_errors if error.get("stage") == "unload"]
        attempted = index <= attempted_models or summary is not None
        if summary is not None:
            status = summary.get("status", "failed")
        elif attempted:
            status = "failed" if terminal else "running"
        else:
            status = "unrun"
        cleanup_status = (cleanup_statuses or {}).get(index)
        if cleanup_errors:
            cleanup_status = "failed"
        elif not cleanup_status:
            cleanup_status = (
                "completed"
                if summary is not None
                else "pending"
                if attempted and not terminal
                else "unrun"
            )
        records.append(
            {
                "model_id": model_id,
                "model_index": index,
                "attempted": attempted,
                "status": status,
                "run_id": summary.get("run_id") if summary else None,
                "cleanup_status": cleanup_status,
                "errors": model_errors,
            }
        )
    return records


def suite_document(
    *,
    suite_id: str,
    experiment_id: str,
    name: str,
    status: str,
    model_ids: Sequence[str],
    attempted_models: int,
    completed_models: int,
    total_work_units: int,
    completed_work_units: int,
    continue_on_model_error: bool,
    model_cooldown_s: float,
    started_at: str,
    summaries: Sequence[Dict[str, Any]],
    errors: Sequence[Dict[str, Any]],
    finished_at: Optional[str] = None,
    cleanup_statuses: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    document = {
        "schema_version": 1,
        "artifact_type": "experiment_suite",
        "suite_id": suite_id,
        "experiment_id": experiment_id,
        "name": name,
        "status": status,
        "model_ids": list(model_ids),
        "model_count": len(model_ids),
        "attempted_models": attempted_models,
        "completed_models": completed_models,
        "total_work_units": total_work_units,
        "completed_work_units": completed_work_units,
        "continue_on_model_error": continue_on_model_error,
        "model_cooldown_s": model_cooldown_s,
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": utc_now(),
        "summaries": list(summaries),
        "errors": list(errors),
    }
    document["models"] = suite_model_records(
        model_ids, summaries, errors, attempted_models, status, cleanup_statuses
    )
    return document


class ExperimentRunner:
    """Execute exactly one model/run through the benchmark facade."""

    def __init__(
        self,
        benchmark: BenchmarkCallable,
        inventory_path: Path,
        results_root: Path,
    ) -> None:
        self._benchmark = benchmark
        self._inventory_path = Path(inventory_path)
        self._results_root = Path(results_root)

    def run(
        self,
        config: ExperimentConfig,
        cancel_event: threading.Event,
        progress: EventCallback,
    ) -> Dict[str, Any]:
        return self._benchmark(
            config,
            inventory_path=self._inventory_path,
            results_root=self._results_root,
            progress=progress,
            cancel_event=cancel_event,
        )


class SuiteRunner:
    """Coordinate ordered independent model runs and durable suite cleanup."""

    def __init__(
        self,
        experiment_runner: ExperimentRunner,
        suite_repository: SuiteRepository,
        unload_models: UnloadCallable,
        emit: Optional[EventCallback] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> None:
        self._experiment_runner = experiment_runner
        self._suite_repository = suite_repository
        self._unload_models = unload_models
        self._emit_callback = emit
        self._progress_callback = progress

    def _emit(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        event = {"type": event_type, "at": utc_now(), **payload}
        if self._emit_callback:
            self._emit_callback(event)
        return event

    def _progress(self, **fields: Any) -> None:
        if self._progress_callback:
            self._progress_callback(fields)

    def run(
        self,
        base_config: ExperimentConfig,
        model_ids: Sequence[str],
        suite_id: str,
        continue_on_model_error: bool,
        model_cooldown_s: float,
        cancel_event: threading.Event,
        total_work_units: int,
        per_model_work_units: int,
        started_at: str,
    ) -> Dict[str, Any]:
        model_ids = list(model_ids)
        summaries: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        cleanup_statuses: Dict[int, str] = {}
        attempted_models = 0
        completed_models = 0
        completed_work_units = 0

        def persist(status: str, finished_at: Optional[str] = None) -> Dict[str, Any]:
            summary = suite_document(
                suite_id=suite_id,
                experiment_id=base_config.experiment_id,
                name=base_config.name,
                status=status,
                model_ids=model_ids,
                attempted_models=attempted_models,
                completed_models=completed_models,
                total_work_units=total_work_units,
                completed_work_units=completed_work_units,
                continue_on_model_error=continue_on_model_error,
                model_cooldown_s=model_cooldown_s,
                started_at=started_at,
                finished_at=finished_at,
                summaries=summaries,
                errors=errors,
                cleanup_statuses=cleanup_statuses,
            )
            self._suite_repository.write(suite_id, summary)
            return summary

        try:
            persist("running")
            self._progress(status="running", phase="suite", started_at=started_at)
            self._emit(
                "suite_started",
                suite_id=suite_id,
                experiment_id=base_config.experiment_id,
                model_ids=model_ids,
                model_count=len(model_ids),
                total_work_units=total_work_units,
            )

            for index, model_id in enumerate(model_ids, start=1):
                if cancel_event.is_set():
                    break
                attempted_models += 1
                config = ExperimentConfig.from_dict(
                    {
                        **asdict(base_config),
                        "model_id": model_id,
                        "suite_id": suite_id,
                        "model_index": index,
                        "model_count": len(model_ids),
                    }
                )
                config.validate()
                local_completed = 0
                captured_summary: Optional[Dict[str, Any]] = None
                model_failed = False
                self._progress(
                    phase="model_starting",
                    current_model=model_id,
                    model_index=index,
                    model_completed=0,
                    current_run_id="",
                    current_summary=None,
                    error="",
                )
                self._emit(
                    "model_started",
                    suite_id=suite_id,
                    experiment_id=config.experiment_id,
                    model_id=model_id,
                    model_index=index,
                    model_count=len(model_ids),
                    total_work_units=per_model_work_units,
                )
                persist("running")

                def handle_model_event(event: Dict[str, Any]) -> None:
                    nonlocal local_completed, captured_summary
                    if event.get("type") == "request_completed":
                        local_completed = max(local_completed, int(event.get("completed", 0)))
                        self._progress(
                            status="running",
                            model_completed=local_completed,
                            completed=completed_work_units + local_completed,
                            latest=event.get("result"),
                        )
                    if event.get("run_id"):
                        self._progress(current_run_id=event["run_id"])
                    if event.get("type") == "phase":
                        self._progress(phase=event.get("phase"))
                    if event.get("type") in {"run_finished", "run_failed"} and event.get("summary"):
                        captured_summary = event["summary"]
                        self._progress(current_summary=captured_summary)
                    if self._emit_callback:
                        self._emit_callback(event)

                try:
                    summary = self._experiment_runner.run(config, cancel_event, handle_model_event)
                    captured_summary = summary
                    summaries.append(summary)
                    if summary.get("status") == "completed":
                        completed_models += 1
                    self._emit(
                        "model_finished",
                        suite_id=suite_id,
                        experiment_id=config.experiment_id,
                        model_id=model_id,
                        model_index=index,
                        model_count=len(model_ids),
                        status=summary.get("status", "completed"),
                        summary=summary,
                    )
                except Exception as exc:
                    model_failed = True
                    failure_summary = captured_summary or {
                        "suite_id": suite_id,
                        "experiment_id": config.experiment_id,
                        "model_id": model_id,
                        "model_index": index,
                        "model_count": len(model_ids),
                        "status": "failed",
                        "error": str(exc),
                    }
                    summaries.append(failure_summary)
                    if cancel_event.is_set():
                        self._emit(
                            "model_finished",
                            suite_id=suite_id,
                            experiment_id=config.experiment_id,
                            model_id=model_id,
                            model_index=index,
                            model_count=len(model_ids),
                            status="cancelled",
                            summary=failure_summary,
                        )
                    else:
                        error = {
                            "model_id": model_id,
                            "model_index": index,
                            "stage": "benchmark",
                            "error": str(exc),
                        }
                        errors.append(error)
                        self._emit(
                            "model_failed",
                            suite_id=suite_id,
                            experiment_id=config.experiment_id,
                            model_id=model_id,
                            model_index=index,
                            model_count=len(model_ids),
                            error=str(exc),
                            summary=failure_summary,
                        )
                finally:
                    completed_work_units += local_completed
                    self._progress(
                        completed=completed_work_units,
                        completed_models=completed_models,
                        summaries=list(summaries),
                        errors=list(errors),
                    )

                cleanup_statuses[index] = "pending"
                persist("cancelling" if cancel_event.is_set() else "running")
                self._progress(phase="model_cleanup")
                try:
                    cleanup_errors = self._unload_models(config.node_names)
                except Exception as exc:
                    cleanup_errors = [str(exc)]
                cleanup_failed = bool(cleanup_errors)
                if cleanup_errors:
                    cleanup_statuses[index] = "failed"
                    cleanup_error = {
                        "model_id": model_id,
                        "model_index": index,
                        "stage": "unload",
                        "error": "; ".join(cleanup_errors),
                    }
                    errors.append(cleanup_error)
                    self._emit(
                        "model_failed",
                        suite_id=suite_id,
                        experiment_id=config.experiment_id,
                        model_id=model_id,
                        model_index=index,
                        model_count=len(model_ids),
                        stage="unload",
                        error=cleanup_error["error"],
                    )
                    self._progress(errors=list(errors))
                else:
                    cleanup_statuses[index] = "completed"
                persist("cancelling" if cancel_event.is_set() else "running")

                should_continue = (
                    index < len(model_ids)
                    and not cancel_event.is_set()
                    and not cleanup_failed
                    and (not model_failed or continue_on_model_error)
                )
                if not should_continue:
                    break
                if model_cooldown_s > 0:
                    self._progress(phase="model_cooldown")
                    self._emit(
                        "model_cooldown",
                        suite_id=suite_id,
                        after_model_id=model_id,
                        seconds=model_cooldown_s,
                    )
                    if cancel_event.wait(model_cooldown_s):
                        break

            if cancel_event.is_set():
                final_status = "cancelled"
            elif errors:
                final_status = "partial" if completed_models else "failed"
            elif attempted_models == len(model_ids):
                final_status = "completed"
            else:
                final_status = "failed"
            summary = persist(final_status, utc_now())
            self._progress(
                status=final_status,
                phase="finished",
                summary=summary,
                summaries=list(summaries),
                errors=list(errors),
                completed_models=completed_models,
                finished_at=summary["finished_at"],
                error=errors[-1]["error"] if errors else "",
            )
            self._emit("suite_finished", **summary)
            return summary
        except Exception as exc:
            errors.append({"stage": "suite", "error": str(exc)})
            summary = persist("failed", utc_now())
            self._progress(
                status="failed",
                phase="finished",
                error=str(exc),
                errors=list(errors),
                summary=summary,
                summaries=list(summaries),
                finished_at=summary["finished_at"],
            )
            self._emit("suite_finished", **summary)
            return summary


def filesystem_suite_runner(
    experiment_runner: ExperimentRunner,
    results_dir: Path,
    unload_models: UnloadCallable,
    emit: Optional[EventCallback] = None,
    progress: Optional[ProgressCallback] = None,
) -> SuiteRunner:
    return SuiteRunner(
        experiment_runner,
        FilesystemSuiteRepository(Path(results_dir) / "_suites"),
        unload_models,
        emit,
        progress,
    )


__all__ = [
    "ExperimentRunner",
    "SuiteRunner",
    "filesystem_suite_runner",
    "suite_document",
    "suite_model_records",
]
