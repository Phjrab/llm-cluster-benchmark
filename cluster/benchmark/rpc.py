"""Worker-hosted llama.cpp RPC lifecycle and coordinator selection."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

from cluster.clusterctl import request_json, run_on_node
from cluster.domain.errors import ErrorCode
from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.gguf import canonical_metadata_sha256

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
        cleanup_started = time.perf_counter()
        try:
            errors = self._closer()
        except Exception as exc:
            errors = [f"{type(exc).__name__}: {exc}"]
        finally:
            self.topology["cleanup_s"] = round(time.perf_counter() - cleanup_started, 6)
        duration = self.topology["cleanup_s"]
        attempts = self.topology.setdefault("cleanup_attempts", [])
        attempts.append({
            "attempt": len(attempts) + 1,
            "duration_s": duration,
            "ok": not errors,
            "errors": list(errors),
        })
        self.topology["cleanup_s"] = round(
            sum(float(item.get("duration_s") or 0.0) for item in attempts), 6
        )
        self.topology["cleanup_status"] = (
            "failed" if errors else ("completed_after_retry" if len(attempts) > 1 else "completed")
        )
        if errors:
            self.topology["cleanup_errors"] = list(errors)
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

    @staticmethod
    def capabilities_from_check(check: Dict[str, Any]) -> Dict[str, set[str]]:
        output = f"{check.get('stdout', '')} {check.get('stderr', '')}"
        capabilities = {
            "rpc_split_modes": {"layer"},
            "rpc_gpu_layers": {"all"},
            "rpc_input_preparation": set(),
        }
        for token in output.split():
            for name in tuple(capabilities):
                prefix = name + "="
                if token.startswith(prefix):
                    capabilities[name] = {
                        item for item in token[len(prefix):].split(",") if item
                    }
        return capabilities

    def _verify_capabilities(
        self,
        nodes: Sequence[Any],
        checks_by_name: Dict[str, Dict[str, Any]],
        config: ExperimentConfig,
    ) -> None:
        required_gpu = "all" if config.rpc_gpu_layers == "all" else "integer"
        missing: List[Dict[str, str]] = []
        for node in nodes:
            available = self.capabilities_from_check(checks_by_name[node.name])
            if str(config.rpc_split_mode) not in available["rpc_split_modes"]:
                missing.append({"node": node.name, "capability": f"split:{config.rpc_split_mode}"})
            if required_gpu not in available["rpc_gpu_layers"]:
                missing.append({"node": node.name, "capability": f"gpu_layers:{required_gpu}"})
            if config.sweep is not None and not {
                "apply-template", "tokenize", "props"
            }.issubset(available["rpc_input_preparation"]):
                missing.append({"node": node.name, "capability": "exact_input_preparation"})
        if missing:
            raise RpcBackendError(
                "Pinned RPC runtime does not report the requested capability",
                code=ErrorCode.RPC_NOT_PREPARED,
                stage="rpc_capability",
                evidence={"unsupported": missing},
            )

    def _assert_clean_start(
        self,
        coordinator: Any,
        remote_devices: Sequence[Any],
        coordinator_platform: str,
    ) -> None:
        checks = [(coordinator, "assert-stopped-coordinator", RPC_COORDINATOR_PORT)]
        checks.extend((node, "assert-stopped-worker", RPC_SERVER_PORT) for node in remote_devices)
        if coordinator_platform == "raspberry-pi":
            checks.append((coordinator, "assert-stopped-worker", RPC_SERVER_PORT))
        failures = []
        for node, action, port in checks:
            try:
                result = self.runtime_command(node, action, str(port), timeout=20)
                if not result["ok"]:
                    failures.append({
                        "node": node.name,
                        "role": action,
                        "error": result["stderr"] or result["stdout"] or "not stopped",
                    })
            except Exception as exc:
                failures.append({"node": node.name, "role": action, "error": type(exc).__name__})
        if failures:
            raise RpcBackendError(
                "Previous RPC process or port state is not clean",
                code=ErrorCode.RPC_NOT_PREPARED,
                stage="rpc_residual_guard",
                evidence={"failures": failures},
            )

    def _prepare_sweep_input(
        self,
        url: str,
        model_path: PurePosixPath,
        config: ExperimentConfig,
    ) -> Dict[str, Any]:
        trace = config.sweep
        if trace is None:
            return {}
        props = self.request_json(f"{url}/props", timeout=30.0)
        if props.get("model_path") != str(model_path):
            raise RpcBackendError(
                "RPC coordinator reported a different model path",
                code=ErrorCode.RPC_MODEL_LOAD_FAILED,
                stage="rpc_model_identity",
                model_id=config.model_id,
            )
        template = props.get("chat_template")
        runtime_template_hash = (
            canonical_metadata_sha256({"tokenizer.chat_template": template})
            if isinstance(template, str) and template
            else ""
        )
        if runtime_template_hash != trace["template_sha256"]:
            raise RpcBackendError(
                "RPC coordinator chat template identity mismatch",
                code=ErrorCode.RPC_MODEL_LOAD_FAILED,
                stage="rpc_model_metadata",
                model_id=config.model_id,
            )
        effective_n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
        if effective_n_ctx != config.n_ctx:
            raise RpcBackendError(
                "RPC coordinator effective context differs from requested n_ctx",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_effective_context",
                evidence={"requested_n_ctx": config.n_ctx, "effective_n_ctx": effective_n_ctx},
            )
        applied = self.request_json(
            f"{url}/apply-template",
            method="POST",
            payload={"messages": [{"role": "user", "content": config.prompt}]},
            timeout=30.0,
        )
        rendered = applied.get("prompt")
        if not isinstance(rendered, str):
            raise RpcBackendError(
                "RPC coordinator did not return a rendered prompt",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_input_preparation",
            )
        tokenized = self.request_json(
            f"{url}/tokenize",
            method="POST",
            payload={"content": rendered, "add_special": False, "parse_special": True},
            timeout=30.0,
        )
        tokens = tokenized.get("tokens")
        if (
            not isinstance(tokens, list)
            or not tokens
            or any(type(token) is not int for token in tokens)
        ):
            raise RpcBackendError(
                "RPC coordinator did not return exact token IDs",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_input_preparation",
            )
        count = len(tokens)
        if count + config.max_tokens > config.n_ctx:
            raise RpcBackendError(
                "RPC prompt and output reserve exceed n_ctx",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_context_budget",
                evidence={
                    "input_tokens": count,
                    "output_reserve_tokens": config.max_tokens,
                    "effective_n_ctx": config.n_ctx,
                },
            )
        target = trace.get("target_input_tokens")
        if target is not None and count != target:
            raise RpcBackendError(
                "RPC prepared input does not match the token-length target",
                code=ErrorCode.CONFIG_MISMATCH,
                stage="rpc_input_preparation",
                evidence={"input_tokens": count, "target_input_tokens": target},
            )
        return {
            "preparation_id": trace["attempt_id"],
            "prompt_sha256": trace["prompt_sha256"],
            "rendered_prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "template_hash": trace["template_sha256"],
            "model_sha256": trace["model_sha256"],
            "effective_n_ctx": config.n_ctx,
            "output_reserve_tokens": config.max_tokens,
            "input_token_source": "pinned_llama_server_apply_template_tokenize",
            "input_tokens": count,
            "input_tokens_exact": True,
        }

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
        self._verify_capabilities(workers, checks_by_name, config)
        platforms = {
            node.name: self.platform_from_check(node, checks_by_name[node.name])
            for node in workers
        }
        coordinator = select_rpc_coordinator(
            workers, config.rpc_coordinator_node, platforms
        )
        remote_devices = [node for node in workers if node.name != coordinator.name]
        self._assert_clean_start(
            coordinator, remote_devices, platforms[coordinator.name]
        )

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
        device_start_s: Dict[str, float] = {}
        try:
            for device in remote_devices:
                emit("rpc_started", node=device.name, role="device", port=RPC_SERVER_PORT)
                # Register the attempt before SSH. A timeout can happen after
                # the remote script has spawned its unauthenticated RPC server,
                # so cleanup must issue an idempotent stop even without a
                # successful response.
                started_devices.append(device)
                device_started = time.perf_counter()
                started = self.runtime_command(
                    device, "start-worker", str(RPC_SERVER_PORT), device.host, timeout=60
                )
                device_start_s[device.name] = round(time.perf_counter() - device_started, 6)
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
                device_started = time.perf_counter()
                started = self.runtime_command(
                    coordinator, "start-worker", str(RPC_SERVER_PORT), "127.0.0.1", timeout=60
                )
                device_start_s[coordinator.name] = round(
                    time.perf_counter() - device_started, 6
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
            if config.sweep is not None:
                checksum = self.run_on_node(
                    coordinator, ["sha256sum", "--", str(model_path)], timeout=900
                )
                observed_sha256 = checksum.stdout.strip().split(maxsplit=1)[0] if checksum.returncode == 0 else ""
                if observed_sha256 != config.sweep["model_sha256"]:
                    raise RpcBackendError(
                        "Coordinator model checksum differs from the sweep identity",
                        code=ErrorCode.RPC_MODEL_LOAD_FAILED,
                        stage="rpc_model_identity",
                        node=coordinator.name,
                        model_id=config.model_id,
                    )

            resolved_device_nodes = list(rpc_device_nodes)
            if coordinator_platform != "raspberry-pi":
                resolved_device_nodes.append(coordinator)
            split_values: List[float] = []
            requested_weights_by_node: Dict[str, float] = {}
            if str(config.rpc_split_policy) == "equal":
                split_values = [1.0] * len(resolved_device_nodes)
                requested_weights_by_node = {node.name: 1.0 for node in workers}
            elif str(config.rpc_split_policy) == "custom":
                requested_by_node = {
                    node.name: float(value)
                    for node, value in zip(workers, config.rpc_tensor_split)
                }
                requested_weights_by_node = dict(requested_by_node)
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
                str(config.rpc_gpu_layers),
                endpoints_csv,
                str(config.rpc_split_mode),
                split_csv,
                coordinator.host,
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
            coordinator_url = f"http://{coordinator.host}:{RPC_COORDINATOR_PORT}"
            input_preparation = self._prepare_sweep_input(
                coordinator_url, model_path, config
            )
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
                "requested_weights_by_worker": requested_weights_by_node,
                "resolved_device_order": [node.name for node in resolved_device_nodes],
                "requested_gpu_layers": config.rpc_gpu_layers,
                "effective_gpu_layers_argv": str(config.rpc_gpu_layers),
                "actual_layer_placement": None,
                "requested_n_ctx": config.n_ctx,
                "model_id": config.model_id,
                "rpc_session_id": uuid.uuid4().hex,
                "rpc_session_policy": "new_session_per_cell",
                "model_cache_policy": "reload_per_cell",
                "model_load_cache_reused": False,
                "model_load_s": round(load_s, 6),
                "model_load_distribution_s": {
                    "rpc_device_start_s": device_start_s,
                    "coordinator_model_load_s": round(load_s, 6),
                },
                "transport": "TCP LAN",
                "rpc_security": "unauthenticated_ephemeral_private_lan",
                "coordinator_slots": 1,
                "client_concurrency": config.concurrency,
            }
            if input_preparation:
                topology["input_preparation"] = input_preparation
            return RpcSession(
                coordinator,
                coordinator_url,
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
        try:
            result = self.runtime_command(
                coordinator, "assert-stopped-coordinator", str(RPC_COORDINATOR_PORT), timeout=20
            )
            if not result["ok"]:
                errors.append(
                    f"{coordinator.name} coordinator residual: {result['stderr'] or result['stdout']}"
                )
        except Exception as exc:
            errors.append(f"{coordinator.name} coordinator residual: {exc}")
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
            try:
                result = self.runtime_command(
                    device, "assert-stopped-worker", str(RPC_SERVER_PORT), timeout=20
                )
                if not result["ok"]:
                    errors.append(
                        f"{device.name} RPC device residual: {result['stderr'] or result['stdout']}"
                    )
            except Exception as exc:
                errors.append(f"{device.name} RPC device residual: {exc}")
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
