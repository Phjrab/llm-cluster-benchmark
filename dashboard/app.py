#!/usr/bin/env python3
"""FastAPI control plane for the Jetson head/worker LLM benchmark cluster."""

from __future__ import annotations

import concurrent.futures
import csv
import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from cluster.benchmark.runner import (
    DEFAULT_RESULTS_DIR,
    ExperimentConfig,
    run_experiment,
)
from cluster.clusterctl import (
    DEFAULT_INVENTORY,
    Node,
    load_nodes,
    request_json,
    run_on_node,
    select_nodes,
)


DASHBOARD_DIR = Path(__file__).resolve().parent
CLUSTER_DIR = DASHBOARD_DIR.parent
PROJECT_ROOT = CLUSTER_DIR.parent
RUNTIME_DIR = PROJECT_ROOT / ".run" / "cluster"
INVENTORY_PATH = Path(os.getenv("CLUSTER_INVENTORY", DEFAULT_INVENTORY))
RESULTS_DIR = Path(os.getenv("CLUSTER_RESULTS_DIR", DEFAULT_RESULTS_DIR))
DEFAULTS_PATH = CLUSTER_DIR / "config" / "experiment_defaults.json"
EXAMPLE_INVENTORY = CLUSTER_DIR / "config" / "nodes.example.csv"
TOKEN_PATH = RUNTIME_DIR / "dashboard.token"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_runtime() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not INVENTORY_PATH.exists():
        INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE_INVENTORY, INVENTORY_PATH)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
        TOKEN_PATH.chmod(0o600)


ensure_runtime()
DASHBOARD_TOKEN = TOKEN_PATH.read_text(encoding="utf-8").strip()


def verify_token(request: Request) -> None:
    supplied = request.headers.get("X-Cluster-Token") or request.query_params.get("token", "")
    if not supplied or not secrets.compare_digest(supplied, DASHBOARD_TOKEN):
        raise HTTPException(status_code=401, detail="Dashboard access token is missing or invalid")


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[queue.Queue[Dict[str, Any]]] = []

    def publish(self, event_type: str, **payload: Any) -> None:
        event = {"type": event_type, "at": utc_now(), **payload}
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    def stream(self) -> Generator[str, None, None]:
        subscriber: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(subscriber)
        try:
            yield f"data: {json.dumps({'type': 'connected', 'at': utc_now()})}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)


events = EventBus()


class NodePayload(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    role: str = "worker"
    host: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=64)
    ssh_port: int = Field(22, ge=1, le=65535)
    api_port: int = Field(8000, ge=1, le=65535)
    project_dir: str = Field(min_length=2, max_length=512)
    enabled: bool = True
    identity_file: str = Field("", max_length=512)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"head", "worker"}:
            raise ValueError("role must be head or worker")
        return value

    @field_validator("project_dir")
    @classmethod
    def validate_project_dir(cls, value: str) -> str:
        if not value.startswith("/") or ".." in Path(value).parts:
            raise ValueError("project_dir must be a safe absolute path")
        return value


class ActionPayload(BaseModel):
    action: str
    node_names: List[str] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class ExperimentPayload(BaseModel):
    name: str = "cluster-load-test"
    node_names: List[str]
    model_id: str
    n_ctx: int = Field(1024, ge=128, le=4096)
    n_gpu_layers: int = Field(30, ge=0, le=120)
    requests: int = Field(20, ge=1, le=10_000)
    concurrency: int = Field(4, ge=1, le=256)
    max_tokens: int = Field(128, ge=1, le=1024)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    seed: int = Field(42, ge=-1, le=2_147_483_647)
    warmup_requests: int = Field(1, ge=0, le=10)
    prompt: str = Field(min_length=1, max_length=20_000)
    require_uniform_config: bool = True


inventory_lock = threading.RLock()


def read_all_nodes() -> List[Node]:
    with inventory_lock:
        return load_nodes(INVENTORY_PATH, include_disabled=True)


def write_all_nodes(nodes: Sequence[Node]) -> None:
    enabled_heads = [node for node in nodes if node.role == "head" and node.enabled]
    if len(enabled_heads) != 1:
        raise ValueError("Exactly one enabled head node is required")
    if len({node.name for node in nodes}) != len(nodes):
        raise ValueError("Node names must be unique")
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = INVENTORY_PATH.with_suffix(f".tmp.{uuid.uuid4().hex}")
    fieldnames = [
        "name",
        "role",
        "host",
        "user",
        "ssh_port",
        "api_port",
        "project_dir",
        "enabled",
        "identity_file",
    ]
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for node in nodes:
            row = asdict(node)
            row["enabled"] = "true" if node.enabled else "false"
            writer.writerow(row)
    os.replace(temporary, INVENTORY_PATH)


def serialize_node(node: Node) -> Dict[str, Any]:
    item = asdict(node)
    item["api_url"] = node.api_url
    return item


def list_models() -> List[Dict[str, Any]]:
    root = PROJECT_ROOT / "models"
    models = []
    if root.exists():
        for path in sorted(root.rglob("*.gguf")):
            models.append(
                {
                    "id": path.relative_to(root).as_posix(),
                    "name": path.name,
                    "size_gb": round(path.stat().st_size / (1024**3), 2),
                }
            )
    return models


def probe_node(node: Node) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        **serialize_node(node),
        "ssh": False,
        "project": False,
        "api": False,
        "current": {},
        "metrics": {},
        "model_count": 0,
        "model_ids": [],
        "node_info": {},
        "error": "",
        "checked_at": utc_now(),
    }
    if not node.enabled:
        result["error"] = "disabled"
        return result
    try:
        check = run_on_node(node, ["test", "-d", node.project_dir], timeout=8)
        result["ssh"] = check.returncode == 0
        result["project"] = check.returncode == 0
        if check.returncode != 0:
            result["error"] = (check.stderr or "project directory missing").strip()
    except Exception as exc:
        result["error"] = f"SSH: {exc}"
    try:
        health = request_json(f"{node.api_url}/cluster/health", timeout=4.0)
        result["api"] = health.get("ok") is True
        result["current"] = health.get("current") or {}
        result["metrics"] = health.get("metrics") or {}
        result["model_count"] = int(health.get("model_count") or 0)
        result["model_ids"] = health.get("model_ids") or []
        result["node_info"] = health.get("node") or {}
    except Exception as exc:
        if not result["error"]:
            result["error"] = f"API: {exc}"
    return result


class StatusMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="cluster-status", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh_now(self) -> None:
        try:
            nodes = read_all_nodes()
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(nodes))) as executor:
                snapshot = list(executor.map(probe_node, nodes))
            with self._lock:
                changed = snapshot != self._snapshot
                self._snapshot = snapshot
            events.publish("cluster_status", nodes=snapshot, changed=changed)
        except Exception as exc:
            events.publish("monitor_error", message=str(exc))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_now()
            self._stop.wait(5.0)

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._snapshot))


status_monitor = StatusMonitor()


class ActionManager:
    ALLOWED = {"doctor", "setup", "prepare", "sync-code", "sync-models", "start", "stop", "select-model"}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._actions: Dict[str, Dict[str, Any]] = {}

    def start(self, payload: ActionPayload) -> Dict[str, Any]:
        if payload.action not in self.ALLOWED:
            raise ValueError(f"Unsupported action: {payload.action}")
        enabled = load_nodes(INVENTORY_PATH)
        selected = select_nodes(enabled, payload.node_names)
        if not selected:
            raise ValueError("Select at least one enabled node")
        action_id = datetime.now().strftime("%H%M%S") + "_" + uuid.uuid4().hex[:6]
        record = {
            "id": action_id,
            "action": payload.action,
            "nodes": [node.name for node in selected],
            "status": "queued",
            "started_at": utc_now(),
            "finished_at": None,
            "exit_code": None,
            "log": [],
        }
        with self._lock:
            self._actions[action_id] = record
        thread = threading.Thread(
            target=self._run,
            args=(action_id, payload),
            name=f"cluster-action-{action_id}",
            daemon=True,
        )
        thread.start()
        return dict(record)

    def _run(self, action_id: str, payload: ActionPayload) -> None:
        command = [
            sys.executable,
            "-m",
            "cluster.clusterctl",
            "--inventory",
            str(INVENTORY_PATH),
        ]
        for node_name in payload.node_names:
            command.extend(["--node", node_name])
        command.append(payload.action)
        if payload.action in {"sync-models", "prepare"}:
            for model in payload.options.get("models", []):
                command.extend(["--model", str(model)])
        elif payload.action == "select-model":
            command.extend(
                [
                    "--model-id",
                    str(payload.options.get("model_id", "")),
                    "--n-ctx",
                    str(payload.options.get("n_ctx", 1024)),
                    "--n-gpu-layers",
                    str(payload.options.get("n_gpu_layers", 30)),
                ]
            )

        with self._lock:
            self._actions[action_id]["status"] = "running"
        events.publish("action_started", action=self.get(action_id))
        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                clean = line.rstrip()
                with self._lock:
                    log = self._actions[action_id]["log"]
                    log.append(clean)
                    if len(log) > 500:
                        del log[:-500]
                events.publish("action_log", action_id=action_id, line=clean)
            exit_code = process.wait()
            with self._lock:
                record = self._actions[action_id]
                record["exit_code"] = exit_code
                record["status"] = "completed" if exit_code == 0 else "failed"
                record["finished_at"] = utc_now()
        except Exception as exc:
            with self._lock:
                record = self._actions[action_id]
                record["status"] = "failed"
                record["finished_at"] = utc_now()
                record["log"].append(str(exc))
        events.publish("action_finished", action=self.get(action_id))
        status_monitor.refresh_now()

    def get(self, action_id: str) -> Dict[str, Any]:
        with self._lock:
            if action_id not in self._actions:
                raise KeyError(action_id)
            return json.loads(json.dumps(self._actions[action_id]))

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            values = list(self._actions.values())
        return json.loads(json.dumps(values[-20:][::-1]))


actions = ActionManager()


def read_run_summaries(limit: int = 30) -> List[Dict[str, Any]]:
    summaries = []
    if RESULTS_DIR.exists():
        paths = sorted(RESULTS_DIR.glob("*/summary.json"), reverse=True)
        for path in paths[:limit]:
            try:
                summaries.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return summaries


class ExperimentManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Optional[Dict[str, Any]] = None
        self._cancel: Optional[threading.Event] = None

    def start(self, payload: ExperimentPayload) -> Dict[str, Any]:
        config = ExperimentConfig.from_dict(payload.model_dump())
        config.validate()
        with self._lock:
            if self._active and self._active.get("status") in {"queued", "running"}:
                raise ValueError("Another experiment is already running")
            client_id = "pending_" + uuid.uuid4().hex[:8]
            self._active = {
                "id": client_id,
                "name": config.name,
                "status": "queued",
                "phase": "queued",
                "completed": 0,
                "total": config.requests,
                "started_at": utc_now(),
                "nodes": config.node_names,
                "latest": None,
                "error": "",
            }
            self._cancel = threading.Event()
            record = dict(self._active)
        thread = threading.Thread(
            target=self._run,
            args=(config,),
            name="cluster-experiment",
            daemon=True,
        )
        thread.start()
        return record

    def _handle_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            if self._active is None:
                return
            if event.get("run_id"):
                self._active["id"] = event["run_id"]
            event_type = event.get("type")
            if event_type == "run_started":
                self._active["status"] = "running"
            elif event_type == "phase":
                self._active["phase"] = event.get("phase")
            elif event_type == "request_completed":
                self._active["completed"] = event.get("completed", 0)
                self._active["latest"] = event.get("result")
            elif event_type == "run_finished":
                self._active["status"] = event.get("summary", {}).get("status", "completed")
                self._active["summary"] = event.get("summary")
            elif event_type == "run_failed":
                self._active["status"] = "failed"
                self._active["error"] = event.get("error", "")
        events.publish("experiment_event", event=event, active=self.active())

    def _run(self, config: ExperimentConfig) -> None:
        try:
            summary = run_experiment(
                config,
                inventory_path=INVENTORY_PATH,
                results_root=RESULTS_DIR,
                progress=self._handle_event,
                cancel_event=self._cancel,
            )
            with self._lock:
                if self._active:
                    self._active["summary"] = summary
                    self._active["status"] = summary.get("status", "completed")
        except Exception as exc:
            with self._lock:
                if self._active:
                    self._active["status"] = "failed"
                    self._active["error"] = str(exc)
            events.publish("experiment_failed", message=str(exc), active=self.active())
        finally:
            status_monitor.refresh_now()

    def cancel(self) -> Dict[str, Any]:
        with self._lock:
            if not self._active or self._active.get("status") not in {"queued", "running"}:
                raise ValueError("No running experiment")
            assert self._cancel is not None
            self._cancel.set()
            self._active["phase"] = "cancelling"
            return json.loads(json.dumps(self._active))

    def active(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._active)) if self._active else None


experiments = ExperimentManager()


app = FastAPI(title="MediFlow LLM Cluster Lab", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR / "static")), name="cluster-static")
templates = Jinja2Templates(directory=str(DASHBOARD_DIR / "templates"))
status_monitor.start()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/dashboard/health")
async def dashboard_health() -> Dict[str, Any]:
    return {"ok": True, "service": "cluster-dashboard"}


@app.get("/api/bootstrap", dependencies=[Depends(verify_token)])
async def bootstrap() -> Dict[str, Any]:
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    public_key = ""
    public_key_path = Path.home() / ".ssh" / "id_ed25519_llm_cluster.pub"
    if public_key_path.exists():
        public_key = public_key_path.read_text(encoding="utf-8").strip()
    return {
        "nodes": [serialize_node(node) for node in read_all_nodes()],
        "status": status_monitor.snapshot(),
        "models": list_models(),
        "defaults": defaults,
        "active_experiment": experiments.active(),
        "runs": read_run_summaries(),
        "actions": actions.list(),
        "onboarding": {
            "public_key": public_key,
            "identity_file": str(Path.home() / ".ssh" / "id_ed25519_llm_cluster"),
        },
    }


@app.get("/api/events", dependencies=[Depends(verify_token)])
async def event_stream() -> StreamingResponse:
    return StreamingResponse(
        events.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/status", dependencies=[Depends(verify_token)])
async def get_status() -> Dict[str, Any]:
    return {"nodes": status_monitor.snapshot(), "at": utc_now()}


@app.post("/api/status/refresh", dependencies=[Depends(verify_token)])
async def refresh_status() -> Dict[str, Any]:
    threading.Thread(target=status_monitor.refresh_now, daemon=True).start()
    return {"ok": True}


@app.post("/api/nodes", dependencies=[Depends(verify_token)])
async def upsert_node(payload: NodePayload) -> Dict[str, Any]:
    node = Node(**payload.model_dump())
    with inventory_lock:
        nodes = read_all_nodes()
        existing_index = next((i for i, item in enumerate(nodes) if item.name == node.name), None)
        if existing_index is None:
            if node.role == "head":
                raise HTTPException(status_code=400, detail="A head node already exists")
            nodes.append(node)
        else:
            existing = nodes[existing_index]
            if existing.role == "head" and node.role != "head":
                raise HTTPException(status_code=400, detail="The head role cannot be changed")
            nodes[existing_index] = node
        try:
            write_all_nodes(nodes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    events.publish("inventory_changed", nodes=[serialize_node(item) for item in nodes])
    threading.Thread(target=status_monitor.refresh_now, daemon=True).start()
    return {"ok": True, "node": serialize_node(node)}


@app.delete("/api/nodes/{node_name}", dependencies=[Depends(verify_token)])
async def delete_node(node_name: str) -> Dict[str, Any]:
    with inventory_lock:
        nodes = read_all_nodes()
        target = next((node for node in nodes if node.name == node_name), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Node not found")
        if target.role == "head":
            raise HTTPException(status_code=400, detail="The head node cannot be removed")
        nodes = [node for node in nodes if node.name != node_name]
        write_all_nodes(nodes)
    events.publish("inventory_changed", nodes=[serialize_node(item) for item in nodes])
    return {"ok": True}


@app.post("/api/actions", dependencies=[Depends(verify_token)])
async def start_action(payload: ActionPayload) -> Dict[str, Any]:
    if payload.action in {"setup", "prepare"} and payload.options.get("confirmed") is not True:
        raise HTTPException(status_code=400, detail="Worker setup requires explicit confirmation")
    try:
        record = actions.start(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "action": record}


@app.get("/api/actions", dependencies=[Depends(verify_token)])
async def list_actions() -> Dict[str, Any]:
    return {"actions": actions.list()}


@app.post("/api/experiments", dependencies=[Depends(verify_token)])
async def start_experiment(payload: ExperimentPayload) -> Dict[str, Any]:
    try:
        active = experiments.start(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "experiment": active}


@app.get("/api/experiments", dependencies=[Depends(verify_token)])
async def list_experiments() -> Dict[str, Any]:
    return {"active": experiments.active(), "runs": read_run_summaries()}


@app.post("/api/experiments/cancel", dependencies=[Depends(verify_token)])
async def cancel_experiment() -> Dict[str, Any]:
    try:
        active = experiments.cancel()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "experiment": active}


@app.get("/api/runs/{run_id}", dependencies=[Depends(verify_token)])
async def get_run(run_id: str) -> Dict[str, Any]:
    if not run_id.replace("_", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid run id")
    summary_path = RESULTS_DIR / run_id / "summary.json"
    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Run not found")
    return json.loads(summary_path.read_text(encoding="utf-8"))


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})
