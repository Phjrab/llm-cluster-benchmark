#!/usr/bin/env python3
"""Run reproducible streaming LLM load experiments across selected workers.

This module is the compatibility facade and CLI. Planning, execution, metrics,
RPC lifecycle, transport, and persistence live behind explicit boundaries.
"""

from __future__ import annotations

import argparse
import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from cluster.clusterctl import (
    DEFAULT_INVENTORY, Node, load_nodes, request_json, select_nodes,
    worker_auth_enabled,
)
from cluster.domain.experiment import ExperimentConfig, normalize_model_ids, validate_model_id
from cluster.domain.power import (
    RaspberryPiPowerIntegrity,
    normalize_power_integrity_snapshot,
    unavailable_power_integrity,
)
from cluster.domain.strategy import EXECUTION_STRATEGIES
from cluster.integrations.runtime_layout import default_project_layout, resolve_runtime_paths

from .core import BenchmarkRunner, benchmark_parameters
from .executor import ScenarioExecutor
from .metrics import aggregate_records, percentile
from .models import RequestTask, StrategyScenario
from .planner import build_strategy_scenarios, strategy_work_units, validate_strategy
from .rpc import (
    RPC_COORDINATOR_PORT, RPC_SERVER_PORT, WorkerRpcBackend,
    default_rpc_backend, worker_runtime_command,
)
from .strategies import get_strategy, strategy_catalog
from .transport import stream_rpc_request, stream_worker_request, utc_now


PROJECT_LAYOUT = default_project_layout()
PROJECT_ROOT = PROJECT_LAYOUT.root
RUNTIME_PATHS = resolve_runtime_paths()
DEFAULT_RESULTS_DIR = RUNTIME_PATHS.results_dir
ProgressCallback = Callable[[Dict[str, Any]], None]


def experiment_strategy_catalog() -> List[Dict[str, Any]]:
    return strategy_catalog()


def _stream_request(
    node: Node, config: ExperimentConfig, task: RequestTask, warmup: bool = False
) -> Dict[str, Any]:
    return stream_worker_request(node, config, task, warmup)


def _stream_rpc_request(
    coordinator: Node, coordinator_url: str, config: ExperimentConfig, task: RequestTask
) -> Dict[str, Any]:
    return stream_rpc_request(coordinator, coordinator_url, config, task)


def _load_model(node: Node, config: ExperimentConfig) -> Dict[str, Any]:
    result = request_json(
        f"{node.api_url}/api/select-model",
        method="POST",
        payload={"model_id": config.model_id, "n_ctx": config.n_ctx, "n_gpu_layers": config.n_gpu_layers},
        timeout=900.0,
    )
    current = result.get("current") or {}
    if result.get("ok") is not True:
        raise RuntimeError(f"{node.name} rejected model selection")
    health = request_json(f"{node.api_url}/cluster/health", timeout=10.0)
    profile = health.get("profile") or {}
    return {
        "node": node.name,
        **current,
        "platform_kind": profile.get("platform_kind"),
        "runtime_backend": (profile.get("runtime_backend") or {}).get("kind"),
        "inference_threads": profile.get("inference_threads"),
    }


def _sample_power_integrity(node: Node) -> Optional[RaspberryPiPowerIntegrity]:
    """Read optional Pi power evidence; never turn a probe failure into run failure."""
    if node.platform not in {"auto", "raspberry-pi"}:
        return None
    try:
        health = request_json(f"{node.api_url}/cluster/health", timeout=4.0)
    except Exception:
        return (
            unavailable_power_integrity(observed_at=utc_now())
            if node.platform == "raspberry-pi"
            else None
        )
    platform = str(
        (health.get("profile") or {}).get("platform_kind") or node.platform
    )
    if platform != "raspberry-pi":
        return None
    return normalize_power_integrity_snapshot(
        health.get("power_integrity"), observed_at=utc_now()
    )


def _validate_uniform(loaded: Sequence[Dict[str, Any]], config: ExperimentConfig) -> List[str]:
    warnings: List[str] = []
    for key in ("model_id", "n_ctx", "n_gpu_layers", "n_batch"):
        values = {str(item.get(key)) for item in loaded}
        if len(values) > 1:
            warnings.append(f"nodes differ in actual {key}: {', '.join(sorted(values))}")
    for item in loaded:
        if item.get("n_ctx") != config.n_ctx:
            warnings.append(f"{item['node']} adjusted n_ctx to {item.get('n_ctx')}")
        if item.get("n_gpu_layers") != config.n_gpu_layers:
            warnings.append(f"{item['node']} adjusted n_gpu_layers to {item.get('n_gpu_layers')}")
    return warnings


def validate_platform_layers(nodes: Sequence[Node], config: ExperimentConfig) -> None:
    if get_strategy(config.execution_strategy).execution_backend == "rpc" or config.n_gpu_layers == 0:
        return
    pi_nodes: List[str] = []
    for node in nodes:
        kind = node.platform
        if kind == "auto":
            try:
                health = request_json(f"{node.api_url}/cluster/health", timeout=5.0)
                kind = str((health.get("profile") or {}).get("platform_kind") or "auto")
            except Exception:
                kind = "auto"
        if kind == "raspberry-pi":
            pi_nodes.append(node.name)
    if pi_nodes:
        raise ValueError("Raspberry Pi nodes require n_gpu_layers=0: " + ", ".join(pi_nodes))


def _rpc_runtime_command(
    node: Node, action: str, *arguments: str, timeout: int = 120
) -> Dict[str, Any]:
    return worker_runtime_command(node, action, *arguments, timeout=timeout)


def _rpc_backend() -> WorkerRpcBackend:
    return default_rpc_backend(
        runtime_command=_rpc_runtime_command, project_root=PROJECT_ROOT
    )


def rpc_preflight(nodes: Sequence[Node]) -> List[Dict[str, Any]]:
    return _rpc_backend().preflight(nodes)


def _rpc_platform_from_check(node: Node, check: Dict[str, Any]) -> str:
    return WorkerRpcBackend.platform_from_check(node, check)


def _start_rpc_topology(
    nodes: Sequence[Node], config: ExperimentConfig, emit: Callable[..., None]
) -> tuple[Node, str, Dict[str, Any], List[Node]]:
    session = _rpc_backend().start(nodes, config, emit)
    return session.coordinator, session.url, session.topology, session.started_devices


def _stop_rpc_topology(coordinator: Node, workers: Sequence[Node]) -> List[str]:
    return _rpc_backend().stop(coordinator, workers)


_aggregate = aggregate_records


def _worker_stream_adapter(
    node: Node, config: ExperimentConfig, task: RequestTask, warmup: bool
) -> Dict[str, Any]:
    if warmup:
        return _stream_request(node, config, task, True)
    return _stream_request(node, config, task)


def _measure_scenario(
    scenario: StrategyScenario,
    nodes_by_name: Dict[str, Node],
    config: ExperimentConfig,
    emit: Callable[..., None],
    cancel_event: threading.Event,
    completed_offset: int,
    total_work_units: int,
    rpc_coordinator: Optional[Node] = None,
    rpc_url: str = "",
) -> tuple[List[Dict[str, Any]], float]:
    return ScenarioExecutor(_worker_stream_adapter, _stream_rpc_request).execute(
        scenario, nodes_by_name, config, emit, cancel_event, completed_offset,
        total_work_units, rpc_coordinator, rpc_url,
    )


def run_experiment(
    config: ExperimentConfig,
    inventory_path: Path = DEFAULT_INVENTORY,
    results_root: Path = DEFAULT_RESULTS_DIR,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    config.validate()
    if config.execution_strategy == "model_parallel_rpc" and worker_auth_enabled():
        raise ValueError(
            "model_parallel_rpc cannot run while Worker API authentication is enabled; "
            "native llama.cpp RPC is unauthenticated"
        )
    # A macOS Controller owns a Worker-only inventory.  Keep legacy head rows
    # readable when present, but never require a synthetic inference head for
    # the benchmark facade or durable child-process path.
    all_nodes = load_nodes(inventory_path, require_legacy_head=False)
    selected = select_nodes(all_nodes, config.node_names)
    if len(selected) != len(config.node_names):
        raise ValueError("Some selected nodes are unavailable")
    selected_by_name = {node.name: node for node in selected}
    nodes = [selected_by_name[name] for name in config.node_names]
    executor = ScenarioExecutor(_worker_stream_adapter, _stream_rpc_request)
    runner = BenchmarkRunner(
        _load_model,
        _validate_uniform,
        validate_platform_layers,
        executor,
        _rpc_backend(),
        _sample_power_integrity,
    )
    return runner.run(config, nodes, results_root, progress, cancel_event)


def main() -> int:
    runtime_paths = resolve_runtime_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, default=runtime_paths.inventory_path)
    parser.add_argument("--results-dir", type=Path, default=runtime_paths.results_dir)
    args = parser.parse_args()
    config = ExperimentConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    try:
        summary = run_experiment(
            config,
            inventory_path=args.inventory,
            results_root=args.results_dir,
            progress=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
        )
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
