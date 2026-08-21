"""FastAPI routes delegating Worker inference and telemetry to explicit services."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from cluster.domain.failures import failure_from_exception, http_status_for_failure

from .inference import InferenceBackend
from .schemas import (
    ChatStreamRequest,
    ClusterChatRequest,
    DeleteModelRequest,
    InstallModelRequest,
    SelectModelRequest,
    VerifyModelRequest,
)
from .telemetry import TelemetryService


def as_sse(event_type: str, payload: Dict[str, object]) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


@dataclass(frozen=True)
class WorkerRuntimeInfo:
    node_name: str
    node_role: str
    hostname: str
    platform: str
    platform_kind: str
    git_commit: Optional[str]
    profile: Dict[str, Any]
    worker_api_auth: bool


def mount_worker_routes(
    app: FastAPI,
    *,
    backend: InferenceBackend,
    telemetry: TelemetryService,
    runtime: WorkerRuntimeInfo,
) -> None:
    """Register legacy and cluster API routes without exposing backend internals."""

    @app.get("/health")
    async def health() -> Dict[str, object]:
        return {"ok": True, "current": backend.current_model_info()}

    @app.get("/api/models")
    async def get_models() -> Dict[str, object]:
        response: Dict[str, object] = {
            "models": backend.list_models(),
            "current": backend.current_model_info(),
        }
        # This was part of the legacy ``web.app`` response consumed by the
        # standalone chat UI.  Keep it additive without coupling routes to a
        # particular backend implementation.
        models_dir = getattr(backend, "models_dir", None)
        if models_dir is not None:
            response["models_dir"] = str(models_dir)
        return response

    @app.post("/api/select-model")
    async def select_model(payload: SelectModelRequest) -> Dict[str, object]:
        try:
            current = backend.load_model(payload.model_id, payload.n_ctx, payload.n_gpu_layers)
        except Exception as exc:
            failure = failure_from_exception(
                exc, stage="model_loading", model_id=payload.model_id
            )
            raise HTTPException(
                status_code=http_status_for_failure(failure),
                detail=str(exc),
                headers={"X-Cluster-Error-Code": failure.code.value},
            ) from exc
        return {"ok": True, "current": current}

    @app.post("/api/unload-model")
    async def unload_model() -> Dict[str, object]:
        backend.unload_model()
        return {"ok": True, "current": backend.current_model_info()}

    def stream_response(payload: ChatStreamRequest, *, seed: Optional[int] = None) -> StreamingResponse:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=400, detail="Message is empty")

        def events() -> Iterable[str]:
            started = time.perf_counter()
            first_token_at: Optional[float] = None
            pieces: list[str] = []
            chunks = 0
            try:
                for token in backend.stream_chat(
                    message=message,
                    history=payload.history,
                    max_tokens=payload.max_tokens,
                    temperature=payload.temperature,
                    top_p=payload.top_p,
                    seed=seed,
                ):
                    now = time.perf_counter()
                    first_token_at = first_token_at or now
                    pieces.append(token)
                    chunks += 1
                    yield as_sse("token", {"text": token})
                finished = time.perf_counter()
                text = "".join(pieces)
                token_count = backend.tokenize(text) if text else 0
                token_count = token_count or chunks
                ttft_s = (first_token_at - started) if first_token_at else finished - started
                yield as_sse(
                    "done",
                    {
                        "metrics": {
                            "ttft_s": round(ttft_s, 6),
                            "generation_s": round(max(finished - (first_token_at or finished), 0.0), 6),
                            "e2e_s": round(finished - started, 6),
                            "generated_tokens": token_count,
                            "stream_chunks": chunks,
                            "output_chars": len(text),
                        }
                    },
                )
            except Exception as exc:
                failure = failure_from_exception(exc, stage="inference")
                yield as_sse(
                    "error",
                    {"message": str(exc), "failure": failure.to_dict()},
                )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatStreamRequest) -> StreamingResponse:
        return stream_response(payload)

    @app.get("/cluster/health")
    async def cluster_health() -> Dict[str, Any]:
        models = backend.list_models()
        telemetry_status = telemetry.status()
        inference_status = backend.readiness()
        response: Dict[str, Any] = {
            "ok": True,
            "node": {
                "name": runtime.node_name,
                "role": runtime.node_role,
                "hostname": runtime.hostname,
                "platform": runtime.platform,
                "platform_kind": runtime.platform_kind,
                "git_commit": runtime.git_commit,
            },
            "profile": runtime.profile,
            "capabilities": {
                "telemetry": telemetry_status["provider"],
                "telemetry_ready": telemetry_status["ready"],
                "telemetry_degraded": telemetry_status["degraded"],
                "telemetry_error": telemetry_status["error"],
                "gpu_offload": bool(runtime.profile.get("runtime_backend", {}).get("gpu_offload", False)),
                "backend_verified": bool(runtime.profile.get("runtime_backend", {}).get("verified", False)),
                "cpu_inference": True,
                "inference_ready": bool(inference_status.get("ready", False)),
                "inference_error": inference_status.get("error"),
                "worker_api_auth": runtime.worker_api_auth,
            },
            "worker_api_auth": runtime.worker_api_auth,
            "telemetry_version": 2,
            "current": backend.current_model_info(),
            "model_count": len(models),
            "model_ids": [str(item["id"]) for item in models],
            "metrics": telemetry.snapshot(),
        }
        power_probe = getattr(telemetry, "power_integrity", None)
        power_integrity = power_probe() if callable(power_probe) else None
        if power_integrity is not None:
            # Additive Pi-only research context. It never changes inference or
            # telemetry readiness above, and non-Pi Workers keep their legacy
            # payload shape until a later Controller normalization phase.
            response["power_integrity"] = power_integrity
        return response

    @app.get("/cluster/models")
    async def cluster_models() -> Dict[str, Any]:
        return {"ok": True, "node": runtime.node_name, "models": backend.model_inventory()}

    @app.post("/cluster/models/verify")
    async def verify_model(payload: VerifyModelRequest) -> Dict[str, Any]:
        try:
            model = backend.verify_model(payload.model_id, payload.expected_sha256 or None)
        except Exception as exc:
            failure = failure_from_exception(exc, stage="model_verify", model_id=payload.model_id)
            raise HTTPException(
                status_code=http_status_for_failure(failure),
                detail=str(exc),
                headers={"X-Cluster-Error-Code": failure.code.value},
            ) from exc
        return {"ok": True, "node": runtime.node_name, "model": model}

    @app.post("/cluster/models/delete")
    async def delete_model(payload: DeleteModelRequest) -> Dict[str, Any]:
        try:
            model = backend.delete_model(payload.model_id)
        except Exception as exc:
            failure = failure_from_exception(exc, stage="model_delete", model_id=payload.model_id)
            raise HTTPException(
                status_code=http_status_for_failure(failure),
                detail=str(exc),
                headers={"X-Cluster-Error-Code": failure.code.value},
            ) from exc
        return {"ok": True, "node": runtime.node_name, "model": model}

    @app.post("/cluster/models/install")
    async def install_model(payload: InstallModelRequest) -> Dict[str, Any]:
        try:
            if payload.metadata:
                model = backend.install_model(
                    payload.model_id, payload.source_url, payload.expected_sha256, payload.metadata
                )
            else:
                # Preserve the Phase 05 custom backend contract for callers
                # that have not adopted additive install provenance metadata.
                model = backend.install_model(payload.model_id, payload.source_url, payload.expected_sha256)
        except Exception as exc:
            failure = failure_from_exception(exc, stage="model_install", model_id=payload.model_id)
            raise HTTPException(
                status_code=http_status_for_failure(failure),
                detail=str(exc),
                headers={"X-Cluster-Error-Code": failure.code.value},
            ) from exc
        return {"ok": True, "node": runtime.node_name, "model": model}

    @app.post("/cluster/chat/stream")
    async def cluster_chat_stream(payload: ClusterChatRequest) -> StreamingResponse:
        return stream_response(payload, seed=payload.seed)


__all__ = ["WorkerRuntimeInfo", "as_sse", "mount_worker_routes"]
