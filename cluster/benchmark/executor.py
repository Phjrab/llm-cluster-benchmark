"""Bounded, cancellation-aware execution of deterministic benchmark plans."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from cluster.domain.experiment import ExperimentConfig

from .models import RequestTask, StrategyScenario

StreamRequest = Callable[[Any, ExperimentConfig, RequestTask, bool], Dict[str, Any]]
StreamRpcRequest = Callable[[Any, str, ExperimentConfig, RequestTask], Dict[str, Any]]


class ScenarioExecutor:
    def __init__(self, stream_request: StreamRequest, stream_rpc_request: StreamRpcRequest) -> None:
        self._stream_request = stream_request
        self._stream_rpc_request = stream_rpc_request

    @staticmethod
    def _failure_record(task: RequestTask, node: Any, exc: Exception) -> Dict[str, Any]:
        from .transport import utc_now

        return {
            "request_id": task.request_id,
            "logical_request_id": task.logical_request_id,
            "scenario_id": task.scenario_id,
            "replica_index": task.replica_index,
            "node": node.name,
            "assigned_node": node.name,
            "node_host": node.host,
            "started_at": utc_now(),
            "ok": False,
            "ttft_s": None,
            "e2e_s": 0.0,
            "server_ttft_s": None,
            "server_generation_s": None,
            "generated_tokens": 0,
            "tokens_per_s": None,
            "output_chars": 0,
            "output_sha256": "",
            "error": str(exc),
            "warmup": False,
        }

    def execute(
        self,
        scenario: StrategyScenario,
        nodes_by_name: Dict[str, Any],
        config: ExperimentConfig,
        emit: Callable[..., None],
        cancel_event: threading.Event,
        completed_offset: int,
        total_work_units: int,
        rpc_coordinator: Optional[Any] = None,
        rpc_url: str = "",
    ) -> tuple[List[Dict[str, Any]], float]:
        records: List[Dict[str, Any]] = []
        started = time.perf_counter()
        if scenario.concurrency_scope == "logical_group":
            physical_concurrency = min(
                len(scenario.tasks), config.concurrency * len(scenario.node_names)
            )
        else:
            physical_concurrency = min(len(scenario.tasks), config.concurrency)
        max_workers = max(1, physical_concurrency)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: Dict[
                concurrent.futures.Future[Dict[str, Any]], tuple[RequestTask, Any, int]
            ] = {}
            if scenario.concurrency_scope == "logical_group":
                by_logical_id: Dict[int, List[RequestTask]] = {}
                for task in scenario.tasks:
                    by_logical_id.setdefault(task.logical_request_id, []).append(task)
                batches = list(by_logical_id.values())
                batch_slots = min(config.concurrency, len(batches))
            else:
                batches = [[task] for task in scenario.tasks]
                batch_slots = min(max_workers, len(batches))
            batch_iterator = iter(enumerate(batches))
            pending_by_batch: Dict[int, int] = {}

            def submit_batch(batch_id: int, tasks: Sequence[RequestTask]) -> None:
                pending_by_batch[batch_id] = len(tasks)
                for task in tasks:
                    target = nodes_by_name[task.target_node]
                    if rpc_coordinator is not None:
                        future = pool.submit(
                            self._stream_rpc_request, rpc_coordinator, rpc_url, config, task
                        )
                    else:
                        future = pool.submit(self._stream_request, target, config, task, False)
                    futures[future] = (task, target, batch_id)

            for _ in range(batch_slots):
                if cancel_event.is_set():
                    break
                try:
                    submit_batch(*next(batch_iterator))
                except StopIteration:
                    break

            while futures:
                done, _ = concurrent.futures.wait(
                    futures, return_when=concurrent.futures.FIRST_COMPLETED
                )
                completed_batches: List[int] = []
                for future in done:
                    task, target, batch_id = futures.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = self._failure_record(task, target, exc)
                    records.append(result)
                    emit(
                        "request_completed",
                        completed=completed_offset + len(records),
                        total=total_work_units,
                        result=result,
                    )
                    pending_by_batch[batch_id] -= 1
                    if pending_by_batch[batch_id] == 0:
                        del pending_by_batch[batch_id]
                        completed_batches.append(batch_id)
                if not cancel_event.is_set():
                    for _ in completed_batches:
                        try:
                            submit_batch(*next(batch_iterator))
                        except StopIteration:
                            break
        return records, time.perf_counter() - started

    def warmup(
        self,
        nodes: Sequence[Any],
        config: ExperimentConfig,
        cancel_event: threading.Event,
        rpc_coordinator: Optional[Any] = None,
        rpc_url: str = "",
    ) -> None:
        if not config.warmup_requests:
            return
        if rpc_coordinator is not None:
            for warmup_index in range(config.warmup_requests):
                if cancel_event.is_set():
                    break
                task = RequestTask(
                    -(warmup_index + 1), warmup_index + 1, "warmup", rpc_coordinator.name
                )
                result = self._stream_rpc_request(rpc_coordinator, rpc_url, config, task)
                if not result["ok"]:
                    raise RuntimeError(f"RPC warmup failed: {result['error']}")
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as pool:
            jobs: Dict[
                concurrent.futures.Future[Dict[str, Any]], tuple[int, Any, int]
            ] = {}

            def submit(node_index: int, node: Any, warmup_index: int) -> None:
                task = RequestTask(
                    -(node_index * config.warmup_requests + warmup_index + 1),
                    warmup_index + 1,
                    "warmup",
                    node.name,
                    node_index,
                )
                future = pool.submit(self._stream_request, node, config, task, True)
                jobs[future] = (node_index, node, warmup_index)

            for node_index, node in enumerate(nodes):
                if cancel_event.is_set():
                    break
                submit(node_index, node, 0)

            while jobs:
                done, _ = concurrent.futures.wait(
                    jobs, return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    node_index, node, warmup_index = jobs.pop(future)
                    result = future.result()
                    if not result["ok"]:
                        raise RuntimeError(
                            f"Warmup failed on {result['node']}: {result['error']}"
                        )
                    next_index = warmup_index + 1
                    if not cancel_event.is_set() and next_index < config.warmup_requests:
                        submit(node_index, node, next_index)


__all__ = ["ScenarioExecutor"]
