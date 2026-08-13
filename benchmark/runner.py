#!/usr/bin/env python3
"""Run reproducible streaming LLM load experiments across selected nodes."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import math
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from cluster.clusterctl import (
    DEFAULT_INVENTORY,
    Node,
    load_nodes,
    request_json,
    select_nodes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = PROJECT_ROOT / ".run" / "cluster" / "results"
ProgressCallback = Callable[[Dict[str, Any]], None]


@dataclass
class ExperimentConfig:
    name: str = "cluster-load-test"
    node_names: List[str] = field(default_factory=list)
    model_id: str = "qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    n_ctx: int = 1024
    n_gpu_layers: int = 30
    requests: int = 20
    concurrency: int = 4
    max_tokens: int = 128
    temperature: float = 0.0
    top_p: float = 0.9
    seed: int = 42
    warmup_requests: int = 1
    prompt: str = "엣지 장치에서 의료 LLM을 실행할 때의 장점과 한계를 한 문단으로 설명해줘."
    require_uniform_config: bool = True
    request_timeout_s: float = 600.0

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ExperimentConfig":
        known = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in known})

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Experiment name cannot be empty")
        if not self.node_names:
            raise ValueError("Select at least one node")
        if not self.model_id.endswith(".gguf") or self.model_id.startswith("/") or ".." in Path(self.model_id).parts:
            raise ValueError("model_id must be a safe relative GGUF path")
        if not 128 <= self.n_ctx <= 4096:
            raise ValueError("n_ctx must be between 128 and 4096")
        if not 0 <= self.n_gpu_layers <= 120:
            raise ValueError("n_gpu_layers must be between 0 and 120")
        if not 1 <= self.requests <= 10_000:
            raise ValueError("requests must be between 1 and 10000")
        if not 1 <= self.concurrency <= 256:
            raise ValueError("concurrency must be between 1 and 256")
        if not 1 <= self.max_tokens <= 1024:
            raise ValueError("max_tokens must be between 1 and 1024")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if not 0.0 <= self.top_p <= 1.0:
            raise ValueError("top_p must be between 0 and 1")
        if not 0 <= self.warmup_requests <= 10:
            raise ValueError("warmup_requests must be between 0 and 10")
        if not self.prompt.strip():
            raise ValueError("prompt cannot be empty")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _emit(callback: Optional[ProgressCallback], event_type: str, **payload: Any) -> None:
    if callback:
        callback({"type": event_type, "at": utc_now(), **payload})


def _stream_request(node: Node, config: ExperimentConfig, request_id: int, warmup: bool = False) -> Dict[str, Any]:
    payload = {
        "message": config.prompt,
        "history": [],
        "max_tokens": min(config.max_tokens, 16) if warmup else config.max_tokens,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "seed": config.seed,
    }
    request = urllib.request.Request(
        f"{node.api_url}/cluster/chat/stream",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started_wall = utc_now()
    started = time.perf_counter()
    first_token_at: Optional[float] = None
    output_parts: List[str] = []
    server_metrics: Dict[str, Any] = {}
    error = ""
    ok = False
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_s) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
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
    except (OSError, ValueError, urllib.error.URLError) as exc:
        error = str(exc)
    finished = time.perf_counter()
    ttft_s = (first_token_at - started) if first_token_at else None
    e2e_s = finished - started
    generated_tokens = int(server_metrics.get("generated_tokens") or 0)
    generation_s = float(server_metrics.get("generation_s") or 0.0)
    return {
        "request_id": request_id,
        "node": node.name,
        "node_host": node.host,
        "started_at": started_wall,
        "ok": ok,
        "ttft_s": round(ttft_s, 6) if ttft_s is not None else None,
        "e2e_s": round(e2e_s, 6),
        "server_ttft_s": server_metrics.get("ttft_s"),
        "server_generation_s": server_metrics.get("generation_s"),
        "generated_tokens": generated_tokens,
        "tokens_per_s": round(generated_tokens / generation_s, 6) if generation_s > 0 else None,
        "output_chars": len("".join(output_parts)),
        "error": error,
        "warmup": warmup,
    }


def _load_model(node: Node, config: ExperimentConfig) -> Dict[str, Any]:
    result = request_json(
        f"{node.api_url}/api/select-model",
        method="POST",
        payload={
            "model_id": config.model_id,
            "n_ctx": config.n_ctx,
            "n_gpu_layers": config.n_gpu_layers,
        },
        timeout=900.0,
    )
    current = result.get("current") or {}
    if result.get("ok") is not True:
        raise RuntimeError(f"{node.name} rejected model selection")
    return {"node": node.name, **current}


def _validate_uniform(loaded: Sequence[Dict[str, Any]], config: ExperimentConfig) -> List[str]:
    warnings: List[str] = []
    keys = ("model_id", "n_ctx", "n_gpu_layers", "n_batch")
    for key in keys:
        values = {str(item.get(key)) for item in loaded}
        if len(values) > 1:
            warnings.append(f"nodes differ in actual {key}: {', '.join(sorted(values))}")
    for item in loaded:
        if item.get("n_ctx") != config.n_ctx:
            warnings.append(f"{item['node']} adjusted n_ctx to {item.get('n_ctx')}")
        if item.get("n_gpu_layers") != config.n_gpu_layers:
            warnings.append(
                f"{item['node']} adjusted n_gpu_layers to {item.get('n_gpu_layers')}"
            )
    return warnings


def _aggregate(records: Sequence[Dict[str, Any]], wall_s: float) -> Dict[str, Any]:
    successful = [item for item in records if item["ok"]]
    ttft = [float(item["ttft_s"]) for item in successful if item["ttft_s"] is not None]
    e2e = [float(item["e2e_s"]) for item in successful]
    total_tokens = sum(int(item["generated_tokens"]) for item in successful)
    per_node: Dict[str, Dict[str, Any]] = {}
    for item in records:
        bucket = per_node.setdefault(
            item["node"],
            {"requests": 0, "successful": 0, "tokens": 0, "e2e_s": []},
        )
        bucket["requests"] += 1
        if item["ok"]:
            bucket["successful"] += 1
            bucket["tokens"] += int(item["generated_tokens"])
            bucket["e2e_s"].append(float(item["e2e_s"]))
    for bucket in per_node.values():
        bucket["e2e_p50_s"] = percentile(bucket.pop("e2e_s"), 0.50)
    return {
        "requests": len(records),
        "successful": len(successful),
        "failed": len(records) - len(successful),
        "success_rate": round(len(successful) / len(records), 6) if records else 0.0,
        "wall_s": round(wall_s, 6),
        "requests_per_s": round(len(successful) / wall_s, 6) if wall_s > 0 else 0.0,
        "total_generated_tokens": total_tokens,
        "cluster_tokens_per_s": round(total_tokens / wall_s, 6) if wall_s > 0 else 0.0,
        "ttft_p50_s": percentile(ttft, 0.50),
        "ttft_p95_s": percentile(ttft, 0.95),
        "e2e_p50_s": percentile(e2e, 0.50),
        "e2e_p95_s": percentile(e2e, 0.95),
        "per_node": per_node,
    }


def run_experiment(
    config: ExperimentConfig,
    inventory_path: Path = DEFAULT_INVENTORY,
    results_root: Path = DEFAULT_RESULTS_DIR,
    progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    config.validate()
    cancel_event = cancel_event or threading.Event()
    all_nodes = load_nodes(inventory_path)
    nodes = select_nodes(all_nodes, config.node_names)
    if len(nodes) != len(config.node_names):
        raise ValueError("Some selected nodes are unavailable")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "config.json").write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    events_path = run_dir / "events.jsonl"
    events_lock = threading.Lock()

    def emit(event_type: str, **payload: Any) -> None:
        event = {"type": event_type, "at": utc_now(), "run_id": run_id, **payload}
        with events_lock:
            with events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if progress:
            progress(event)

    emit("run_started", config=asdict(config), nodes=[node.name for node in nodes])
    loaded: List[Dict[str, Any]] = []
    try:
        emit("phase", phase="loading_model", message="선택한 노드에 모델을 로드하는 중")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
            futures = {executor.submit(_load_model, node, config): node for node in nodes}
            for future in concurrent.futures.as_completed(futures):
                node = futures[future]
                try:
                    info = future.result()
                    loaded.append(info)
                    emit("node_model_loaded", node=node.name, actual=info)
                except Exception as exc:
                    emit("node_error", node=node.name, error=str(exc))
                    raise RuntimeError(f"Failed to load model on {node.name}: {exc}") from exc

        warnings = _validate_uniform(loaded, config)
        for warning in warnings:
            emit("warning", message=warning)
        if warnings and config.require_uniform_config:
            raise RuntimeError("Uniform configuration check failed: " + "; ".join(warnings))

        if config.warmup_requests:
            emit("phase", phase="warmup", message="노드별 워밍업 실행 중")
            warmup_jobs = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(nodes)) as executor:
                for node in nodes:
                    for warmup_index in range(config.warmup_requests):
                        warmup_jobs.append(
                            executor.submit(_stream_request, node, config, warmup_index, True)
                        )
                for future in concurrent.futures.as_completed(warmup_jobs):
                    result = future.result()
                    if not result["ok"]:
                        raise RuntimeError(
                            f"Warmup failed on {result['node']}: {result['error']}"
                        )

        if cancel_event.is_set():
            raise RuntimeError("Experiment cancelled before measurement")

        emit("phase", phase="measurement", message="분산 부하 측정 중")
        records: List[Dict[str, Any]] = []
        wall_started = time.perf_counter()
        max_workers = min(config.concurrency, config.requests)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for request_index in range(config.requests):
                if cancel_event.is_set():
                    break
                node = nodes[request_index % len(nodes)]
                future = executor.submit(_stream_request, node, config, request_index + 1)
                futures[future] = (request_index + 1, node)

            for future in concurrent.futures.as_completed(futures):
                request_id, node = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "request_id": request_id,
                        "node": node.name,
                        "node_host": node.host,
                        "started_at": utc_now(),
                        "ok": False,
                        "ttft_s": None,
                        "e2e_s": 0.0,
                        "server_ttft_s": None,
                        "server_generation_s": None,
                        "generated_tokens": 0,
                        "tokens_per_s": None,
                        "output_chars": 0,
                        "error": str(exc),
                        "warmup": False,
                    }
                records.append(result)
                emit(
                    "request_completed",
                    completed=len(records),
                    total=config.requests,
                    result=result,
                )
        wall_s = time.perf_counter() - wall_started
        records.sort(key=lambda item: item["request_id"])
        summary = _aggregate(records, wall_s)
        summary.update(
            {
                "run_id": run_id,
                "name": config.name,
                "status": "cancelled" if cancel_event.is_set() else "completed",
                "started_at": json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])["at"],
                "finished_at": utc_now(),
                "nodes": [node.name for node in nodes],
                "actual_model_config": loaded,
                "warnings": warnings,
                "result_dir": str(run_dir),
            }
        )

        fieldnames = list(records[0].keys()) if records else []
        if fieldnames:
            with (run_dir / "requests.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(records)
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        emit("run_finished", summary=summary)
        return summary
    except Exception as exc:
        failure = {
            "run_id": run_id,
            "name": config.name,
            "status": "failed",
            "finished_at": utc_now(),
            "nodes": [node.name for node in nodes],
            "actual_model_config": loaded,
            "error": str(exc),
            "result_dir": str(run_dir),
        }
        (run_dir / "summary.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        emit("run_failed", error=str(exc), summary=failure)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
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
