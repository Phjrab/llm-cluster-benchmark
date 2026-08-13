#!/usr/bin/env python3
"""Cluster worker API layered on top of the existing local LLM web app."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import psutil
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import Field

from web.app import ChatStreamRequest, app, as_sse, manager

try:
    from jtop import jtop
except ImportError:  # pragma: no cover
    jtop = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE_NAME = os.getenv("CLUSTER_NODE_NAME", socket.gethostname())
NODE_ROLE = os.getenv("CLUSTER_NODE_ROLE", "worker")


def _git_commit() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "-C", str(PROJECT_ROOT), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_optional(value: Any, digits: int = 2) -> Optional[float]:
    number = _safe_float(value)
    return round(number, digits) if number is not None else None


class JetsonMetricsSampler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._snapshot: Dict[str, Any] = {}
        self._error: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jtop-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        if jtop is None:
            with self._lock:
                self._error = "jtop is not installed"
            return
        while not self._stop.is_set():
            try:
                with jtop(interval=1.0) as jetson:
                    while not self._stop.is_set() and jetson.ok():
                        stats = dict(jetson.stats)
                        cpu_values = [
                            _safe_float(value)
                            for key, value in stats.items()
                            if key.startswith("CPU")
                        ]
                        cpu_values = [value for value in cpu_values if value is not None]
                        snapshot = {
                            "sampled_at": datetime.now(timezone.utc).isoformat(),
                            "cpu_pct": round(sum(cpu_values) / len(cpu_values), 2)
                            if cpu_values
                            else None,
                            "ram_pct": _round_optional(stats.get("RAM", 0) * 100),
                            "swap_pct": _round_optional(stats.get("SWAP", 0) * 100),
                            "gpu_pct": _round_optional(stats.get("GPU")),
                            "power_w": _round_optional(
                                (_safe_float(stats.get("Power TOT")) or 0) / 1000
                            ),
                            "cpu_temp_c": _round_optional(stats.get("Temp cpu")),
                            "gpu_temp_c": _round_optional(stats.get("Temp gpu")),
                            "fan_pct": _round_optional(stats.get("Fan pwmfan0")),
                            "power_mode": stats.get("nvp model"),
                            "jetson_clocks": stats.get("jetson_clocks"),
                        }
                        with self._lock:
                            self._snapshot = snapshot
                            self._error = None
            except Exception as exc:  # pragma: no cover - hardware-specific
                with self._lock:
                    self._error = str(exc)
                self._stop.wait(3.0)

    def snapshot(self) -> Dict[str, Any]:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage(str(PROJECT_ROOT))
        with self._lock:
            sampled = dict(self._snapshot)
            error = self._error
        sampled.update(
            {
                "ram_used_mb": round(memory.used / (1024 * 1024), 2),
                "ram_available_mb": round(memory.available / (1024 * 1024), 2),
                "swap_used_mb": round(swap.used / (1024 * 1024), 2),
                "disk_free_gb": round(disk.free / (1024**3), 2),
                "load_1m": round(os.getloadavg()[0], 2),
            }
        )
        if error:
            sampled["sampler_error"] = error
        return sampled


sampler = JetsonMetricsSampler()
sampler.start()


class ClusterChatRequest(ChatStreamRequest):
    seed: int = Field(42, ge=-1, le=2_147_483_647)


@app.get("/cluster/health")
async def cluster_health() -> Dict[str, Any]:
    models = manager.list_models()
    return {
        "ok": True,
        "node": {
            "name": NODE_NAME,
            "role": NODE_ROLE,
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "git_commit": _git_commit(),
        },
        "current": manager.current_model_info(),
        "model_count": len(models),
        "model_ids": [str(item["id"]) for item in models],
        "metrics": sampler.snapshot(),
    }


@app.get("/cluster/models")
async def cluster_models() -> Dict[str, Any]:
    return {
        "ok": True,
        "node": NODE_NAME,
        "models": manager.list_models(),
    }


@app.post("/cluster/chat/stream")
async def cluster_chat_stream(payload: ClusterChatRequest) -> StreamingResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is empty")

    def event_generator() -> Iterable[str]:
        started = time.perf_counter()
        first_token_at: Optional[float] = None
        pieces: List[str] = []
        chunks = 0
        try:
            with manager.lock:
                set_seed = getattr(manager.llm, "set_seed", None)
                if callable(set_seed):
                    set_seed(payload.seed)
                for token in manager.stream_chat(
                    message=message,
                    history=payload.history,
                    max_tokens=payload.max_tokens,
                    temperature=payload.temperature,
                    top_p=payload.top_p,
                ):
                    now = time.perf_counter()
                    if first_token_at is None:
                        first_token_at = now
                    pieces.append(token)
                    chunks += 1
                    yield as_sse("token", {"text": token})

            finished = time.perf_counter()
            text = "".join(pieces)
            token_count = chunks
            tokenizer = getattr(manager.llm, "tokenize", None)
            if callable(tokenizer) and text:
                try:
                    token_count = len(tokenizer(text.encode("utf-8"), add_bos=False))
                except Exception:
                    pass

            ttft_s = (first_token_at - started) if first_token_at else finished - started
            generation_s = max(finished - (first_token_at or finished), 0.0)
            yield as_sse(
                "done",
                {
                    "metrics": {
                        "ttft_s": round(ttft_s, 6),
                        "generation_s": round(generation_s, 6),
                        "e2e_s": round(finished - started, 6),
                        "generated_tokens": token_count,
                        "stream_chunks": chunks,
                        "output_chars": len(text),
                    }
                },
            )
        except Exception as exc:
            yield as_sse("error", {"message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
