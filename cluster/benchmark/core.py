"""Readable benchmark orchestration over planning, execution, metrics, and storage."""

from __future__ import annotations

import concurrent.futures
import hashlib
import threading
import time
import uuid
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from cluster.domain.experiment import ExperimentConfig
from cluster.domain.failures import failure_from_exception
from cluster.domain.errors import ErrorCode
from cluster.domain.power import RaspberryPiPowerIntegrity, unavailable_power_integrity

from .executor import ScenarioExecutor
from .metrics import add_cumulative_scaling, aggregate_records
from .persistence import ProgressCallback, RunPersistence
from .planner import build_strategy_scenarios, validate_strategy
from .power import RunPowerIntegrityTracker
from .rpc import RpcBackend, RpcSession
from .strategies import get_strategy
from .transport import utc_now

LoadModel = Callable[[Any, ExperimentConfig], Dict[str, Any]]
ValidatePlatform = Callable[[Sequence[Any], ExperimentConfig], None]
ValidateUniform = Callable[[Sequence[Dict[str, Any]], ExperimentConfig], List[str]]
PowerSnapshot = Callable[[Any], Optional[RaspberryPiPowerIntegrity]]
DescribeNode = Callable[[Any], Dict[str, Any]]


def benchmark_parameters(config: ExperimentConfig) -> Dict[str, Any]:
    strategy = get_strategy(config.execution_strategy)
    return {
        "model_id": config.model_id,
        "n_ctx": config.n_ctx,
        "n_gpu_layers": config.n_gpu_layers,
        "requested_n_gpu_layers": config.n_gpu_layers,
        "effective_n_gpu_layers": "all" if strategy.execution_backend == "rpc" else None,
        "requests_per_scenario": config.requests,
        "concurrency": config.concurrency,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "seed": config.seed,
        "warmup_requests": config.warmup_requests,
        "require_uniform_config": config.require_uniform_config,
        "prompt_sha256": hashlib.sha256(config.prompt.encode("utf-8")).hexdigest(),
        "prompt_chars": len(config.prompt),
    }


class BenchmarkRunner:
    def __init__(
        self,
        load_model: LoadModel,
        validate_uniform: ValidateUniform,
        validate_platform: ValidatePlatform,
        executor: ScenarioExecutor,
        rpc_backend: RpcBackend,
        sample_power: Optional[PowerSnapshot] = None,
        describe_node: Optional[DescribeNode] = None,
    ) -> None:
        self.load_model = load_model
        self.validate_uniform = validate_uniform
        self.validate_platform = validate_platform
        self.executor = executor
        self.rpc_backend = rpc_backend
        self.sample_power = sample_power
        self.describe_node = describe_node

    def _capture_participants(self, nodes: Sequence[Any]) -> List[Dict[str, Any]]:
        """Capture immutable, non-secret node metadata for result provenance."""
        captured: Dict[str, Dict[str, Any]] = {}

        def base(node: Any) -> Dict[str, Any]:
            api_port = getattr(node, "api_port", None)
            host = str(getattr(node, "host", "") or "")
            return {
                "name": str(getattr(node, "name", "") or ""),
                "host": host,
                "api_port": api_port,
                "api_url": f"http://{host}:{api_port}" if host and api_port else "",
                "configured_platform": str(getattr(node, "platform", "auto") or "auto"),
                "capture_status": "inventory_only",
            }

        if self.describe_node is not None and nodes:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(nodes))) as pool:
                futures = {pool.submit(self.describe_node, node): node for node in nodes}
                for future in concurrent.futures.as_completed(futures):
                    node = futures[future]
                    record = base(node)
                    try:
                        detail = future.result()
                        if isinstance(detail, dict):
                            record.update(detail)
                        record["capture_status"] = "captured"
                    except Exception as exc:
                        record["capture_status"] = "unavailable"
                        record["capture_error"] = f"{type(exc).__name__}: {exc}"
                    captured[node.name] = record

        return [captured.get(node.name, base(node)) for node in nodes]

    def _observe_power(
        self,
        tracker: RunPowerIntegrityTracker,
        nodes: Sequence[Any],
        stage: str,
    ) -> None:
        if self.sample_power is None or not nodes:
            return
        snapshots: Dict[str, Optional[RaspberryPiPowerIntegrity]] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(nodes))
        ) as pool:
            futures = {pool.submit(self.sample_power, node): node for node in nodes}
            for future in concurrent.futures.as_completed(futures):
                node = futures[future]
                try:
                    snapshots[node.name] = future.result()
                except Exception:
                    snapshots[node.name] = (
                        unavailable_power_integrity(observed_at=utc_now())
                        if getattr(node, "platform", "") == "raspberry-pi"
                        else None
                    )
        recorder = {
            "preflight": tracker.record_preflight,
            "pre_measurement": tracker.record_pre_measurement,
            "measurement": tracker.record_measurement_sample,
            "postflight": tracker.record_postflight,
        }[stage]
        for node in nodes:
            snapshot = snapshots.get(node.name)
            if snapshot is not None:
                recorder(node.name, snapshot)

    def run(
        self,
        config: ExperimentConfig,
        nodes: Sequence[Any],
        results_root: Path,
        progress: Optional[ProgressCallback] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        config.validate()
        cancel_event = cancel_event or threading.Event()
        validate_strategy(nodes, config)
        self.validate_platform(nodes, config)
        strategy = get_strategy(config.execution_strategy)
        scenarios = (
            [] if strategy.execution_backend == "rpc"
            else build_strategy_scenarios(config, nodes)
        )
        total_work_units = strategy.work_units(config, len(nodes))

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        persistence = RunPersistence(results_root, run_id, config, progress)
        started_event = persistence.emit(
            "run_started",
            config=asdict(config),
            nodes=[node.name for node in nodes],
            strategy=config.execution_strategy,
            total_work_units=total_work_units,
        )
        participant_nodes = self._capture_participants(nodes)
        loaded: List[Dict[str, Any]] = []
        warnings: List[str] = []
        rpc_session: Optional[RpcSession] = None
        topology: Dict[str, Any] = {}
        power = RunPowerIntegrityTracker(persistence.emit)
        postflight_recorded = False
        try:
            self._observe_power(power, nodes, "preflight")
            warnings.extend(power.human_warning_messages())
            if strategy.execution_backend == "rpc":
                persistence.emit(
                    "phase", phase="rpc_preflight",
                    message="RPC 모델 분할 런타임과 노드 연결을 확인하는 중",
                )
                rpc_session = self.rpc_backend.start(nodes, config, persistence.emit)
                topology = rpc_session.topology
                effective_config = replace(
                    config, rpc_coordinator_node=rpc_session.coordinator.name
                )
                scenarios = build_strategy_scenarios(effective_config, nodes)
                loaded = [{
                    "node": node.name,
                    "loaded": True,
                    "model_id": config.model_id,
                    "placement": "sharded_participant",
                    "runtime_backend": "llama.cpp-rpc",
                    "coordinator": node.name == rpc_session.coordinator.name,
                } for node in nodes]
                warnings.append(
                    "llama.cpp RPC는 proof-of-concept이며 인증 없는 사설 LAN 전용 실험 경로입니다"
                )
            else:
                persistence.emit(
                    "phase", phase="loading_model",
                    message="선택한 노드에 전체 모델 복제본을 로드하는 중",
                )
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as pool:
                    futures = {pool.submit(self.load_model, node, config): node for node in nodes}
                    for future in concurrent.futures.as_completed(futures):
                        node = futures[future]
                        try:
                            info = future.result()
                            loaded.append(info)
                            persistence.emit("node_model_loaded", node=node.name, actual=info)
                        except Exception as exc:
                            failure = failure_from_exception(
                                exc,
                                stage="model_loading",
                                node=node.name,
                                model_id=config.model_id,
                                fallback=ErrorCode.MODEL_LOAD_FAILED,
                            )
                            persistence.emit(
                                "node_error",
                                node=node.name,
                                error=str(exc),
                                error_code=failure.code.value,
                                failure=failure.to_dict(),
                            )
                            raise RuntimeError(
                                f"Failed to load model on {node.name}: {exc}"
                            ) from exc
                uniform_warnings = self.validate_uniform(loaded, config)
                warnings.extend(uniform_warnings)
                if uniform_warnings and config.require_uniform_config:
                    raise RuntimeError(
                        "Uniform configuration check failed: "
                        + "; ".join(uniform_warnings)
                    )

            for warning in warnings:
                persistence.emit("warning", message=warning)
            if cancel_event.is_set():
                raise RuntimeError("Experiment cancelled before warmup")

            if config.warmup_requests:
                persistence.emit("phase", phase="warmup", message="측정 전 워밍업 실행 중")
                self.executor.warmup(
                    nodes,
                    config,
                    cancel_event,
                    rpc_session.coordinator if rpc_session else None,
                    rpc_session.url if rpc_session else "",
                )
            if cancel_event.is_set():
                raise RuntimeError("Experiment cancelled before measurement")

            self._observe_power(power, nodes, "pre_measurement")

            persistence.emit(
                "phase", phase="measurement",
                message="선택한 실험 전략으로 부하를 측정하는 중",
            )
            # One out-of-timing-band measurement-boundary sample provides a
            # measurement observation without changing request scheduling or
            # including health polling latency in benchmark wall time.
            self._observe_power(power, nodes, "measurement")
            records: List[Dict[str, Any]] = []
            scenario_summaries: List[Dict[str, Any]] = []
            wall_started = time.perf_counter()
            nodes_by_name = {node.name: node for node in nodes}
            for scenario in scenarios:
                if cancel_event.is_set():
                    break
                persistence.emit(
                    "scenario_started",
                    scenario_id=scenario.scenario_id,
                    label=scenario.label,
                    nodes=scenario.node_names,
                    physical_requests=len(scenario.tasks),
                )
                scenario_records, scenario_wall_s = self.executor.execute(
                    scenario,
                    nodes_by_name,
                    config,
                    persistence.emit,
                    cancel_event,
                    len(records),
                    total_work_units,
                    rpc_session.coordinator if rpc_session else None,
                    rpc_session.url if rpc_session else "",
                )
                records.extend(scenario_records)
                scenario_summary = aggregate_records(scenario_records, scenario_wall_s)
                scenario_summary.update({
                    "scenario_id": scenario.scenario_id,
                    "label": scenario.label,
                    "nodes": scenario.node_names,
                })
                scenario_summaries.append(scenario_summary)
                persistence.emit(
                    "scenario_finished", scenario_id=scenario.scenario_id,
                    summary=scenario_summary,
                )
            wall_s = time.perf_counter() - wall_started
            records.sort(key=lambda item: item["request_id"])
            summary = aggregate_records(records, wall_s)
            if strategy.cumulative_scaling and str(config.sweep_mode) == "cumulative":
                add_cumulative_scaling(scenario_summaries)

            if rpc_session is not None:
                persistence.emit(
                    "phase", phase="rpc_cleanup",
                    message="RPC 모델 분할 프로세스를 종료하는 중",
                )
                rpc_session.close()
                rpc_session = None

            self._observe_power(power, nodes, "postflight")
            postflight_recorded = True
            for warning in power.human_warning_messages():
                if warning not in warnings:
                    warnings.append(warning)

            summary.update({
                "schema_version": 2,
                "run_id": run_id,
                "suite_id": config.suite_id,
                "experiment_id": config.experiment_id,
                "name": config.name,
                "model_id": config.model_id,
                "model_index": config.model_index,
                "model_count": config.model_count,
                "execution_strategy": config.execution_strategy,
                "model_placement": strategy.result_model_placement,
                "status": "cancelled" if cancel_event.is_set() else "completed",
                "started_at": started_event["at"],
                "finished_at": utc_now(),
                "nodes": [node.name for node in nodes],
                "participant_nodes": participant_nodes,
                "actual_model_config": loaded,
                "benchmark_parameters": benchmark_parameters(config),
                "warnings": warnings,
                "scenario_summaries": scenario_summaries,
                "topology": topology,
                "result_dir": str(persistence.run_dir),
            })
            if power.has_observations:
                power_summary = power.summarize()
                summary.update({
                    "measurement_quality": power_summary["overall"]["quality"],
                    "measurement_quality_reasons": power_summary["overall"]["reason_codes"],
                    "power_integrity": power_summary,
                })
                persistence.emit(
                    "measurement_quality_finalized",
                    measurement_quality=summary["measurement_quality"],
                    power_integrity=power_summary,
                )
            persistence.complete(records, summary)
            persistence.emit("run_finished", summary=summary)
            return summary
        except Exception as exc:
            if not postflight_recorded:
                self._observe_power(power, nodes, "postflight")
                postflight_recorded = True
            for warning in power.human_warning_messages():
                if warning not in warnings:
                    warnings.append(warning)
            cancelled = cancel_event.is_set()
            structured_failure = failure_from_exception(
                exc,
                stage="run",
                model_id=config.model_id,
            )
            failure = {
                "schema_version": 2,
                "run_id": run_id,
                "suite_id": config.suite_id,
                "experiment_id": config.experiment_id,
                "name": config.name,
                "model_id": config.model_id,
                "model_index": config.model_index,
                "model_count": config.model_count,
                "execution_strategy": config.execution_strategy,
                "model_placement": strategy.result_model_placement,
                "status": "cancelled" if cancelled else "failed",
                "finished_at": utc_now(),
                "nodes": [node.name for node in nodes],
                "participant_nodes": participant_nodes,
                "actual_model_config": loaded,
                "benchmark_parameters": benchmark_parameters(config),
                "topology": topology,
                "warnings": warnings,
                "error": str(exc),
                "error_code": structured_failure.code.value,
                "failure": structured_failure.to_dict(),
                "failures": [structured_failure.to_dict()],
                "result_dir": str(persistence.run_dir),
            }
            if power.has_observations:
                power_summary = power.summarize()
                failure.update({
                    "measurement_quality": power_summary["overall"]["quality"],
                    "measurement_quality_reasons": power_summary["overall"]["reason_codes"],
                    "power_integrity": power_summary,
                })
                last_power = {
                    node: values.get("postflight") or values.get("pre_measurement")
                    or values.get("preflight")
                    for node, values in power_summary["nodes"].items()
                }
                failure_record = dict(failure["failure"])
                failure_record["evidence"] = {
                    **dict(failure_record.get("evidence") or {}),
                    "last_power_integrity": last_power,
                }
                failure["failure"] = failure_record
                failure["failures"] = [failure_record]
                persistence.emit(
                    "measurement_quality_finalized",
                    measurement_quality=failure["measurement_quality"],
                    power_integrity=power_summary,
                )
            persistence.write_summary(failure)
            if cancelled:
                persistence.emit("run_finished", summary=failure)
                return failure
            persistence.emit(
                "run_failed",
                error=str(exc),
                error_code=structured_failure.code.value,
                failure=structured_failure.to_dict(),
                summary=failure,
            )
            raise
        finally:
            if rpc_session is not None:
                persistence.emit(
                    "phase", phase="rpc_cleanup",
                    message="RPC 모델 분할 프로세스를 종료하는 중",
                )
                try:
                    rpc_session.close()
                except Exception as cleanup_exc:
                    persistence.emit("rpc_cleanup_failed", errors=[str(cleanup_exc)])


__all__ = ["BenchmarkRunner", "benchmark_parameters"]
