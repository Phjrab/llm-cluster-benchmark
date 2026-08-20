"""Worker-hosted llama.cpp RPC lifecycle and coordinator selection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from cluster.clusterctl import request_json, run_on_node
from cluster.domain.errors import ErrorCode
from cluster.domain.experiment import ExperimentConfig

from .rpc_selection import RpcBackendError, select_rpc_coordinator


RPC_SERVER_PORT = 50052
RPC_COORDINATOR_PORT = 18080
RuntimeCommand = Callable[..., Dict[str, Any]]


def worker_runtime_command(
    node: Any, action: str, *arguments: str, timeout: int = 120
) -> Dict[str, Any]:
    """Execute the pinned RPC lifecycle script through the SSH adapter."""
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
    _closer: Callable[[], List[str]] = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        errors = self._closer()
        if errors:
            raise RpcBackendError(
                "RPC cleanup failed: " + "; ".join(errors),
                code=ErrorCode.RPC_CLEANUP_FAILED,
                stage="rpc_cleanup",
                node=self.coordinator.name,
                evidence={"cleanup_errors": errors},
            )
        self._closed = True

    def __enter__(self) -> "RpcSession":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        self.close()
        return False


class RpcBackend(Protocol):
    def start(
        self, nodes: Sequence[Any], config: ExperimentConfig, emit: Callable[..., None]
    ) -> RpcSession: ...


class WorkerRpcBackend:
    def __init__(
        self,
        runtime_command: RuntimeCommand,
        request_json: Callable[..., Dict[str, Any]],
        run_on_node: Callable[..., Any],
        project_root: Optional[Any] = None,
    ) -> None:
        self.runtime_command = runtime_command
        self.request_json = request_json
        self.run_on_node = run_on_node

    @staticmethod
    def platform_from_check(node: Any, check: Dict[str, Any]) -> str:
        output = f"{check.get('stdout', '')}\n{check.get('stderr', '')}"
        if "platform=raspberry-pi" in output:
            return "raspberry-pi"
        if "platform=jetson" in output:
            return "jetson"
        return str(getattr(node, "platform", "auto"))

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
        workers = list(nodes)
        select_rpc_coordinator(workers, config.rpc_coordinator_node)
        checks = self.preflight(workers)
        missing = [item for item in checks if not item["ok"]]
        if missing:
            details = "; ".join(
                f"{item['node']}: {item.get('error', 'not ready')}" for item in missing
            )
            raise RpcBackendError(
                "RPC runtime is not ready. Run 'RPC 환경 준비' first. " + details,
                code=ErrorCode.RPC_NOT_PREPARED,
                stage="rpc_preflight",
                evidence={"nodes": [item["node"] for item in missing]},
            )

        checks_by_name = {item["node"]: item for item in checks}
        platforms = {
            node.name: self.platform_from_check(node, checks_by_name[node.name])
            for node in workers
        }
        coordinator = select_rpc_coordinator(
            workers, config.rpc_coordinator_node, platforms
        )
        remote_devices = [node for node in workers if node.name != coordinator.name]

        for node in workers:
            try:
                self.request_json(
                    f"{node.api_url}/api/unload-model", method="POST", payload={}, timeout=60.0
                )
            except Exception as exc:
                raise RpcBackendError(
                    f"Failed to unload the replicated model on {node.name}: {exc}",
                    code=ErrorCode.RPC_DEVICE_FAILED,
                    stage="rpc_unload",
                    node=node.name,
                    model_id=config.model_id,
                ) from exc

        coordinator_platform = platforms[coordinator.name]
        endpoints: List[str] = []
        started_devices: List[Any] = []
        rpc_device_nodes: List[Any] = []
        try:
            for device in remote_devices:
                emit("rpc_started", node=device.name, role="device", port=RPC_SERVER_PORT)
                # Register the attempt before SSH. A timeout can happen after
                # the remote script has spawned its unauthenticated RPC server,
                # so cleanup must issue an idempotent stop even without a
                # successful response.
                started_devices.append(device)
                started = self.runtime_command(
                    device, "start-worker", str(RPC_SERVER_PORT), timeout=60
                )
                if not started["ok"]:
                    raise RpcBackendError(
                        f"RPC device failed on {device.name}: {started['stderr'] or started['stdout']}",
                        code=ErrorCode.RPC_DEVICE_FAILED,
                        stage="rpc_device_start",
                        node=device.name,
                    )
                rpc_device_nodes.append(device)
                endpoints.append(f"{device.host}:{RPC_SERVER_PORT}")

            if coordinator_platform == "raspberry-pi":
                emit(
                    "rpc_started", node=coordinator.name, role="loopback_cpu_device",
                    port=RPC_SERVER_PORT,
                )
                started_devices.append(coordinator)
                started = self.runtime_command(
                    coordinator, "start-worker", str(RPC_SERVER_PORT), "127.0.0.1", timeout=60
                )
                if not started["ok"]:
                    raise RpcBackendError(
                        f"RPC loopback CPU device failed on {coordinator.name}: "
                        f"{started['stderr'] or started['stdout']}",
                        code=ErrorCode.RPC_DEVICE_FAILED,
                        stage="rpc_device_start",
                        node=coordinator.name,
                    )
                rpc_device_nodes.append(coordinator)
                endpoints.append(f"127.0.0.1:{RPC_SERVER_PORT}")

            model_path = PurePosixPath(coordinator.project_dir) / "models" / config.model_id
            model_check = self.run_on_node(
                coordinator, ["test", "-f", str(model_path)], timeout=20
            )
            if model_check.returncode != 0:
                raise RpcBackendError(
                    f"Coordinator model is missing: {config.model_id}",
                    code=ErrorCode.RPC_MODEL_LOAD_FAILED,
                    stage="rpc_model_preflight",
                    node=coordinator.name,
                    model_id=config.model_id,
                )

            resolved_device_nodes = list(rpc_device_nodes)
            if coordinator_platform != "raspberry-pi":
                resolved_device_nodes.append(coordinator)
            split_values: List[float] = []
            if str(config.rpc_split_policy) == "equal":
                split_values = [1.0] * len(resolved_device_nodes)
            elif str(config.rpc_split_policy) == "custom":
                requested_by_node = {
                    node.name: float(value)
                    for node, value in zip(workers, config.rpc_tensor_split)
                }
                split_values = [requested_by_node[node.name] for node in resolved_device_nodes]
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
                "0.0.0.0",
                timeout=900,
            )
            if not started["ok"]:
                output = started["stderr"] or started["stdout"]
                code = (
                    ErrorCode.RPC_MODEL_LOAD_FAILED
                    if "model" in output.lower()
                    else ErrorCode.RPC_COORDINATOR_FAILED
                )
                raise RpcBackendError(
                    f"RPC coordinator failed: {output}",
                    code=code,
                    stage="rpc_coordinator_start",
                    node=coordinator.name,
                    model_id=config.model_id,
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
                "coordinator_platform": coordinator_platform,
                "participants": [node.name for node in workers],
                "rpc_workers": [node.name for node in remote_devices],
                "rpc_device_nodes": [node.name for node in rpc_device_nodes],
                "rpc_endpoints": endpoints,
                "split_mode": str(config.rpc_split_mode),
                "split_policy": str(config.rpc_split_policy),
                "tensor_split": split_values,
                "resolved_device_order": [node.name for node in resolved_device_nodes],
                "requested_gpu_layers": "all",
                "model_load_s": round(load_s, 6),
                "transport": "TCP LAN",
                "rpc_security": "unauthenticated_ephemeral_private_lan",
                "coordinator_slots": 1,
                "client_concurrency": config.concurrency,
            }
            return RpcSession(
                coordinator,
                f"http://{coordinator.host}:{RPC_COORDINATOR_PORT}",
                topology,
                started_devices,
                lambda: self.stop(coordinator, started_devices),
            )
        except Exception as exc:
            cleanup_errors = self.stop(coordinator, started_devices)
            if cleanup_errors:
                raise RpcBackendError(
                    f"{exc}; RPC cleanup also failed: {'; '.join(cleanup_errors)}",
                    code=ErrorCode.RPC_CLEANUP_FAILED,
                    stage="rpc_cleanup",
                    node=coordinator.name,
                    evidence={"cleanup_errors": cleanup_errors},
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


def default_rpc_backend(
    *, runtime_command: Optional[RuntimeCommand] = None, project_root: Optional[Any] = None
) -> WorkerRpcBackend:
    return WorkerRpcBackend(
        runtime_command or worker_runtime_command,
        request_json,
        run_on_node,
        project_root,
    )


# Public compatibility aliases for callers migrated in later phases.
LegacyRpcBackend = WorkerRpcBackend
legacy_runtime_command = worker_runtime_command
default_legacy_rpc_backend = default_rpc_backend


__all__ = [
    "LegacyRpcBackend", "RPC_COORDINATOR_PORT", "RPC_SERVER_PORT", "RpcBackend",
    "RpcBackendError", "RpcSession", "WorkerRpcBackend", "default_legacy_rpc_backend",
    "default_rpc_backend", "legacy_runtime_command", "select_rpc_coordinator",
    "worker_runtime_command",
]
