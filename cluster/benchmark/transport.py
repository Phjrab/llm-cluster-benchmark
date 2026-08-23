"""HTTP streaming boundary used by the benchmark executor."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cluster.clusterctl import worker_auth_headers
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.errors import ErrorCode
from cluster.domain.failures import failure_from_message
from cluster.infrastructure.sse import parse_sse_events

from .models import RequestTask


class _CountingIterator:
    """Count streamed HTTP body bytes without changing SSE parsing."""

    def __init__(self, source: Any) -> None:
        self._iterator = iter(source)
        self.bytes_read = 0

    def __iter__(self) -> "_CountingIterator":
        return self

    def __next__(self) -> bytes:
        value = next(self._iterator)
        self.bytes_read += len(value)
        return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stream_worker_request(
    node: Any, config: ExperimentConfig, task: RequestTask, warmup: bool = False
) -> Dict[str, Any]:
    payload = {
        "message": config.prompt,
        "history": [],
        "max_tokens": min(config.max_tokens, 16) if warmup else config.max_tokens,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "seed": config.seed,
    }
    request_body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{node.api_url}/cluster/chat/stream",
        data=request_body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream", **worker_auth_headers()},
        method="POST",
    )
    started_wall = utc_now()
    started = time.perf_counter()
    connection_setup_s: Optional[float] = None
    bytes_received = 0
    first_token_at: Optional[float] = None
    output_parts: List[str] = []
    server_metrics: Dict[str, Any] = {}
    error = ""
    ok = False
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_s) as response:
            connection_setup_s = time.perf_counter() - started
            counted = _CountingIterator(response)
            for event in parse_sse_events(counted):
                event_type = event.get("type")
                if event_type == "token":
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                    output_parts.append(str(event.get("text", "")))
                elif event_type == "done":
                    server_metrics = event.get("metrics") or {}
                    ok = True
                elif event_type == "error":
                    error = str(event.get("message", "worker error"))
            bytes_received = counted.bytes_read
    except (OSError, ValueError, urllib.error.URLError) as exc:
        error = str(exc)
    finished = time.perf_counter()
    ttft_s = (first_token_at - started) if first_token_at else None
    e2e_s = finished - started
    generated_tokens = int(server_metrics.get("generated_tokens") or 0)
    generation_s = float(server_metrics.get("generation_s") or 0.0)
    output = "".join(output_parts)
    input_tokens = server_metrics.get("input_tokens")
    total_tokens = server_metrics.get("total_tokens")
    bandwidth = (
        (len(request_body) + bytes_received) / e2e_s if e2e_s > 0 else None
    )
    failure = failure_from_message(error, stage="inference", node=node.name, model_id=config.model_id) if error else None
    return {
        "request_id": task.request_id,
        "logical_request_id": task.logical_request_id,
        "scenario_id": task.scenario_id,
        "replica_index": task.replica_index,
        "node": node.name,
        "assigned_node": node.name,
        "node_host": node.host,
        "started_at": started_wall,
        "ok": ok,
        "ttft_s": round(ttft_s, 6) if ttft_s is not None else None,
        "e2e_s": round(e2e_s, 6),
        "server_ttft_s": server_metrics.get("ttft_s"),
        "server_generation_s": server_metrics.get("generation_s"),
        "generated_tokens": generated_tokens,
        "tokens_per_s": round(generated_tokens / generation_s, 6) if generation_s > 0 else None,
        "output_chars": len(output),
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest() if ok else "",
        "response": output,
        "error": error,
        "error_code": failure.code.value if failure else "",
        "failure": failure.to_dict() if failure else None,
        "warmup": warmup,
        "monotonic_started_s": round(started, 9),
        "monotonic_finished_s": round(finished, 9),
        "input_tokens": input_tokens,
        "input_token_source": server_metrics.get("input_token_source"),
        "input_tokens_exact": server_metrics.get("input_tokens_exact") is True,
        "prefill_time_s": server_metrics.get("prefill_time_s"),
        "prefill_time_source": server_metrics.get("prefill_time_source"),
        "prefill_tokens_per_s": server_metrics.get("prefill_tokens_per_s"),
        "decode_time_s": server_metrics.get("decode_time_s"),
        "decode_time_source": server_metrics.get("decode_time_source"),
        "decode_tokens_per_s": server_metrics.get("decode_tokens_per_s"),
        "total_tokens": total_tokens,
        "connection_setup_s": round(connection_setup_s, 9) if connection_setup_s is not None else None,
        "rtt_s": None,
        "bytes_sent": len(request_body),
        "bytes_received": bytes_received,
        "effective_bandwidth_bytes_s": round(bandwidth, 6) if bandwidth is not None else None,
        "coordinator_wait_s": None,
    }


def stream_rpc_request(
    coordinator: Any, coordinator_url: str, config: ExperimentConfig, task: RequestTask
) -> Dict[str, Any]:
    payload = {
        "messages": [{"role": "user", "content": config.prompt}],
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "seed": config.seed,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request_body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{coordinator_url}/v1/chat/completions",
        data=request_body,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started_wall = utc_now()
    started = time.perf_counter()
    connection_setup_s: Optional[float] = None
    bytes_received = 0
    first_token_at: Optional[float] = None
    output_parts: List[str] = []
    generated_tokens = 0
    input_tokens: Optional[int] = None
    error = ""
    error_code = ""
    ok = False
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_s) as response:
            connection_setup_s = time.perf_counter() - started
            for raw_line in response:
                bytes_received += len(raw_line)
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                raw_event = line[6:]
                if raw_event == "[DONE]":
                    ok = True
                    continue
                event = json.loads(raw_event)
                usage = event.get("usage") or {}
                if usage.get("prompt_tokens") is not None:
                    input_tokens = int(usage["prompt_tokens"])
                if usage.get("completion_tokens") is not None:
                    generated_tokens = int(usage["completion_tokens"])
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    token = str(delta.get("content") or choices[0].get("text") or "")
                    if token:
                        if first_token_at is None:
                            first_token_at = time.perf_counter()
                        output_parts.append(token)
                    if choices[0].get("finish_reason") is not None:
                        ok = True
    except (OSError, ValueError, urllib.error.URLError) as exc:
        error = str(exc)
        error_code = ErrorCode.RPC_CONNECTION_FAILED.value
    finished = time.perf_counter()
    output = "".join(output_parts)
    if ok and generated_tokens <= 0:
        generated_tokens = len(output_parts)
    generation_s = finished - (first_token_at or started)
    ttft_s = (first_token_at - started) if first_token_at else None
    total_tokens = input_tokens + generated_tokens if input_tokens is not None else None
    decode_tokens = max(generated_tokens - (1 if first_token_at else 0), 0)
    e2e_s = finished - started
    bandwidth = (
        (len(request_body) + bytes_received) / e2e_s if e2e_s > 0 else None
    )
    failure = failure_from_message(error, stage="rpc_inference", node=coordinator.name, model_id=config.model_id, fallback=ErrorCode.RPC_CONNECTION_FAILED) if error else None
    return {
        "request_id": task.request_id,
        "logical_request_id": task.logical_request_id,
        "scenario_id": task.scenario_id,
        "replica_index": task.replica_index,
        "node": coordinator.name,
        "assigned_node": coordinator.name,
        "node_host": coordinator.host,
        "started_at": started_wall,
        "ok": ok,
        "ttft_s": round(ttft_s, 6) if ttft_s is not None else None,
        "e2e_s": round(e2e_s, 6),
        "server_ttft_s": None,
        "server_generation_s": round(generation_s, 6),
        "generated_tokens": generated_tokens,
        "tokens_per_s": round(generated_tokens / generation_s, 6) if generation_s > 0 else None,
        "output_chars": len(output),
        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest() if ok else "",
        "response": output,
        "error": error,
        "error_code": failure.code.value if failure else error_code,
        "failure": failure.to_dict() if failure else None,
        "warmup": False,
        "token_count_source": "server_usage" if generated_tokens and generated_tokens != len(output_parts) else "stream_chunk_estimate",
        "monotonic_started_s": round(started, 9),
        "monotonic_finished_s": round(finished, 9),
        "input_tokens": input_tokens,
        "input_token_source": "llama_server_usage" if input_tokens is not None else "server_not_reported",
        "input_tokens_exact": input_tokens is not None,
        "prefill_time_s": round(ttft_s, 6) if ttft_s is not None else None,
        "prefill_time_source": "controller_client_ttft_proxy",
        "prefill_tokens_per_s": (
            round(input_tokens / ttft_s, 6)
            if input_tokens is not None and ttft_s and ttft_s > 0 else None
        ),
        "decode_time_s": round(generation_s, 6),
        "decode_time_source": "controller_stream_after_first_token",
        "decode_tokens_per_s": (
            round(decode_tokens / generation_s, 6) if generation_s > 0 else None
        ),
        "total_tokens": total_tokens,
        "connection_setup_s": round(connection_setup_s, 9) if connection_setup_s is not None else None,
        "rtt_s": None,
        "bytes_sent": len(request_body),
        "bytes_received": bytes_received,
        "effective_bandwidth_bytes_s": round(bandwidth, 6) if bandwidth is not None else None,
        "coordinator_wait_s": None,
    }


__all__ = ["stream_rpc_request", "stream_worker_request", "utc_now"]
