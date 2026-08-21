#!/usr/bin/env python3
"""Standalone FastAPI runtime for an inference Worker.

The Controller/Dashboard is intentionally not imported here.  Worker routes
receive explicit inference and telemetry services and can therefore be tested
without a native llama.cpp installation.
"""

from __future__ import annotations

import hashlib
import os
import platform
import secrets
import shutil
import socket
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import psutil
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from cluster.integrations.runtime_layout import default_project_layout

from .inference import InferenceBackend, LlamaCppInferenceBackend
from .routes import WorkerRuntimeInfo, mount_worker_routes
from .telemetry import TelemetryService, read_text


PROJECT_ROOT = default_project_layout().root


def detect_platform_kind(environment: Optional[Dict[str, str]] = None) -> str:
    values = os.environ if environment is None else environment
    override = values.get("CLUSTER_PLATFORM", "").strip().lower()
    if override in {"jetson", "raspberry-pi", "generic-linux"}:
        return override
    board = read_text("/proc/device-tree/model").lower()
    if "raspberry pi" in board:
        return "raspberry-pi"
    if Path("/etc/nv_tegra_release").exists() or shutil.which("nvpmodel"):
        return "jetson"
    return "generic-linux"


def _git_commit(project_root: Path) -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "-C", str(project_root), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def runtime_backend() -> Dict[str, Any]:
    """Describe the installed backend without making telemetry a dependency."""
    try:
        import llama_cpp as llama_package
        from llama_cpp import llama_cpp

        info = llama_cpp.llama_print_system_info().decode("utf-8", errors="replace")
        supports_gpu = bool(llama_cpp.llama_supports_gpu_offload())
        kind = "cuda" if supports_gpu else "cpu"
        if not supports_gpu:
            try:
                for candidate in Path(llama_package.__file__).resolve().parent.rglob("*.so"):
                    linked = subprocess.check_output(
                        ["ldd", str(candidate)], text=True, stderr=subprocess.DEVNULL, timeout=3
                    )
                    if "openblas" in linked.lower():
                        kind = "openblas"
                        break
            except (OSError, subprocess.SubprocessError):
                pass
        normalized_info = " ".join(info.split())[:1000]
        return {
            "verified": True,
            "kind": kind,
            "gpu_offload": supports_gpu,
            "llama_cpp_python": getattr(llama_package, "__version__", "unknown"),
            "system_info": normalized_info,
            "runtime_fingerprint": hashlib.sha256(normalized_info.encode("utf-8")).hexdigest()[:16],
        }
    except Exception as exc:  # Native runtime is optional for import/test only.
        return {"verified": False, "kind": "unknown", "gpu_offload": False, "error": str(exc)}


def _os_release() -> Dict[str, str]:
    result: Dict[str, str] = {}
    for line in read_text("/etc/os-release").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
    return result


def system_profile(project_root: Path, platform_kind: str, backend: Dict[str, Any]) -> Dict[str, Any]:
    release = _os_release()
    memory = psutil.virtual_memory()
    try:
        swap = psutil.swap_memory()
        swap_total_mb = round(swap.total / (1024 * 1024), 2)
        swap_free_mb = round(swap.free / (1024 * 1024), 2)
    except OSError:
        # Some restricted macOS test/container environments cannot query VM
        # swap. This is observational metadata, never an inference gate.
        swap_total_mb = None
        swap_free_mb = None
    cpu_model = ""
    for line in read_text("/proc/cpuinfo").splitlines():
        if line.lower().startswith(("model name", "hardware")) and ":" in line:
            cpu_model = line.split(":", 1)[1].strip()
            if cpu_model:
                break
    cuda = ""
    nvcc = Path("/usr/local/cuda/bin/nvcc")
    if nvcc.exists():
        try:
            cuda = subprocess.check_output(
                [str(nvcc), "--version"], text=True, stderr=subprocess.DEVNULL, timeout=3
            ).splitlines()[-1].strip()
        except (OSError, subprocess.SubprocessError):
            pass
    l4t = read_text("/etc/nv_tegra_release").splitlines()
    return {
        "platform_kind": platform_kind,
        "board_model": read_text("/proc/device-tree/model") or platform.machine(),
        "os": release.get("PRETTY_NAME") or platform.platform(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "cpu_model": cpu_model or platform.processor() or platform.machine(),
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "inference_threads": int(os.getenv("LLM_N_THREADS", str(min(6, psutil.cpu_count(logical=True) or 1)))),
        "memory_total_mb": round(memory.total / (1024 * 1024), 2),
        "memory_available_mb": round(memory.available / (1024 * 1024), 2),
        "swap_total_mb": swap_total_mb,
        "swap_free_mb": swap_free_mb,
        "accelerator": backend["kind"],
        "runtime_backend": backend,
        "l4t": l4t[0] if l4t else "",
        "cuda": cuda,
        "git_commit": _git_commit(project_root),
    }


def create_app(
    *,
    backend: Optional[InferenceBackend] = None,
    telemetry: Optional[TelemetryService] = None,
    project_root: Path = PROJECT_ROOT,
    environment: Optional[Dict[str, str]] = None,
) -> FastAPI:
    """Create a Worker-only ASGI app, with injectable services for contracts."""
    values = os.environ if environment is None else environment
    platform_kind = detect_platform_kind(values)
    node_name = values.get("CLUSTER_NODE_NAME", socket.gethostname())
    node_role = values.get("CLUSTER_NODE_ROLE", "worker")
    token = values.get("CLUSTER_API_TOKEN", "").strip()
    auth_enabled = values.get("CLUSTER_WORKER_AUTH", "false").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    resolved_root = Path(project_root)
    selected_backend = backend or LlamaCppInferenceBackend(resolved_root / "models")
    selected_telemetry = telemetry or TelemetryService.for_platform(platform_kind, resolved_root)
    backend_profile = runtime_backend()
    runtime = WorkerRuntimeInfo(
        node_name=node_name,
        node_role=node_role,
        hostname=socket.gethostname(),
        platform=platform.platform(),
        platform_kind=platform_kind,
        git_commit=_git_commit(resolved_root),
        profile=system_profile(resolved_root, platform_kind, backend_profile),
        worker_api_auth=auth_enabled,
    )
    telemetry_running = False
    if telemetry is not None:
        # Preserve the injectable service contract used by existing callers;
        # the real platform sampler starts only when the ASGI app starts.
        selected_telemetry.start()
        telemetry_running = True

    @asynccontextmanager
    async def worker_lifespan(_application: FastAPI):
        nonlocal telemetry_running
        if not telemetry_running:
            selected_telemetry.start()
            telemetry_running = True
        try:
            yield
        finally:
            if telemetry_running:
                selected_telemetry.stop()
                telemetry_running = False

    app = FastAPI(
        title="LLM Cluster Worker",
        version="1.0.0",
        lifespan=worker_lifespan,
    )

    @app.middleware("http")
    async def require_cluster_api_token(request: Request, call_next: Any) -> Any:
        if not auth_enabled:
            return await call_next(request)
        supplied = request.headers.get("X-Cluster-Worker-Token", "")
        if not token or not supplied or not secrets.compare_digest(supplied, token):
            return JSONResponse(status_code=401, content={"detail": "Worker API authentication failed"})
        return await call_next(request)

    mount_worker_routes(app, backend=selected_backend, telemetry=selected_telemetry, runtime=runtime)
    app.state.inference_backend = selected_backend
    app.state.telemetry = selected_telemetry
    app.state.worker_runtime = runtime
    return app


app = create_app()


__all__ = ["app", "create_app", "detect_platform_kind", "runtime_backend", "system_profile"]
