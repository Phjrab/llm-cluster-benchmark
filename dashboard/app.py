#!/usr/bin/env python3
"""FastAPI control plane for the Jetson head/worker LLM benchmark cluster."""

from __future__ import annotations

import asyncio
import concurrent.futures
import csv
import ipaddress
import json
import os
import queue
import re
import secrets
import socket
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
    experiment_strategy_catalog,
    run_experiment,
    strategy_work_units,
    validate_strategy,
)
from cluster.clusterctl import (
    DEFAULT_INVENTORY,
    Node,
    discover_node,
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
EXPERIMENTS_DIR = RUNTIME_DIR / "experiments"
DEFAULTS_PATH = CLUSTER_DIR / "config" / "experiment_defaults.json"
EXAMPLE_INVENTORY = CLUSTER_DIR / "config" / "nodes.example.csv"
TOKEN_PATH = RUNTIME_DIR / "dashboard.token"
SETTINGS_PATH = RUNTIME_DIR / "settings.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_runtime() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    if not INVENTORY_PATH.exists():
        raise RuntimeError(
            f"Cluster inventory is missing: {INVENTORY_PATH}. "
            "Run ./cluster/setup_head.sh before starting the dashboard."
        )
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(24) + "\n", encoding="utf-8")
        TOKEN_PATH.chmod(0o600)
    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text('{\n  "worker_api_auth": false\n}\n', encoding="utf-8")
        SETTINGS_PATH.chmod(0o600)


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
    host: str = Field(min_length=1, max_length=45)
    user: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_][a-zA-Z0-9_-]*$")
    ssh_port: int = Field(22, ge=1, le=65535)
    api_port: int = Field(8000, ge=1, le=65535)
    project_dir: str = Field(min_length=2, max_length=512)
    enabled: bool = True
    identity_file: str = Field("", max_length=512)
    platform: str = "auto"

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
        if (
            not value.startswith(("/home/", "/opt/", "/srv/"))
            or ".." in Path(value).parts
            or not re.fullmatch(r"/[a-zA-Z0-9._/-]+", value)
        ):
            raise ValueError("project_dir must be a safe absolute path")
        return value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("host must be a private IPv4 address") from exc
        allowed = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("127.0.0.0/8"),
        )
        if address.version != 4 or not any(address in network for network in allowed):
            raise ValueError("host must belong to the head node's private LAN")
        return str(address)

    @field_validator("identity_file")
    @classmethod
    def validate_identity_file(cls, value: str) -> str:
        if value:
            raise ValueError("identity_file is managed by the head node")
        return ""

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"auto", "jetson", "raspberry-pi"}:
            raise ValueError("platform must be auto, jetson or raspberry-pi")
        return value


class ActionPayload(BaseModel):
    action: str
    node_names: List[str] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class ExperimentPayload(BaseModel):
    experiment_id: str = Field("", max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$|^$")
    name: str = "cluster-load-test"
    node_names: List[str] = Field(min_length=1, max_length=4)
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
    execution_strategy: str = "replicated_round_robin"
    sweep_mode: str = "cumulative"
    rpc_split_mode: str = "layer"
    rpc_split_policy: str = "auto"
    rpc_tensor_split: List[float] = Field(default_factory=list, max_length=4)
    acknowledge_experimental_rpc: bool = False


class ClusterSettingsPayload(BaseModel):
    worker_api_auth: bool = False


inventory_lock = threading.RLock()
settings_lock = threading.RLock()


def read_settings() -> Dict[str, Any]:
    with settings_lock:
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        return {"worker_api_auth": bool(raw.get("worker_api_auth", False))}


def write_settings(settings: Dict[str, Any]) -> None:
    with settings_lock:
        temporary = SETTINGS_PATH.with_suffix(f".tmp.{uuid.uuid4().hex}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, SETTINGS_PATH)


def read_all_nodes() -> List[Node]:
    with inventory_lock:
        return load_nodes(INVENTORY_PATH, include_disabled=True)


def write_all_nodes(nodes: Sequence[Node]) -> None:
    enabled_heads = [node for node in nodes if node.role == "head" and node.enabled]
    if len(enabled_heads) != 1:
        raise ValueError("Exactly one enabled head node is required")
    if len({node.name for node in nodes}) != len(nodes):
        raise ValueError("Node names must be unique")
    endpoints = [(node.host, node.ssh_port) for node in nodes]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("Each physical host and SSH port can be registered only once")
    if sum(1 for node in nodes if node.enabled) > 4:
        raise ValueError("At most four nodes can be enabled in one cluster")
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
        "platform",
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
    item.pop("identity_file", None)
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
        "profile": {},
        "capabilities": {},
        "error": "",
        "checked_at": utc_now(),
    }
    if not node.enabled:
        result["error"] = "disabled"
        return result
    try:
        health = request_json(f"{node.api_url}/cluster/health", timeout=4.0)
        reported = health.get("node") or {}
        result["api"] = (
            health.get("ok") is True
            and int(health.get("telemetry_version") or 0) >= 2
            and reported.get("name") == node.name
        )
        result["current"] = health.get("current") or {}
        result["metrics"] = health.get("metrics") or {}
        result["model_count"] = int(health.get("model_count") or 0)
        result["model_ids"] = health.get("model_ids") or []
        result["node_info"] = reported
        result["profile"] = health.get("profile") or {}
        result["capabilities"] = health.get("capabilities") or {}
        result["telemetry_version"] = health.get("telemetry_version")
        if not result["api"]:
            raise ValueError("worker API identity or telemetry schema mismatch")
        result["ssh"] = True
        result["project"] = True
    except Exception as exc:
        discovery = discover_node(node, timeout=8)
        result["ssh"] = discovery["ssh"]
        result["project"] = discovery["project"]
        result["discovery"] = discovery
        if not discovery["ssh"]:
            result["error"] = "SSH key authentication failed or the host is unreachable"
        elif not discovery["project"]:
            result["error"] = "SSH connected; project is not installed yet"
        else:
            result["error"] = f"Worker API is offline: {exc}"
    return result


def _private_scan_networks() -> List[Dict[str, str]]:
    try:
        process = subprocess.run(
            ["ip", "-j", "-4", "addr", "show", "up", "scope", "global"],
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        interfaces = json.loads(process.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    allowed = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    found: List[Dict[str, str]] = []
    seen = set()
    for interface in interfaces:
        interface_name = str(interface.get("ifname", ""))
        if interface_name.startswith(("docker", "br-", "veth", "virbr", "tailscale")):
            continue
        for info in interface.get("addr_info", []):
            raw = info.get("local", "")
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if address.version != 4 or not any(address in network for network in allowed):
                continue
            prefix = max(int(info.get("prefixlen", 24)), 24)
            network = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
            key = str(network)
            if key not in seen:
                seen.add(key)
                found.append({"interface": interface_name, "local_ip": str(address), "network": key})
    return found


def _port_open(host: str, port: int = 22) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.22):
            return True
    except OSError:
        return False


def _ssh_fingerprint(host: str, port: int = 22) -> str:
    try:
        scan = subprocess.run(
            ["ssh-keyscan", "-T", "2", "-p", str(port), host],
            text=True,
            capture_output=True,
            timeout=4,
        )
        key_line = next((line for line in scan.stdout.splitlines() if line and not line.startswith("#")), "")
        if not key_line:
            return ""
        fingerprint = subprocess.run(
            ["ssh-keygen", "-lf", "-"],
            input=key_line + "\n",
            text=True,
            capture_output=True,
            timeout=3,
        )
        return fingerprint.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


scan_lock = threading.Lock()
scan_cache: Dict[str, Any] = {"at": 0.0, "result": None}


def scan_lan_devices(force: bool = False) -> Dict[str, Any]:
    with scan_lock:
        if not force and scan_cache["result"] is not None and time.monotonic() - scan_cache["at"] < 15:
            return json.loads(json.dumps(scan_cache["result"]))
    networks = _private_scan_networks()
    candidates: List[str] = []
    local_ips = {item["local_ip"] for item in networks}
    for item in networks:
        network = ipaddress.ip_network(item["network"])
        candidates.extend(str(address) for address in network.hosts())
    candidates = sorted(set(candidates), key=lambda value: tuple(int(part) for part in value.split(".")))
    open_hosts: List[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(64, max(1, len(candidates)))) as executor:
        futures = {executor.submit(_port_open, host): host for host in candidates}
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                open_hosts.append(futures[future])
    existing = {node.host: node for node in read_all_nodes()}
    head_node = next((node for node in read_all_nodes() if node.role == "head"), None)
    devices = []
    for host in sorted(open_hosts, key=lambda value: tuple(int(part) for part in value.split("."))):
        known = existing.get(host) or (head_node if host in local_ips else None)
        devices.append(
            {
                "host": host,
                "ssh_port": 22,
                "fingerprint": _ssh_fingerprint(host),
                "known_node": known.name if known else "",
                "is_head": host in local_ips,
            }
        )
    result = {"networks": networks, "devices": devices, "scanned_at": utc_now()}
    with scan_lock:
        scan_cache["at"] = time.monotonic()
        scan_cache["result"] = result
    return json.loads(json.dumps(result))


def probe_candidate(node: Node) -> Dict[str, Any]:
    discovery = discover_node(node, timeout=20)
    configured = node.platform
    detected = discovery.get("platform_kind", "unknown")
    warnings: List[str] = []
    if configured != "auto" and discovery["ssh"] and configured != detected:
        warnings.append(f"configured platform {configured} differs from detected {detected}")
    if discovery["ssh"] and detected == "raspberry-pi" and discovery.get("architecture") not in {"aarch64", "arm64"}:
        warnings.append("Raspberry Pi requires a 64-bit OS")
    if discovery["ssh"] and not discovery.get("sudo_nopasswd") and discovery.get("missing_packages"):
        warnings.append("system dependencies require one manual sudo command")
    compatible = (
        discovery["ssh"]
        and detected in {"jetson", "raspberry-pi"}
        and discovery.get("architecture") in {"aarch64", "arm64"}
    )
    if discovery["ssh"] and detected not in {"jetson", "raspberry-pi"}:
        warnings.append("only NVIDIA Jetson and Raspberry Pi are supported")
    if discovery["ssh"] and discovery.get("architecture") not in {"aarch64", "arm64"}:
        warnings.append("a 64-bit ARM operating system is required")
    return {
        "ok": compatible,
        "ssh_ok": discovery["ssh"],
        "stage": "ready_to_register" if compatible else "incompatible" if discovery["ssh"] else "pairing_required",
        "node": serialize_node(node),
        "fingerprint": _ssh_fingerprint(node.host, node.ssh_port),
        "discovery": discovery,
        "warnings": warnings,
    }


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
    ALLOWED = {"doctor", "setup", "prepare", "prepare-rpc", "sync-code", "sync-models", "start", "stop", "restart", "select-model"}

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
        selected_names = {node.name for node in selected}
        experiment = experiments.active() if "experiments" in globals() else None
        if experiment and experiment.get("status") in {"queued", "running"}:
            overlap = selected_names.intersection(experiment.get("nodes") or [])
            if overlap:
                raise ValueError("Nodes are busy with an experiment: " + ", ".join(sorted(overlap)))
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
            for action in self._actions.values():
                if action.get("status") in {"queued", "running"} and selected_names.intersection(action.get("nodes") or []):
                    raise ValueError("A selected node already has a running control action")
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
        elif payload.action == "prepare-rpc":
            pass
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

    def busy_nodes(self) -> List[str]:
        with self._lock:
            return sorted(
                {
                    node
                    for action in self._actions.values()
                    if action.get("status") in {"queued", "running"}
                    for node in action.get("nodes", [])
                }
            )


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


def _experiment_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "experiment"


experiment_catalog_lock = threading.RLock()


def save_experiment_definition(payload: ExperimentPayload) -> Dict[str, Any]:
    with experiment_catalog_lock:
        experiment_id = payload.experiment_id
        if not experiment_id:
            experiment_id = f"{_experiment_slug(payload.name)}-{uuid.uuid4().hex[:6]}"
        path = EXPERIMENTS_DIR / f"{experiment_id}.json"
        existing: Dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
        if existing:
            previous_strategy = (existing.get("default_config") or {}).get(
                "execution_strategy", "replicated_round_robin"
            )
            if previous_strategy != payload.execution_strategy:
                raise ValueError(
                    "한 실험 묶음에는 하나의 실행 방식만 사용할 수 있습니다. 새 실험 묶음을 만드세요."
                )
        now = utc_now()
        definition = {
            "experiment_id": experiment_id,
            "name": payload.name,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "archived": False,
            "default_config": {
                key: value
                for key, value in payload.model_dump().items()
                if key not in {"experiment_id", "name"}
            },
        }
        temporary = path.with_suffix(f".tmp.{uuid.uuid4().hex}")
        temporary.write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return definition


def read_experiment_groups() -> List[Dict[str, Any]]:
    definitions: Dict[str, Dict[str, Any]] = {}
    with experiment_catalog_lock:
        for path in sorted(EXPERIMENTS_DIR.glob("*.json")):
            try:
                definition = json.loads(path.read_text(encoding="utf-8"))
                definitions[definition["experiment_id"]] = definition
            except (OSError, ValueError, KeyError):
                continue
    for run in read_run_summaries(limit=500):
        experiment_id = run.get("experiment_id")
        if not experiment_id:
            experiment_id = f"legacy-{_experiment_slug(str(run.get('name') or 'unnamed'))}"
        group = definitions.setdefault(
            experiment_id,
            {
                "experiment_id": experiment_id,
                "name": run.get("name") or experiment_id,
                "created_at": run.get("started_at") or run.get("finished_at"),
                "updated_at": run.get("finished_at"),
                "archived": False,
                "default_config": {},
                "legacy": not bool(run.get("experiment_id")),
            },
        )
        group.setdefault("runs", []).append(run)
        if str(run.get("finished_at", "")) > str(group.get("updated_at", "")):
            group["updated_at"] = run.get("finished_at")
    groups = []
    for definition in definitions.values():
        runs = sorted(definition.get("runs", []), key=lambda item: item.get("finished_at", ""), reverse=True)
        definition = {**definition, "runs": runs, "run_count": len(runs)}
        definition["latest_run"] = runs[0] if runs else None
        groups.append(definition)
    return sorted(groups, key=lambda item: item.get("updated_at") or "", reverse=True)


class ExperimentManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Optional[Dict[str, Any]] = None
        self._cancel: Optional[threading.Event] = None

    def start(self, payload: ExperimentPayload) -> Dict[str, Any]:
        config = ExperimentConfig.from_dict(payload.model_dump())
        config.validate()
        selected = select_nodes(load_nodes(INVENTORY_PATH), config.node_names)
        validate_strategy(selected, config)
        with self._lock:
            if self._active and self._active.get("status") in {"queued", "running"}:
                raise ValueError("Another experiment is already running")
            client_id = "pending_" + uuid.uuid4().hex[:8]
            self._active = {
                "id": client_id,
                "experiment_id": config.experiment_id,
                "name": config.name,
                "status": "queued",
                "phase": "queued",
                "completed": 0,
                "total": strategy_work_units(config, len(selected)),
                "strategy": config.execution_strategy,
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
        "experiment_groups": read_experiment_groups(),
        "actions": actions.list(),
        "settings": read_settings(),
        "experiment_strategies": experiment_strategy_catalog(),
        "onboarding": {
            "public_key": public_key,
        },
    }


@app.get("/api/events", dependencies=[Depends(verify_token)])
async def event_stream() -> StreamingResponse:
    return StreamingResponse(
        events.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/settings", dependencies=[Depends(verify_token)])
async def get_settings() -> Dict[str, Any]:
    return {"settings": read_settings()}


@app.put("/api/settings", dependencies=[Depends(verify_token)])
async def update_settings(payload: ClusterSettingsPayload) -> Dict[str, Any]:
    previous = read_settings()
    updated = payload.model_dump()
    write_settings(updated)
    action: Optional[Dict[str, Any]] = None
    if previous != updated:
        try:
            enabled_names = [node.name for node in load_nodes(INVENTORY_PATH)]
            action = actions.start(
                ActionPayload(action="restart", node_names=enabled_names, options={})
            )
        except ValueError as exc:
            write_settings(previous)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    events.publish("settings_changed", settings=updated, action=action)
    return {"ok": True, "settings": updated, "action": action}


@app.get("/api/status", dependencies=[Depends(verify_token)])
async def get_status() -> Dict[str, Any]:
    return {"nodes": status_monitor.snapshot(), "at": utc_now()}


@app.post("/api/status/refresh", dependencies=[Depends(verify_token)])
async def refresh_status() -> Dict[str, Any]:
    threading.Thread(target=status_monitor.refresh_now, daemon=True).start()
    return {"ok": True}


@app.post("/api/network/scan", dependencies=[Depends(verify_token)])
async def scan_network(force: bool = False) -> Dict[str, Any]:
    return await asyncio.to_thread(scan_lan_devices, force)


@app.post("/api/nodes/probe", dependencies=[Depends(verify_token)])
async def probe_unregistered_node(payload: NodePayload) -> Dict[str, Any]:
    if payload.role != "worker":
        raise HTTPException(status_code=400, detail="Only worker candidates can be probed")
    node = Node(**payload.model_dump())
    return await asyncio.to_thread(probe_candidate, node)


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
    if payload.action in {"setup", "prepare", "prepare-rpc"} and payload.options.get("confirmed") is not True:
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
        current = experiments.active()
        if current and current.get("status") in {"queued", "running"}:
            raise ValueError("Another experiment is already running")
        busy = set(actions.busy_nodes()).intersection(payload.node_names)
        if busy:
            raise ValueError("Nodes have a running control action: " + ", ".join(sorted(busy)))
        status_by_name = {item.get("name"): item for item in status_monitor.snapshot()}
        inventory_by_name = {item.name: item for item in read_all_nodes()}
        selected_nodes = [inventory_by_name[name] for name in payload.node_names if name in inventory_by_name]
        strategy_config = ExperimentConfig.from_dict(payload.model_dump())
        validate_strategy(selected_nodes, strategy_config)
        if payload.execution_strategy == "model_parallel_rpc" and read_settings()["worker_api_auth"]:
            raise ValueError(
                "워커 API 보안 모드에서는 인증 없는 llama.cpp RPC 포트를 열지 않습니다. "
                "SSH 터널 모드가 추가되기 전에는 신뢰 LAN에서만 보안을 끄고 실행하세요."
            )
        pi_nodes = []
        for name in payload.node_names:
            detected = (status_by_name.get(name, {}).get("profile") or {}).get("platform_kind")
            configured = inventory_by_name.get(name).platform if inventory_by_name.get(name) else "auto"
            if detected == "raspberry-pi" or configured == "raspberry-pi":
                pi_nodes.append(name)
        if pi_nodes and payload.n_gpu_layers != 0 and payload.execution_strategy != "model_parallel_rpc":
            raise ValueError(
                "Raspberry Pi nodes require n_gpu_layers=0: " + ", ".join(str(item) for item in pi_nodes)
            )
        definition = save_experiment_definition(payload)
        linked_payload = payload.model_copy(update={"experiment_id": definition["experiment_id"]})
        active = experiments.start(linked_payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "experiment": active, "definition": definition}


@app.get("/api/experiments", dependencies=[Depends(verify_token)])
async def list_experiments() -> Dict[str, Any]:
    return {
        "active": experiments.active(),
        "runs": read_run_summaries(),
        "experiment_groups": read_experiment_groups(),
    }


@app.get("/api/experiment-groups", dependencies=[Depends(verify_token)])
async def list_experiment_groups() -> Dict[str, Any]:
    return {"experiment_groups": read_experiment_groups()}


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
