#!/usr/bin/env python3
"""Legacy local chat UI over the repository-local Worker inference backend.

This module remains a compatibility UI.  The Worker runtime no longer imports
it or mutates its FastAPI application.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from cluster.worker.inference import (
    DEFAULT_N_BATCH,
    DEFAULT_MAX_TOKENS,
    DEFAULT_N_CTX,
    DEFAULT_N_GPU_LAYERS,
    DEFAULT_N_THREADS,
    LegacyWebInferenceBackend,
    LlamaCppInferenceBackend,
)
from cluster.worker.routes import as_sse
from cluster.worker.schemas import ChatStreamRequest, SelectModelRequest


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
MODELS_DIR = Path(os.getenv("LLM_MODELS_DIR", PROJECT_ROOT / "models")).resolve()

# Public legacy aliases retained for callers that imported web.app directly.
ModelManager = LlamaCppInferenceBackend
manager = LegacyWebInferenceBackend(MODELS_DIR)
app = FastAPI(title="Jetson Local LLM Chat", version="1.0.0")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"default_n_ctx": DEFAULT_N_CTX, "default_n_gpu_layers": DEFAULT_N_GPU_LAYERS},
    )


@app.get("/api/models")
async def get_models() -> Dict[str, object]:
    return {"models": manager.list_models(), "current": manager.current_model_info(), "models_dir": str(MODELS_DIR)}


@app.post("/api/select-model")
async def select_model(payload: SelectModelRequest) -> Dict[str, object]:
    try:
        info = manager.load_model(payload.model_id, payload.n_ctx, payload.n_gpu_layers)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load model: {exc}") from exc
    return {"ok": True, "current": info}


@app.post("/api/unload-model")
async def unload_model() -> Dict[str, object]:
    manager.unload_model()
    return {"ok": True, "current": manager.current_model_info()}


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatStreamRequest) -> StreamingResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")

    def events() -> Iterable[str]:
        try:
            for token in manager.stream_chat(
                message=message,
                history=payload.history,
                max_tokens=payload.max_tokens,
                temperature=payload.temperature,
                top_p=payload.top_p,
            ):
                yield as_sse("token", {"text": token})
            yield as_sse("done", {})
        except Exception as exc:
            yield as_sse("error", {"message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.get("/health")
async def health() -> Dict[str, object]:
    return {"ok": True, "current": manager.current_model_info()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.app:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")), reload=False)
