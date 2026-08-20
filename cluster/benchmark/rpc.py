"""Legacy llama.cpp RPC session boundary.

The legacy head-as-coordinator contract is deliberately preserved here.
Controller-hosted coordinator migration is Phase 07 work.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from cluster.clusterctl import request_json, run_on_node
from cluster.domain.experiment import ExperimentConfig
from cluster.integrations.runtime_layout import default_project_layout


RPC_SERVER_PORT = 50052
RPC_COORDINATOR_PORT = 18080
RuntimeCommand = Callable[..., Dict[str, Any]]


def legacy_runtime_command(
    node: Any, action: str, *arguments: str, timeout: int = 120
) -> Dict[str, Any]:
    """Execute the pinned RPC lifecycle script through the legacy SSH adapter."""
    script = f"{node.project_dir}/cluster/rpc/runtime.sh"
    process = run_on_node(node, [script, action, *arguments], timeout=timeout)
    return {
        "node": node.name,
        "ok": process.returncode == 0,
        "stdout": process.stdout.strip(),
        "stderr": process.stderr.strip(),
    }


@dataclass
class RpcSession:
    coordinator: Any
    url: str
    topology: Dict[str, Any]
    started_devices: List[Any]


class RpcBackend(Protocol):
    def start(
        self, nodes: Sequence[Any], config: ExperimentConfig, emit: Callable[..., None]
    ) -> RpcSession: ...

    def stop(self, coordinator: Any, devices: Sequence[Any]) -> List[str]: ...


class LegacyRpcBackend:
    def __init__(
        self,
        runtime_command: RuntimeCommand,
        request_json: Callable[..., Dict[str, Any]],
        run_on_node: Callable[..., Any],
        project_root: Path,
    ) -> None:
        self.runtime_command = runtime_command
        self.request_json = request_json
        self.run_on_node = run_on_node
        self.project_root = project_root

    @staticmethod
    def platform_from_check(node: Any, check: Dict[str, Any]) -> str:
        output = f"{check.get('stdout', '')}\n{check.get('stderr', '')}"
        if "platform=raspberry-pi" in output:
            return "raspberry-pi"
        if "platform=jetson" in output:
            return "jetson"
        return node.platform

    def preflight(self, nodes: Sequence[Any]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for node in nodes:
            item = self.runtime_command(node, "check", timeout=20)
            if not item["ok"]:
                item["error"] = item["stderr"] or item["stdout"] or "native RPC runtime unavailable"
            results.append(item)
        return results

    def start(
        self,
        nodes: Sequence[Any],
        config: ExperimentConfig,
        emit: Callable[..., None],
    ) -> RpcSession:
        coordinator = next(node for node in nodes if node.role == "head")
        workers = [node for node in nodes if node.role == "worker"]
        checks = self.preflight(nodes)
        missing = [item for item in checks if not item["ok"]]
        if missing:
            details = "; ".join(
                f"{item['node']}: {item.get('error', 'not ready')}" for item in missing
            )
            raise RuntimeError("RPC runtime is not ready. Run 'RPC 환경 준비' first. " + details)

        for node in nodes:
            try:
                self.request_json(
                    f"{node.api_url}/api/unload-model", method="POST", payload={}, timeout=60.0
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to unload the replicated model on {node.name}: {exc}"
                ) from exc

        checks_by_name = {item["node"]: item for item in checks}
        coordinator_platform = self.platform_from_check(
            coordinator, checks_by_name[coordinator.name]
        )
        endpoints: List[str] = []
        started_devices: List[Any] = []
        rpc_device_nodes: List[Any] = []
        try:
            for worker in workers:
                emit("rpc_started", node=worker.name, role="device", port=RPC_SERVER_PORT)
                started = self.runtime_command(
                    worker, "start-worker", str(RPC_SERVER_PORT), timeout=60
                )
                if not started["ok"]:
                    raise RuntimeError(
                        f"RPC device failed on {worker.name}: {started['stderr'] or started['stdout']}"
                    )
                started_devices.append(worker)
                rpc_device_nodes.append(worker)
                endpoints.append(f"{worker.host}:{RPC_SERVER_PORT}")

            if coordinator_platform == "raspberry-pi":
                emit(
                    "rpc_started", node=coordinator.name, role="loopback_cpu_device",
                    port=RPC_SERVER_PORT,
                )
                started = self.runtime_command(
                    coordinator, "start-worker", str(RPC_SERVER_PORT), "127.0.0.1", timeout=60
                )
                if not started["ok"]:
                    raise RuntimeError(
                        f"RPC loopback CPU device failed on {coordinator.name}: "
                        f"{started['stderr'] or started['stdout']}"
                    )
                started_devices.append(coordinator)
                rpc_device_nodes.append(coordinator)
                endpoints.append(f"127.0.0.1:{RPC_SERVER_PORT}")

            model_path = (self.project_root / "models" / config.model_id).resolve()
            try:
                model_path.relative_to((self.project_root / "models").resolve())
            except ValueError as exc:
                raise RuntimeError("Unsafe coordinator model path") from exc
            if not model_path.is_file():
                raise RuntimeError(f"Coordinator model is missing: {config.model_id}")

            split_values: List[float] = []
            if str(config.rpc_split_policy) == "equal":
                split_values = [1.0] * len(nodes)
            elif str(config.rpc_split_policy) == "custom":
                requested_by_node = {
                    node.name: float(value)
                    for node, value in zip(nodes, config.rpc_tensor_split)
                }
                split_values = [requested_by_node[node.name] for node in rpc_device_nodes]
                if coordinator_platform != "raspberry-pi":
                    split_values.append(requested_by_node[coordinator.name])
            split_csv = ",".join(f"{value:g}" for value in split_values) or "-"
            endpoints_csv = ",".join(endpoints)
            emit(
                "rpc_started", node=coordinator.name, role="coordinator",
                port=RPC_COORDINATOR_PORT, endpoints=endpoints,
            )
            load_started = time.perf_counter()
            started = self.runtime_command(
                coordinator,
                "start-coordinator",
                str(RPC_COORDINATOR_PORT),
                str(model_path),
                str(config.n_ctx),
                "999",
                endpoints_csv,
                str(config.rpc_split_mode),
                split_csv,
                timeout=900,
            )
            if not started["ok"]:
                raise RuntimeError(
                    f"RPC coordinator failed: {started['stderr'] or started['stdout']}"
                )
            load_s = time.perf_counter() - load_started
            commit_check = self.run_on_node(
                coordinator,
                [
                    "git", "-C",
                    f"{coordinator.project_dir}/.run/cluster/llama.cpp-src",
                    "rev-parse", "HEAD",
                ],
                timeout=20,
            )
            topology = {
                "engine": "llama.cpp-rpc",
                "runtime_commit": commit_check.stdout.strip() if commit_check.returncode == 0 else "unknown",
                "coordinator": coordinator.name,
                "participants": [node.name for node in nodes],
                "rpc_workers": [node.name for node in workers],
                "rpc_device_nodes": [node.name for node in rpc_device_nodes],
                "rpc_endpoints": endpoints,
                "split_mode": str(config.rpc_split_mode),
                "split_policy": str(config.rpc_split_policy),
                "tensor_split": split_values,
                "resolved_device_order": [node.name for node in rpc_device_nodes]
                + ([] if coordinator_platform == "raspberry-pi" else [coordinator.name]),
                "requested_gpu_layers": "all",
                "model_load_s": round(load_s, 6),
                "transport": "TCP LAN",
                "rpc_security": "unauthenticated_ephemeral_private_lan",
                "coordinator_slots": 1,
                "client_concurrency": config.concurrency,
            }
            return RpcSession(
                coordinator,
                f"http://127.0.0.1:{RPC_COORDINATOR_PORT}",
                topology,
                started_devices,
            )
        except Exception as exc:
            cleanup_errors = self.stop(coordinator, started_devices)
            if cleanup_errors:
                raise RuntimeError(
                    f"{exc}; RPC cleanup also failed: {'; '.join(cleanup_errors)}"
                ) from exc
            raise

    def stop(self, coordinator: Any, devices: Sequence[Any]) -> List[str]:
        errors: List[str] = []
        try:
            result = self.runtime_command(
                coordinator, "stop-coordinator", str(RPC_COORDINATOR_PORT), timeout=30
            )
            if not result["ok"]:
                errors.append(
                    f"{coordinator.name} coordinator: {result['stderr'] or result['stdout']}"
                )
        except Exception as exc:
            errors.append(f"{coordinator.name} coordinator: {exc}")
        for device in devices:
            try:
                result = self.runtime_command(
                    device, "stop-worker", str(RPC_SERVER_PORT), timeout=30
                )
                if not result["ok"]:
                    errors.append(
                        f"{device.name} RPC device: {result['stderr'] or result['stdout']}"
                    )
            except Exception as exc:
                errors.append(f"{device.name} RPC device: {exc}")
        return errors


def default_legacy_rpc_backend(
    *,
    runtime_command: Optional[RuntimeCommand] = None,
    project_root: Optional[Path] = None,
) -> LegacyRpcBackend:
    return LegacyRpcBackend(
        runtime_command or legacy_runtime_command,
        request_json,
        run_on_node,
        project_root or default_project_layout().root,
    )


__all__ = [
    "LegacyRpcBackend", "RPC_COORDINATOR_PORT", "RPC_SERVER_PORT", "RpcBackend", "RpcSession",
    "default_legacy_rpc_backend", "legacy_runtime_command",
]
