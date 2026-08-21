#!/usr/bin/env python3
"""Dashboard controller services and legacy-compatible state adapters.

This module contains controller-side business orchestration only. FastAPI
wiring lives in :mod:`cluster.dashboard.app`; route adapters live in
:mod:`cluster.dashboard.routes`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
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

import psutil

from cluster.dashboard.schemas import ActionPayload, ExperimentPayload, NodePayload

from cluster.application.jobs import JobService, NONTERMINAL_JOB_STATES
from cluster.application.model_service import (
    ModelPreflightError,
    WorkerModelInventory,
    aggregate_catalog,
    parse_worker_inventory,
    validate_model_preflight,
)
from cluster.application.suite_runner import suite_document, suite_model_records
from cluster.domain.events import ClusterEvent, EventChannel
from cluster.domain.identifiers import validate_node_id
from cluster.benchmark.runner import (
    ExperimentConfig,
    experiment_strategy_catalog,
    normalize_model_ids,
    strategy_work_units,
    validate_strategy,
)
from cluster.benchmark.rpc_selection import select_rpc_coordinator
from cluster.clusterctl import (
    DEFAULT_IDENTITY,
    Node,
    discover_node,
    load_nodes,
    request_json,
    run_on_node,
    select_nodes,
)
from cluster.integrations.runtime_layout import resolve_runtime_paths
from cluster.domain.errors import ClusterError, ErrorCode
from cluster.domain.failures import http_status_for_failure
from cluster.domain.model import ModelCatalogEntry, recommend_models
from cluster.infrastructure.storage import (
    FilesystemEnvironmentReportRepository,
    FilesystemExperimentRepository,
    FilesystemInventoryRepository,
    FilesystemRunRepository,
    FilesystemSettingsRepository,
    FilesystemSuiteRepository,
    StorageCorruptionError,
)


RUNTIME_PATHS = resolve_runtime_paths()
PROJECT_LAYOUT = RUNTIME_PATHS.layout
PROJECT_ROOT = PROJECT_LAYOUT.root
CLUSTER_DIR = PROJECT_LAYOUT.cluster_dir
DASHBOARD_DIR = CLUSTER_DIR / "dashboard"
RUNTIME_DIR = RUNTIME_PATHS.runtime_dir
INVENTORY_PATH = RUNTIME_PATHS.inventory_path
RESULTS_DIR = RUNTIME_PATHS.results_dir
EXPERIMENTS_DIR = RUNTIME_PATHS.experiments_dir
DEFAULTS_PATH = CLUSTER_DIR / "config" / "experiment_defaults.json"
MODEL_CATALOG_PATH = CLUSTER_DIR / "config" / "model_catalog.json"
MODEL_CATALOG_CACHE_PATH = RUNTIME_DIR / "model_catalog.cache.json"
EXAMPLE_INVENTORY = CLUSTER_DIR / "config" / "nodes.example.csv"
TOKEN_PATH = RUNTIME_PATHS.dashboard_token_path
SETTINGS_PATH = RUNTIME_PATHS.settings_path
ENVIRONMENT_DIR = RUNTIME_PATHS.environment_dir
JOBS_DIR = RUNTIME_PATHS.jobs_dir
ENVIRONMENT_MARKER = "CLUSTER_ENVIRONMENT_JSON="
MODEL_PROGRESS_MARKER = "CLUSTER_MODEL_PROGRESS_JSON="
PRIVATE_RUN_ARTIFACTS = frozenset(
    {"config.json", "events.jsonl", "requests.csv", "responses.jsonl", "summary.json"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _controller_public_key_info() -> Dict[str, Any]:
    """Expose only a validated Controller public key, never the private key."""
    public_key_path = DEFAULT_IDENTITY.with_suffix(".pub")
    if not public_key_path.is_file() or public_key_path.is_symlink():
        return {"public_key": "", "key_status": "missing"}
    try:
        public_key = public_key_path.read_text(encoding="utf-8").strip()
    except OSError:
        return {"public_key": "", "key_status": "unreadable"}
    parts = public_key.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or not re.fullmatch(r"[A-Za-z0-9+/=]+", parts[1]):
        return {"public_key": "", "key_status": "invalid"}
    return {"public_key": public_key, "key_status": "ready"}


def _validate_controller_identity_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    try:
        metadata = path.stat()
    except OSError as exc:
        raise DashboardServiceError(409, "Controller SSH key cannot be inspected") from exc
    if path.is_symlink() or not path.is_file() or metadata.st_uid != os.getuid():
        raise DashboardServiceError(409, "Controller SSH key path is not safe to use")


def ensure_controller_ssh_identity() -> Dict[str, Any]:
    """Create the dedicated Controller identity only after an explicit UI request."""
    identity_path = DEFAULT_IDENTITY
    public_key_path = identity_path.with_suffix(".pub")
    ssh_dir = identity_path.parent
    if ssh_dir.exists() and (ssh_dir.is_symlink() or not ssh_dir.is_dir()):
        raise DashboardServiceError(409, "Controller SSH directory is not safe to use")
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    _validate_controller_identity_file(identity_path)
    _validate_controller_identity_file(public_key_path)
    if (public_key_path.exists() or public_key_path.is_symlink()) and not identity_path.exists():
        raise DashboardServiceError(
            409,
            "Controller public key exists without its private key; create or select a new dedicated identity manually",
        )
    if not identity_path.exists():
        try:
            generated = subprocess.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    f"llm-cluster-controller@{socket.gethostname()}",
                    "-f",
                    str(identity_path),
                ],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DashboardServiceError(500, "Controller SSH key generation failed") from exc
        if generated.returncode != 0:
            raise DashboardServiceError(500, "Controller SSH key generation failed")
    elif not public_key_path.exists() and not public_key_path.is_symlink():
        try:
            derived = subprocess.run(
                ["ssh-keygen", "-y", "-f", str(identity_path)],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DashboardServiceError(500, "Controller SSH public key recovery failed") from exc
        if derived.returncode != 0 or not derived.stdout.strip():
            raise DashboardServiceError(500, "Controller SSH public key recovery failed")
        public_key_path.write_text(derived.stdout.strip() + "\n", encoding="utf-8")
    identity_path.chmod(0o600)
    public_key_path.chmod(0o644)
    info = _controller_public_key_info()
    if info["key_status"] != "ready":
        raise DashboardServiceError(500, "Controller SSH key validation failed")
    return info


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _chmod_private_file(path: Path) -> None:
    if path.is_file() and not path.is_symlink():
        path.chmod(0o600)


def _tighten_existing_runtime_permissions() -> None:
    """Migrate only known private runtime artifacts without following symlinks."""
    for path in (
        INVENTORY_PATH,
        TOKEN_PATH,
        SETTINGS_PATH,
        MODEL_CATALOG_CACHE_PATH,
    ):
        _chmod_private_file(path)

    for directory in (EXPERIMENTS_DIR, ENVIRONMENT_DIR, JOBS_DIR):
        for path in directory.iterdir():
            _chmod_private_file(path)

    suites_dir = RESULTS_DIR / "_suites"
    for path in suites_dir.iterdir():
        _chmod_private_file(path)

    for run_dir in RESULTS_DIR.iterdir():
        if run_dir == suites_dir or run_dir.is_symlink() or not run_dir.is_dir():
            continue
        run_dir.chmod(0o700)
        for name in PRIVATE_RUN_ARTIFACTS:
            _chmod_private_file(run_dir / name)


def ensure_runtime() -> None:
    for directory in (
        RUNTIME_DIR,
        RESULTS_DIR,
        RESULTS_DIR / "_suites",
        EXPERIMENTS_DIR,
        ENVIRONMENT_DIR,
        JOBS_DIR,
    ):
        _ensure_private_directory(directory)
    if not INVENTORY_PATH.exists():
        # A Controller can start before its first inference worker is enrolled.
        # Do not create a synthetic legacy head: the Controller is not a worker.
        FilesystemInventoryRepository(INVENTORY_PATH).write_rows([])
    try:
        token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = ""
    if not token:
        temporary = TOKEN_PATH.with_suffix(f".tmp.{uuid.uuid4().hex}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(24) + "\n")
        os.replace(temporary, TOKEN_PATH)
    TOKEN_PATH.chmod(0o600)
    if not SETTINGS_PATH.exists():
        FilesystemSettingsRepository(SETTINGS_PATH).write(
            {"worker_api_auth": False, "dashboard_token_auth": False}
        )
    SETTINGS_PATH.chmod(0o600)
    _tighten_existing_runtime_permissions()


ensure_runtime()
DASHBOARD_TOKEN = TOKEN_PATH.read_text(encoding="utf-8").strip()


def dashboard_token_is_valid(supplied: str) -> bool:
    return bool(supplied) and secrets.compare_digest(supplied, DASHBOARD_TOKEN)


class EventBus:
    def __init__(self, subscriber_maxsize: int = 100) -> None:
        self._lock = threading.Lock()
        self._subscribers: List[queue.Queue[Dict[str, Any]]] = []
        self._subscriber_maxsize = subscriber_maxsize

    def publish(
        self, event_type: str, *, channel: EventChannel = EventChannel.SYSTEM, **payload: Any
    ) -> None:
        event = ClusterEvent.create(channel, event_type, utc_now(), **payload).to_dict()
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

    def stream(self, supplied_token: str = "") -> Generator[str, None, None]:
        subscriber: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=self._subscriber_maxsize)
        with self._lock:
            self._subscribers.append(subscriber)
        try:
            yield f"data: {json.dumps(ClusterEvent.create(EventChannel.SYSTEM, 'connected', utc_now()).to_dict())}\n\n"
            while True:
                try:
                    event = subscriber.get(timeout=15.0)
                    if (
                        read_settings()["dashboard_token_auth"]
                        and not dashboard_token_is_valid(supplied_token)
                    ):
                        yield f"data: {json.dumps(ClusterEvent.create(EventChannel.SYSTEM, 'auth_required', utc_now()).to_dict())}\n\n"
                        return
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    if (
                        read_settings()["dashboard_token_auth"]
                        and not dashboard_token_is_valid(supplied_token)
                    ):
                        yield f"data: {json.dumps(ClusterEvent.create(EventChannel.SYSTEM, 'auth_required', utc_now()).to_dict())}\n\n"
                        return
                    yield ": keepalive\n\n"
        finally:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)


events = EventBus()


inventory_lock = threading.RLock()
settings_lock = threading.RLock()


def _settings_repository() -> FilesystemSettingsRepository:
    return FilesystemSettingsRepository(SETTINGS_PATH)


def _inventory_repository() -> FilesystemInventoryRepository:
    return FilesystemInventoryRepository(INVENTORY_PATH)


def _environment_repository() -> FilesystemEnvironmentReportRepository:
    return FilesystemEnvironmentReportRepository(ENVIRONMENT_DIR)


def _experiment_repository() -> FilesystemExperimentRepository:
    return FilesystemExperimentRepository(EXPERIMENTS_DIR)


def _run_repository() -> FilesystemRunRepository:
    return FilesystemRunRepository(RESULTS_DIR)


def _suite_repository() -> FilesystemSuiteRepository:
    return FilesystemSuiteRepository(RESULTS_DIR / "_suites")


def read_settings() -> Dict[str, Any]:
    with settings_lock:
        try:
            raw = _settings_repository().read()
        except FileNotFoundError:
            raw = {}
        except (OSError, StorageCorruptionError):
            # A damaged existing settings file must not silently disable a
            # dashboard protection that may previously have been enabled.
            return {"worker_api_auth": True, "dashboard_token_auth": True}
        worker_value = raw.get("worker_api_auth", False)
        dashboard_value = raw.get("dashboard_token_auth", False)
        return {
            "worker_api_auth": (
                worker_value
                if isinstance(worker_value, bool)
                else "worker_api_auth" in raw
            ),
            "dashboard_token_auth": (
                dashboard_value
                if isinstance(dashboard_value, bool)
                else "dashboard_token_auth" in raw
            ),
        }


def write_settings(settings: Dict[str, Any]) -> None:
    with settings_lock:
        _settings_repository().write(settings)


def read_all_nodes() -> List[Node]:
    with inventory_lock:
        return load_nodes(
            INVENTORY_PATH,
            include_disabled=True,
            require_legacy_head=False,
        )


def read_enabled_nodes() -> List[Node]:
    """Dashboard inventory may be worker-only on a standalone Controller."""
    return load_nodes(INVENTORY_PATH, require_legacy_head=False)


def write_all_nodes(nodes: Sequence[Node]) -> None:
    enabled_heads = [node for node in nodes if node.role == "head" and node.enabled]
    if len(enabled_heads) > 1:
        raise ValueError("At most one legacy enabled head node is allowed")
    if len({node.name for node in nodes}) != len(nodes):
        raise ValueError("Node names must be unique")
    endpoints = [(node.host, node.ssh_port) for node in nodes]
    if len(set(endpoints)) != len(endpoints):
        raise ValueError("Each physical host and SSH port can be registered only once")
    _inventory_repository().write_rows([asdict(node) for node in nodes])


def serialize_node(node: Node) -> Dict[str, Any]:
    item = asdict(node)
    item.pop("identity_file", None)
    item["api_url"] = node.api_url
    return item


def _node_environment_fingerprint(node: Node) -> str:
    identity = {
        "name": node.name,
        "role": node.role,
        "host": node.host,
        "user": node.user,
        "ssh_port": node.ssh_port,
        "api_port": node.api_port,
        "project_dir": node.project_dir,
        "platform": node.platform,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _invalidate_environment_report(node_name: str) -> None:
    _environment_repository().delete(node_name)


def _environment_placeholder(node: Node) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "node": node.name,
        "status": "not_checked",
        "checked_at": None,
        "received_at": None,
        "inventory_fingerprint": _node_environment_fingerprint(node),
        "platform": node.platform,
        "board_model": "",
        "architecture": "",
        "os": "",
        "python": "",
        "disk_free_gb": None,
        "project_dir": node.project_dir,
        "venv_path": f"{node.project_dir}/.venv",
        "checks": [],
        "missing_system_packages": [],
        "manual_commands": [],
        "backend": {"kind": "unknown", "verified": False},
        "model_count": 0,
    }


def normalize_environment_report(raw: Dict[str, Any], node: Node) -> Dict[str, Any]:
    allowed_statuses = {
        "ready",
        "needs_setup",
        "manual",
        "unavailable",
        "failed",
        "not_checked",
        "repairable",
        "blocked",
        "checking",
    }
    status = str(raw.get("status") or "failed")
    if status not in allowed_statuses:
        status = "failed"
    checks = []
    for index, item in enumerate(raw.get("checks") or []):
        if index >= 40 or not isinstance(item, dict):
            break
        check_status = str(item.get("status") or "unknown")
        if check_status not in {"pass", "fail", "warn", "missing", "unknown", "checking"}:
            check_status = "unknown"
        check_id = re.sub(
            r"[^a-zA-Z0-9_-]",
            "_",
            str(item.get("id") or f"check_{index}"),
        )[:64]
        checks.append(
            {
                "id": check_id,
                "label": str(item.get("label") or check_id)[:120],
                "status": check_status,
                "detail": str(item.get("detail") or "")[:2000],
                "auto_fixable": bool(item.get("auto_fixable", False)),
            }
        )
    missing = [
        str(item)[:80]
        for item in (raw.get("missing_system_packages") or [])[:40]
        if isinstance(item, str)
    ]
    manual = [
        str(item)[:2000]
        for item in (raw.get("manual_commands") or [])[:10]
        if isinstance(item, str)
    ]
    try:
        model_count = max(0, int(raw.get("model_count", 0)))
    except (TypeError, ValueError):
        model_count = 0
    raw_backend = raw.get("backend")
    if isinstance(raw_backend, dict):
        backend: Any = {
            "kind": str(raw_backend.get("kind") or "unknown")[:64],
            "verified": bool(raw_backend.get("verified", False)),
        }
    else:
        backend = {
            "kind": str(raw_backend or "unknown")[:64],
            # Old reports used a scalar only after successful verification.
            "verified": bool(raw_backend and raw_backend != "unknown"),
        }
    try:
        disk_free_gb: Optional[float] = round(float(raw.get("disk_free_gb")), 2)
    except (TypeError, ValueError):
        disk_free_gb = None
    return {
        "schema_version": 1,
        "node": node.name,
        "status": status,
        # Never manufacture a fresh timestamp for an incomplete/legacy file:
        # experiment admission treats a missing timestamp as stale and asks
        # the user to run a real preflight again.
        "checked_at": str(raw.get("checked_at")) if raw.get("checked_at") else None,
        "received_at": str(raw.get("received_at")) if raw.get("received_at") else None,
        "inventory_fingerprint": str(raw.get("inventory_fingerprint") or "")[:64],
        "platform": str(raw.get("platform") or node.platform)[:80],
        "board_model": str(raw.get("board_model") or "")[:240],
        "architecture": str(raw.get("architecture") or "")[:32],
        "os": str(raw.get("os") or "")[:240],
        "python": str(raw.get("python") or "")[:120],
        "disk_free_gb": disk_free_gb,
        "project_dir": node.project_dir,
        "venv_path": f"{node.project_dir}/.venv",
        "checks": checks,
        "missing_system_packages": missing,
        "manual_commands": manual,
        "backend": backend,
        "model_count": model_count,
    }


def write_environment_report(raw: Dict[str, Any]) -> Dict[str, Any]:
    node_name = str(raw.get("node") or "")
    nodes = {node.name: node for node in read_all_nodes()}
    if node_name not in nodes:
        raise ValueError("Environment report references an unknown node")
    received = {
        **raw,
        "received_at": utc_now(),
        "inventory_fingerprint": _node_environment_fingerprint(nodes[node_name]),
    }
    report = normalize_environment_report(received, nodes[node_name])
    _environment_repository().write(node_name, report)
    return report


def read_environment_reports() -> List[Dict[str, Any]]:
    reports = []
    for node in read_all_nodes():
        try:
            raw = _environment_repository().read(node.name)
            if raw.get("inventory_fingerprint") != _node_environment_fingerprint(node):
                placeholder = _environment_placeholder(node)
                placeholder["status"] = "not_checked"
                placeholder["checks"] = [
                    {
                        "id": "inventory_identity",
                        "label": "Node inventory identity",
                        "status": "fail",
                        "detail": "Node address or project identity changed; run a fresh environment check",
                        "auto_fixable": True,
                    }
                ]
                reports.append(placeholder)
                continue
            reports.append(normalize_environment_report(raw, node))
        except (FileNotFoundError, OSError, StorageCorruptionError, TypeError):
            reports.append(_environment_placeholder(node))
    return reports


def validate_experiment_environment(
    nodes: Sequence[Node],
    live_status: Dict[str, Dict[str, Any]],
    model_ids: Sequence[str],
    execution_strategy: str,
    rpc_coordinator_node: Optional[str] = None,
) -> None:
    """Reject experiments whose most recent persisted preflight is unsafe or stale."""
    reports = {item["node"]: item for item in read_environment_reports()}
    now = datetime.now(timezone.utc)
    problems: List[str] = []
    for node in nodes:
        report = reports.get(node.name) or _environment_placeholder(node)
        checked_at = report.get("checked_at")
        received_at = report.get("received_at")
        age_hours: Optional[float] = None
        if received_at:
            try:
                parsed = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_hours = (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600
            except ValueError:
                pass
        if report.get("status") != "ready":
            problems.append(
                f"{node.name}: 환경 상태 {report.get('status') or 'not_checked'} "
                "(환경 점검 또는 자동 구성을 실행하세요)"
            )
        elif age_hours is None or age_hours < -0.08 or age_hours > 24:
            problems.append(f"{node.name}: 환경 점검 결과가 24시간을 초과했으므로 다시 점검하세요")
        else:
            backend = report.get("backend")
            if isinstance(backend, dict):
                backend_verified = bool(backend.get("verified")) and backend.get("kind") not in {
                    "",
                    "unknown",
                    None,
                }
            else:
                backend_verified = backend not in {"", "unknown", None}
            if not backend_verified:
                problems.append(f"{node.name}: LLM 백엔드 검증 결과가 없습니다")
            detected_platform = report.get("platform")
            if node.platform != "auto" and detected_platform != node.platform:
                problems.append(
                    f"{node.name}: 인벤토리 플랫폼 {node.platform}와 실제 보드 "
                    f"{detected_platform or 'unknown'}이 다릅니다"
                )
        if checked_at:
            try:
                worker_time = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
                if worker_time.tzinfo is None:
                    worker_time = worker_time.replace(tzinfo=timezone.utc)
                if (worker_time.astimezone(timezone.utc) - now).total_seconds() > 300:
                    problems.append(f"{node.name}: 노드 시계가 head보다 5분 이상 빠릅니다 (NTP를 확인하세요)")
            except ValueError:
                pass

        live = live_status.get(node.name) or {}
        if live.get("api") is not True:
            problems.append(f"{node.name}: 워커 API가 오프라인입니다 (노드 시작 후 다시 시도하세요)")

        # RPC model-parallel loads the GGUF only on its selected worker coordinator. All
        # replicated strategies require every selected node to have every model.
        requires_models = (
            execution_strategy != "model_parallel_rpc"
            or node.name == rpc_coordinator_node
        )
        if requires_models and live.get("api") is True:
            available = set(live.get("model_ids") or [])
            missing_models = [model_id for model_id in model_ids if model_id not in available]
            if missing_models:
                raise ModelPreflightError(
                    f"{node.name}: 모델 없음 - " + ", ".join(missing_models[:4])
                    + (" 외" if len(missing_models) > 4 else ""),
                    code=ErrorCode.MODEL_MISSING,
                    stage="model_preflight",
                    node=node.name,
                    model_id=missing_models[0],
                    evidence={"available_model_ids": sorted(available)},
                )
    if problems:
        raise ValueError("실험 환경 준비가 필요합니다: " + " / ".join(problems))


def read_model_catalog() -> List[ModelCatalogEntry]:
    """Read local/cache catalog metadata; no network is required for control plane use."""
    raw_by_id: Dict[str, Dict[str, Any]] = {}
    for path in (MODEL_CATALOG_PATH, MODEL_CATALOG_CACHE_PATH):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            continue
        values = raw.get("models", []) if isinstance(raw, dict) else raw
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and value.get("id"):
                raw_by_id[str(value["id"])] = value
    catalog: List[ModelCatalogEntry] = []
    for value in raw_by_id.values():
        try:
            catalog.append(ModelCatalogEntry.from_dict(value))
        except (TypeError, ValueError):
            continue
    return sorted(catalog, key=lambda item: item.id)


def fetch_worker_model_inventory(node: Node, timeout: float = 5.0) -> WorkerModelInventory:
    try:
        payload = request_json(f"{node.api_url}/cluster/models", timeout=timeout)
        if payload.get("ok") is not True or payload.get("node") != node.name:
            raise ValueError("worker model inventory identity mismatch")
        models = payload.get("models")
        if not isinstance(models, list):
            raise ValueError("worker model inventory has no models list")
        return parse_worker_inventory(node.name, models)
    except Exception as exc:
        return WorkerModelInventory(node=node.name, models=(), error=str(exc))


def collect_worker_model_inventories(
    nodes: Optional[Sequence[Node]] = None, *, timeout: float = 5.0
) -> List[WorkerModelInventory]:
    selected = list(nodes) if nodes is not None else [node for node in read_enabled_nodes() if node.role == "worker"]
    if not selected:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(selected)) as executor:
        futures = {executor.submit(fetch_worker_model_inventory, node, timeout): node for node in selected}
        collected = [future.result() for future in concurrent.futures.as_completed(futures)]
    order = {node.name: index for index, node in enumerate(selected)}
    return sorted(collected, key=lambda item: order[item.node])


def list_models() -> List[Dict[str, Any]]:
    """Aggregate Worker filesystem inventories; the Controller model directory is never scanned."""
    return aggregate_catalog(collect_worker_model_inventories(), read_model_catalog())


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
    allowed = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    found: List[Dict[str, str]] = []
    seen = set()
    try:
        interfaces = psutil.net_if_addrs()
        interface_stats = psutil.net_if_stats()
    except (OSError, ValueError):
        return found
    for interface_name, addresses in interfaces.items():
        if not interface_stats.get(interface_name, None) or not interface_stats[interface_name].isup:
            continue
        if interface_name.startswith(("docker", "br-", "veth", "virbr", "tailscale")):
            continue
        for info in addresses:
            if info.family != socket.AF_INET:
                continue
            raw = info.address
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if address.version != 4 or not any(address in network for network in allowed):
                continue
            try:
                prefix = ipaddress.ip_network(f"{address}/{info.netmask}", strict=False).prefixlen
            except ValueError:
                continue
            prefix = max(prefix, 24)
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


def _reverse_hostname(host: str) -> str:
    """Return a best-effort LAN hostname without making SSH state changes."""
    try:
        hostname, _aliases, _addresses = socket.gethostbyaddr(host)
    except (OSError, socket.herror):
        return ""
    hostname = hostname.rstrip(".").strip()
    return "" if hostname == host else hostname[:253]


def _registered_worker_hostname(node: Node) -> str:
    """Ask an already registered worker for its OS hostname using its SSH key."""
    try:
        result = run_on_node(node, ["hostname"], timeout=5)
    except Exception:
        return ""
    if not result.ok:
        return ""
    hostname = result.stdout.splitlines()[0].strip() if result.stdout else ""
    return hostname[:253] if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", hostname) else ""


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
    all_nodes = read_all_nodes()
    existing = {node.host: node for node in all_nodes}
    hostname_by_host: Dict[str, str] = {}
    hostname_source_by_host: Dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(1, len(open_hosts)))) as executor:
        futures = {}
        for host in open_hosts:
            node = existing.get(host)
            resolver = _registered_worker_hostname if node else _reverse_hostname
            futures[executor.submit(resolver, node or host)] = host
        for future in concurrent.futures.as_completed(futures):
            host = futures[future]
            hostname = future.result()
            hostname_by_host[host] = hostname
            if hostname:
                hostname_source_by_host[host] = "ssh" if existing.get(host) else "reverse_dns"
    head_node = next((node for node in all_nodes if node.role == "head"), None)
    devices = []
    for host in sorted(open_hosts, key=lambda value: tuple(int(part) for part in value.split("."))):
        known = existing.get(host) or (head_node if host in local_ips else None)
        devices.append(
            {
                "host": host,
                "hostname": hostname_by_host.get(host, ""),
                "hostname_source": hostname_source_by_host.get(host, ""),
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
        and (configured == "auto" or configured == detected)
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
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cluster-status", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def refresh_now(self) -> None:
        try:
            nodes = read_all_nodes()
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(32, max(1, len(nodes)))) as executor:
                snapshot = list(executor.map(probe_node, nodes))
            with self._lock:
                changed = snapshot != self._snapshot
                self._snapshot = snapshot
            events.publish("cluster_status", channel=EventChannel.SYSTEM, nodes=snapshot, changed=changed)
        except Exception as exc:
            events.publish("monitor_error", channel=EventChannel.SYSTEM, message=str(exc))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh_now()
            self._stop.wait(5.0)

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return json.loads(json.dumps(self._snapshot))


status_monitor = StatusMonitor()


class ActionManager:
    ALLOWED = {
        "doctor",
        "setup",
        "prepare",
        "prepare-rpc",
        "sync-code",
        "sync-models",
        "delete-models",
        "install-model-url",
        "start",
        "stop",
        "restart",
        "select-model",
        "environment-check",
        "environment-install",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._actions: Dict[str, Dict[str, Any]] = {}

    def start(self, payload: ActionPayload) -> Dict[str, Any]:
        if payload.action not in self.ALLOWED:
            raise ValueError(f"Unsupported action: {payload.action}")
        enabled = read_enabled_nodes()
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
        if payload.action in {"environment-check", "environment-install"}:
            record["inventory_fingerprints"] = {
                node.name: _node_environment_fingerprint(node) for node in selected
            }
        with self._lock:
            for action in self._actions.values():
                if action.get("status") in {"queued", "running"} and selected_names.intersection(action.get("nodes") or []):
                    raise ValueError("A selected node already has a running control action")
            self._actions[action_id] = record
        if payload.action in {"environment-check", "environment-install"}:
            checking_reports = []
            for node in selected:
                pending = _environment_placeholder(node)
                pending.update(
                    {
                        "status": "checking",
                        "checked_at": None,
                        "checks": [
                            {
                                "id": "environment_operation",
                                "label": "Environment operation",
                                "status": "checking",
                                "detail": f"{payload.action} is running",
                                "auto_fixable": False,
                            }
                        ],
                    }
                )
                checking_reports.append(write_environment_report(pending))
            events.publish("environment_changed", channel=EventChannel.NODE_OPS, environment=read_environment_reports(), reports=checking_reports)
        thread = threading.Thread(
            target=self._run,
            args=(action_id, payload),
            name=f"cluster-action-{action_id}",
            daemon=True,
        )
        thread.start()
        return dict(record)

    def _run(self, action_id: str, payload: ActionPayload) -> None:
        environment_reported_nodes: set[str] = set()
        with self._lock:
            expected_environment_fingerprints = dict(
                self._actions[action_id].get("inventory_fingerprints") or {}
            )

        def persist_missing_environment_reports(detail: str) -> None:
            if payload.action not in {"environment-check", "environment-install"}:
                return
            inventory = {node.name: node for node in read_all_nodes()}
            for node_name in payload.node_names:
                if node_name in environment_reported_nodes or node_name not in inventory:
                    continue
                raw = _environment_placeholder(inventory[node_name])
                raw.update(
                    {
                        "status": "failed",
                        "checked_at": utc_now(),
                        "checks": [
                            {
                                "id": "environment_operation",
                                "label": "Environment operation",
                                "status": "fail",
                                "detail": detail[-2000:] or "Environment process exited without a report",
                                "auto_fixable": True,
                            }
                        ],
                    }
                )
                report = write_environment_report(raw)
                environment_reported_nodes.add(node_name)
                events.publish(
                    "environment_changed",
                    channel=EventChannel.NODE_OPS,
                    environment=read_environment_reports(),
                    report=report,
                )

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
        if payload.action in {"sync-models", "delete-models", "prepare"}:
            for model in payload.options.get("models", []):
                command.extend(["--model", str(model)])
        elif payload.action == "install-model-url":
            command.extend(
                [
                    "--model-id", str(payload.options.get("model_id", "")),
                    "--source-url", str(payload.options.get("source_url", "")),
                    "--expected-sha256", str(payload.options.get("expected_sha256", "")),
                ]
            )
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
        elif payload.action == "environment-install":
            command.append("--confirmed")

        with self._lock:
            self._actions[action_id]["status"] = "running"
        events.publish("action_started", channel=EventChannel.NODE_OPS, action=self.get(action_id))
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
                if clean.startswith(ENVIRONMENT_MARKER):
                    try:
                        raw_report = json.loads(clean[len(ENVIRONMENT_MARKER) :])
                        if isinstance(raw_report, dict):
                            report_node = str(raw_report.get("node") or "")
                            current_nodes = {node.name: node for node in read_all_nodes()}
                            expected_fingerprint = expected_environment_fingerprints.get(report_node)
                            current_node = current_nodes.get(report_node)
                            if (
                                not expected_fingerprint
                                or current_node is None
                                or _node_environment_fingerprint(current_node) != expected_fingerprint
                            ):
                                raise ValueError(
                                    f"inventory identity changed while checking {report_node or 'unknown node'}"
                                )
                            report = write_environment_report(raw_report)
                            environment_reported_nodes.add(report["node"])
                            events.publish(
                                "environment_changed",
                                channel=EventChannel.NODE_OPS,
                                environment=read_environment_reports(),
                                report=report,
                            )
                    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                        clean = f"[environment-report-error] {exc}"
                if clean.startswith(MODEL_PROGRESS_MARKER):
                    try:
                        progress = json.loads(clean[len(MODEL_PROGRESS_MARKER) :])
                        if not isinstance(progress, dict):
                            raise ValueError("model progress is not an object")
                        events.publish(
                            "model_progress",
                            channel=EventChannel.NODE_OPS,
                            action_id=action_id,
                            progress=progress,
                        )
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        clean = f"[model-progress-error] {exc}"
                    else:
                        # Progress is already delivered as a typed node event;
                        # do not leak the internal marker into the action log.
                        continue
                with self._lock:
                    log = self._actions[action_id]["log"]
                    log.append(clean)
                    if len(log) > 500:
                        del log[:-500]
                events.publish("action_log", channel=EventChannel.NODE_OPS, action_id=action_id, line=clean)
            exit_code = process.wait()
            persist_missing_environment_reports(
                f"Environment process exited with code {exit_code} without a structured report"
            )
            with self._lock:
                record = self._actions[action_id]
                record["exit_code"] = exit_code
                record["status"] = "completed" if exit_code == 0 else "failed"
                record["finished_at"] = utc_now()
        except Exception as exc:
            persist_missing_environment_reports(str(exc))
            with self._lock:
                record = self._actions[action_id]
                record["status"] = "failed"
                record["finished_at"] = utc_now()
                record["log"].append(str(exc))
        events.publish("action_finished", channel=EventChannel.NODE_OPS, action=self.get(action_id))
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



def _suite_model_records(
    model_ids: Sequence[str],
    summaries: Sequence[Dict[str, Any]],
    errors: Sequence[Dict[str, Any]],
    attempted_models: int,
    suite_status: str,
    cleanup_statuses: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
    return suite_model_records(
        model_ids,
        summaries,
        errors,
        attempted_models,
        suite_status,
        cleanup_statuses,
    )


def _suite_document(
    *,
    suite_id: str,
    experiment_id: str,
    name: str,
    status: str,
    model_ids: Sequence[str],
    attempted_models: int,
    completed_models: int,
    total_work_units: int,
    completed_work_units: int,
    continue_on_model_error: bool,
    model_cooldown_s: float,
    started_at: str,
    summaries: Sequence[Dict[str, Any]],
    errors: Sequence[Dict[str, Any]],
    finished_at: Optional[str] = None,
    cleanup_statuses: Optional[Dict[int, str]] = None,
) -> Dict[str, Any]:
    return suite_document(
        suite_id=suite_id,
        experiment_id=experiment_id,
        name=name,
        status=status,
        model_ids=model_ids,
        attempted_models=attempted_models,
        completed_models=completed_models,
        total_work_units=total_work_units,
        completed_work_units=completed_work_units,
        continue_on_model_error=continue_on_model_error,
        model_cooldown_s=model_cooldown_s,
        started_at=started_at,
        finished_at=finished_at,
        summaries=summaries,
        errors=errors,
        cleanup_statuses=cleanup_statuses,
    )


def write_suite_summary(summary: Dict[str, Any]) -> Path:
    suite_id = str(summary.get("suite_id") or "")
    if not suite_id or not suite_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("suite_id contains unsupported characters")
    _suite_repository().write(suite_id, summary)
    return RESULTS_DIR / "_suites" / f"{suite_id}.json"


def reconcile_interrupted_suites(preserve_suite_ids: Sequence[str] = ()) -> int:
    """Finalize only legacy nonterminal suites that have no live durable job."""
    reconciled = 0
    preserved = set(preserve_suite_ids)
    suites_dir = RESULTS_DIR / "_suites"
    if not suites_dir.exists():
        return reconciled
    for path in suites_dir.glob("*.json"):
        try:
            suite = json.loads(path.read_text(encoding="utf-8"))
            suite_id = str(suite.get("suite_id") or "")
            if (
                suite.get("artifact_type") != "experiment_suite"
                or suite.get("status") not in {"queued", "running", "cancelling"}
                or suite_id in preserved
                or path.stem != suite_id
                or not suite_id.replace("-", "").replace("_", "").isalnum()
            ):
                continue
            error = {
                "stage": "dashboard_restart",
                "error": "Suite was interrupted by a dashboard restart",
            }
            errors = list(suite.get("errors") or [])
            errors.append(error)
            suite.update(
                {
                    "status": "failed",
                    "interrupted": True,
                    "interrupted_from_status": suite.get("status"),
                    "finished_at": utc_now(),
                    "errors": errors,
                }
            )
            suite["updated_at"] = suite["finished_at"]
            suite["models"] = _suite_model_records(
                suite.get("model_ids") or [],
                suite.get("summaries") or [],
                errors,
                int(suite.get("attempted_models") or 0),
                "failed",
                {
                    int(model["model_index"]): str(model.get("cleanup_status") or "pending")
                    for model in suite.get("models") or []
                    if isinstance(model, dict)
                    and str(model.get("model_index", "")).isdigit()
                },
            )
            write_suite_summary(suite)
            reconciled += 1
        except (OSError, TypeError, ValueError):
            continue
    return reconciled


def read_suite_summaries(limit: int = 100) -> List[Dict[str, Any]]:
    return [
        suite
        for suite in _suite_repository().list(limit=limit)
        if suite.get("artifact_type") == "experiment_suite" and suite.get("suite_id")
    ]


def _with_suite_metadata(
    summary: Dict[str, Any], suites_by_id: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    suite = suites_by_id.get(str(summary.get("suite_id") or ""))
    if not suite:
        return summary
    return {
        **summary,
        "suite_status": suite.get("status"),
        "suite_started_at": suite.get("started_at"),
        "suite_finished_at": suite.get("finished_at"),
        "suite_attempted_models": suite.get("attempted_models"),
        "suite_completed_models": suite.get("completed_models"),
        "suite_models": suite.get("models", []),
        "suite_errors": suite.get("errors", []),
    }


def read_run_summaries(limit: int = 100) -> List[Dict[str, Any]]:
    suites_by_id = {
        str(suite["suite_id"]): suite for suite in read_suite_summaries(limit=0)
    }
    return [
        _with_suite_metadata(summary, suites_by_id)
        for summary in _run_repository().list_summaries(limit=limit)
    ]


def _experiment_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "experiment"


experiment_catalog_lock = threading.RLock()


def save_experiment_definition(payload: ExperimentPayload) -> Dict[str, Any]:
    with experiment_catalog_lock:
        experiment_id = payload.experiment_id
        if not experiment_id:
            experiment_id = f"{_experiment_slug(payload.name)}-{uuid.uuid4().hex[:6]}"
        existing: Dict[str, Any] = {}
        try:
            existing = _experiment_repository().read(experiment_id)
        except (FileNotFoundError, OSError, StorageCorruptionError):
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
        _experiment_repository().write(experiment_id, definition)
        return definition


def read_experiment_groups() -> List[Dict[str, Any]]:
    definitions: Dict[str, Dict[str, Any]] = {}
    with experiment_catalog_lock:
        for definition in _experiment_repository().list():
            try:
                definitions[definition["experiment_id"]] = definition
            except KeyError:
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
    """Dashboard facade over the durable child-process job service."""

    def __init__(self, job_service: Optional[JobService] = None) -> None:
        self._jobs = job_service or JobService(
            jobs_dir=JOBS_DIR,
            inventory_path=INVENTORY_PATH,
            results_dir=RESULTS_DIR,
            project_root=PROJECT_ROOT,
            python_bin=Path(sys.executable),
            on_change=self._publish_change,
        )

    @staticmethod
    def _public_job(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if job is None:
            return None
        public = json.loads(json.dumps(job))
        for key in ("config", "command", "process", "log_path"):
            public.pop(key, None)
        return public

    @staticmethod
    def _publish_change(job: Dict[str, Any]) -> None:
        public_job = ExperimentManager._public_job(job) or {}
        event = job.get("latest_event") or {
            "type": "job_recovered",
            "at": utc_now(),
            "job_id": job.get("job_id"),
            "status": job.get("status"),
        }
        events.publish("experiment_event", channel=EventChannel.EXPERIMENT, event=event, active=public_job)
        if job.get("status") not in NONTERMINAL_JOB_STATES:
            status_monitor.refresh_now()

    def start(self, payload: ExperimentPayload) -> Dict[str, Any]:
        payload_data = payload.model_dump()
        config = ExperimentConfig.from_dict(payload_data)
        config.validate()
        selected = select_nodes(read_enabled_nodes(), config.node_names)
        if len(selected) != len(config.node_names):
            raise ValueError("Some selected nodes are unavailable")
        validate_strategy(selected, config)
        available_models = {item["id"] for item in list_models()}
        missing_models = [
            model_id for model_id in payload.model_ids if model_id not in available_models
        ]
        if missing_models and config.execution_strategy != "model_parallel_rpc":
            raise ValueError("Unknown model_ids: " + ", ".join(missing_models))

        suffix = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        suite_id = "suite_" + suffix
        job_id = "job_" + suffix
        per_model_total = strategy_work_units(config, len(selected))
        started_at = utc_now()
        config.suite_id = suite_id
        config.model_count = len(payload.model_ids)
        config.validate()
        queued_suite = _suite_document(
            suite_id=suite_id,
            experiment_id=config.experiment_id,
            name=config.name,
            status="queued",
            model_ids=payload.model_ids,
            attempted_models=0,
            completed_models=0,
            total_work_units=per_model_total * len(payload.model_ids),
            completed_work_units=0,
            continue_on_model_error=payload.continue_on_model_error,
            model_cooldown_s=payload.model_cooldown_s,
            started_at=started_at,
            summaries=[],
            errors=[],
        )
        write_suite_summary(queued_suite)
        job = {
            "schema_version": 1,
            "artifact_type": "experiment_job",
            "id": suite_id,
            "job_id": job_id,
            "suite_id": suite_id,
            "experiment_id": config.experiment_id,
            "name": config.name,
            "status": "queued",
            "phase": "queued",
            "completed": 0,
            "total": per_model_total * len(payload.model_ids),
            "model_completed": 0,
            "model_total": per_model_total,
            "strategy": str(config.execution_strategy),
            "started_at": started_at,
            "nodes": list(config.node_names),
            "model_ids": list(payload.model_ids),
            "current_model": payload.model_ids[0],
            "model_index": 0,
            "model_count": len(payload.model_ids),
            "completed_models": 0,
            "summaries": [],
            "errors": [],
            "latest": None,
            "error": "",
            "continue_on_model_error": payload.continue_on_model_error,
            "model_cooldown_s": payload.model_cooldown_s,
            "config": asdict(config),
            "cancel_requested": False,
            "created_at": started_at,
            "updated_at": started_at,
        }
        try:
            return self._public_job(self._jobs.start(job)) or {}
        except Exception:
            # A queued suite without a child process is terminal evidence, not a
            # fake running experiment.
            failed = {**queued_suite, "status": "failed", "finished_at": utc_now()}
            failed["updated_at"] = failed["finished_at"]
            failed["errors"] = [
                {"stage": "job_spawn", "error": "Durable job process did not start"}
            ]
            failed["models"] = _suite_model_records(
                failed["model_ids"], [], failed["errors"], 0, "failed"
            )
            write_suite_summary(failed)
            raise

    def cancel(self) -> Dict[str, Any]:
        return self._public_job(self._jobs.cancel()) or {}

    def active(self) -> Optional[Dict[str, Any]]:
        return self._public_job(self._jobs.active())

    def jobs(self) -> List[Dict[str, Any]]:
        return [self._public_job(job) or {} for job in self._jobs.list()]


experiments = ExperimentManager()
_active_job = experiments.active()
reconcile_interrupted_suites(
    [str(_active_job.get("suite_id"))]
    if _active_job and _active_job.get("status") in NONTERMINAL_JOB_STATES
    else []
)


class DashboardServiceError(ValueError):
    """Transport-neutral error returned by the dashboard application service."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class DashboardFacade:
    """Application service facade consumed by route adapters.

    The facade returns plain dictionaries and raises ``DashboardServiceError``.
    It knows neither FastAPI requests nor response objects.
    """

    def startup(self) -> None:
        active = experiments.active()
        reconcile_interrupted_suites(
            [str(active.get("suite_id"))]
            if active and active.get("status") in NONTERMINAL_JOB_STATES
            else []
        )
        status_monitor.start()

    def shutdown(self) -> None:
        status_monitor.stop()

    def dashboard_health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "service": "cluster-dashboard",
            "role": "controller",
            "inference_enabled": False,
        }

    def controller_status(self) -> Dict[str, Any]:
        return {
            "role": "controller",
            "inference_enabled": False,
            "runtime_dir": str(PROJECT_LAYOUT.controller_runtime_dir),
            "results_dir": str(RESULTS_DIR),
            "dashboard": {"service": "cluster-dashboard", "healthy": True},
            "at": utc_now(),
        }

    async def bootstrap(self) -> Dict[str, Any]:
        defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
        inventories = await asyncio.to_thread(collect_worker_model_inventories)
        catalog = read_model_catalog()
        return {
            "nodes": [serialize_node(node) for node in read_all_nodes()],
            "status": status_monitor.snapshot(),
            "models": aggregate_catalog(inventories, catalog),
            "model_inventories": [item.to_dict() for item in inventories],
            "model_catalog": [item.to_dict() for item in catalog],
            "defaults": defaults,
            "active_experiment": experiments.active(),
            "jobs": experiments.jobs(),
            "runs": read_run_summaries(),
            "suites": read_suite_summaries(),
            "experiment_groups": read_experiment_groups(),
            "actions": actions.list(),
            "environment": read_environment_reports(),
            "settings": read_settings(),
            "experiment_strategies": experiment_strategy_catalog(),
            "onboarding": _controller_public_key_info(),
        }

    def create_controller_ssh_identity(self) -> Dict[str, Any]:
        return ensure_controller_ssh_identity()

    def settings(self) -> Dict[str, Any]:
        return {"settings": read_settings()}

    def update_settings(
        self, payload: Any, *, supplied_token: str, token_is_valid: Any
    ) -> Dict[str, Any]:
        action: Optional[Dict[str, Any]] = None
        with settings_lock:
            previous = read_settings()
            if previous["dashboard_token_auth"] and not token_is_valid(supplied_token):
                raise DashboardServiceError(401, "Dashboard access token is missing or invalid")
            updated = dict(previous)
            if payload.worker_api_auth is not None:
                updated["worker_api_auth"] = payload.worker_api_auth
            if payload.dashboard_token_auth is not None:
                updated["dashboard_token_auth"] = payload.dashboard_token_auth
            if not previous["dashboard_token_auth"] and updated["dashboard_token_auth"]:
                if not token_is_valid(supplied_token):
                    raise DashboardServiceError(
                        403,
                        "Enabling dashboard token auth requires the current dashboard token",
                    )
            write_settings(updated)
            if previous["worker_api_auth"] != updated["worker_api_auth"]:
                try:
                    action = actions.start(
                        ActionPayload(
                            action="restart",
                            node_names=[node.name for node in read_enabled_nodes()],
                            options={},
                        )
                    )
                except ValueError as exc:
                    write_settings(previous)
                    raise DashboardServiceError(409, str(exc)) from exc
        events.publish("settings_changed", channel=EventChannel.SYSTEM, settings=updated, action=action)
        return {"ok": True, "settings": updated, "action": action}

    def status(self) -> Dict[str, Any]:
        return {"nodes": status_monitor.snapshot(), "at": utc_now()}

    async def models(self) -> Dict[str, Any]:
        inventories = await asyncio.to_thread(collect_worker_model_inventories)
        catalog = read_model_catalog()
        status_by_name = {item.get("name"): item for item in status_monitor.snapshot()}
        recommendations: Dict[str, List[Dict[str, Any]]] = {}
        for node in read_enabled_nodes():
            if node.role != "worker":
                continue
            profile = (status_by_name.get(node.name) or {}).get("profile") or {}
            recommendations[node.name] = [
                item.to_dict()
                for item in recommend_models(
                    catalog,
                    platform=str(profile.get("platform_kind") or node.platform),
                    memory_total_mb=profile.get("memory_total_mb"),
                )
            ]
        return {
            "models": aggregate_catalog(inventories, catalog),
            "inventories": [item.to_dict() for item in inventories],
            "catalog": [item.to_dict() for item in catalog],
            "recommendations": recommendations,
        }

    def refresh_status(self) -> Dict[str, Any]:
        threading.Thread(target=status_monitor.refresh_now, daemon=True).start()
        return {"ok": True}

    async def scan_network(self, force: bool) -> Dict[str, Any]:
        return await asyncio.to_thread(scan_lan_devices, force)

    async def probe_candidate(self, payload: NodePayload) -> Dict[str, Any]:
        if payload.role != "worker":
            raise DashboardServiceError(400, "Only worker candidates can be probed")
        return await asyncio.to_thread(probe_candidate, Node(**payload.model_dump()))

    def upsert_node(self, payload: NodePayload) -> Dict[str, Any]:
        node = Node(**payload.model_dump())
        with inventory_lock:
            nodes = read_all_nodes()
            existing_index = next((i for i, item in enumerate(nodes) if item.name == node.name), None)
            active = experiments.active()
            if (
                existing_index is not None
                and active
                and active.get("status") in NONTERMINAL_JOB_STATES
                and node.name in set(active.get("nodes") or [])
            ):
                raise DashboardServiceError(409, "Node is busy with an experiment")
            if existing_index is not None and node.name in set(actions.busy_nodes()):
                raise DashboardServiceError(409, "Node has a running control action")
            identity_changed = existing_index is None
            if existing_index is None:
                if node.role == "head":
                    raise DashboardServiceError(400, "A head node already exists")
                nodes.append(node)
            else:
                existing = nodes[existing_index]
                if existing.role == "head" and node.role != "head":
                    raise DashboardServiceError(400, "The head role cannot be changed")
                identity_changed = _node_environment_fingerprint(existing) != _node_environment_fingerprint(node)
                nodes[existing_index] = node
            try:
                write_all_nodes(nodes)
                if identity_changed:
                    _invalidate_environment_report(node.name)
            except ValueError as exc:
                raise DashboardServiceError(400, str(exc)) from exc
        events.publish(
            "inventory_changed",
            channel=EventChannel.NODE_OPS,
            nodes=[serialize_node(item) for item in nodes],
        )
        threading.Thread(target=status_monitor.refresh_now, daemon=True).start()
        return {"ok": True, "node": serialize_node(node)}

    def rename_node(self, node_name: str, new_name: str) -> Dict[str, Any]:
        new_name = new_name.strip()
        try:
            validate_node_id(node_name)
            validate_node_id(new_name)
        except ValueError as exc:
            raise DashboardServiceError(400, str(exc)) from exc
        with inventory_lock:
            active = experiments.active()
            if (
                active
                and active.get("status") in NONTERMINAL_JOB_STATES
                and node_name in set(active.get("nodes") or [])
            ):
                raise DashboardServiceError(409, "Node is busy with an experiment")
            if node_name in set(actions.busy_nodes()):
                raise DashboardServiceError(409, "Node has a running control action")
            nodes = read_all_nodes()
            target_index = next((index for index, node in enumerate(nodes) if node.name == node_name), None)
            if target_index is None:
                raise DashboardServiceError(404, "Node not found")
            target = nodes[target_index]
            if target.role != "worker":
                raise DashboardServiceError(400, "Only worker nodes can be renamed")
            if node_name == new_name:
                return {
                    "ok": True,
                    "old_name": node_name,
                    "node": serialize_node(target),
                    "action": None,
                    "warning": "",
                }
            if any(node.name == new_name for node in nodes):
                raise DashboardServiceError(409, "Another node already uses that name")
            renamed = Node(
                name=new_name,
                role=target.role,
                host=target.host,
                user=target.user,
                ssh_port=target.ssh_port,
                api_port=target.api_port,
                project_dir=target.project_dir,
                enabled=target.enabled,
                identity_file=target.identity_file,
                platform=target.platform,
            )
            nodes[target_index] = renamed
            try:
                write_all_nodes(nodes)
            except ValueError as exc:
                raise DashboardServiceError(400, str(exc)) from exc
            _invalidate_environment_report(node_name)
        events.publish(
            "inventory_changed",
            channel=EventChannel.NODE_OPS,
            nodes=[serialize_node(item) for item in nodes],
            renamed={"old_name": node_name, "new_name": new_name},
        )
        action = None
        warning = ""
        if renamed.enabled:
            try:
                action = actions.start(ActionPayload(action="restart", node_names=[new_name], options={}))
            except ValueError as exc:
                warning = f"Name changed, but worker restart could not start: {exc}"
        threading.Thread(target=status_monitor.refresh_now, daemon=True).start()
        return {
            "ok": True,
            "old_name": node_name,
            "node": serialize_node(renamed),
            "action": action,
            "warning": warning,
        }

    def delete_node(self, node_name: str) -> Dict[str, Any]:
        with inventory_lock:
            active = experiments.active()
            if (
                active
                and active.get("status") in NONTERMINAL_JOB_STATES
                and node_name in set(active.get("nodes") or [])
            ):
                raise DashboardServiceError(409, "Node is busy with an experiment")
            if node_name in set(actions.busy_nodes()):
                raise DashboardServiceError(409, "Node has a running control action")
            nodes = read_all_nodes()
            target = next((node for node in nodes if node.name == node_name), None)
            if target is None:
                raise DashboardServiceError(404, "Node not found")
            if target.role == "head":
                raise DashboardServiceError(400, "The head node cannot be removed")
            write_all_nodes([node for node in nodes if node.name != node_name])
            _invalidate_environment_report(node_name)
        events.publish(
            "inventory_changed",
            channel=EventChannel.NODE_OPS,
            nodes=[serialize_node(item) for item in read_all_nodes()],
        )
        return {"ok": True}

    def start_action(self, payload: ActionPayload) -> Dict[str, Any]:
        requires_confirmation = {
            "setup", "prepare", "prepare-rpc", "environment-install", "delete-models", "install-model-url"
        }
        if payload.action in requires_confirmation and payload.options.get("confirmed") is not True:
            raise DashboardServiceError(400, "This worker operation requires explicit confirmation")
        try:
            record = actions.start(payload)
        except ValueError as exc:
            raise DashboardServiceError(400, str(exc)) from exc
        return {"ok": True, "action": record}

    def listed_actions(self) -> Dict[str, Any]:
        return {"actions": actions.list()}

    def environment(self) -> Dict[str, Any]:
        return {"environment": read_environment_reports()}

    def start_experiment(self, payload: ExperimentPayload) -> Dict[str, Any]:
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
            readiness_platforms = {item.get("node"): item.get("platform") for item in read_environment_reports()}
            if payload.execution_strategy == "model_parallel_rpc":
                platform_by_name = {}
                for node in selected_nodes:
                    detected = (status_by_name.get(node.name, {}).get("profile") or {}).get("platform_kind")
                    platform_by_name[node.name] = detected or readiness_platforms.get(node.name) or node.platform
                coordinator = select_rpc_coordinator(selected_nodes, payload.rpc_coordinator_node, platform_by_name)
                payload = payload.model_copy(update={"rpc_coordinator_node": coordinator.name})
            strategy_config = ExperimentConfig.from_dict(payload.model_dump())
            validate_strategy(selected_nodes, strategy_config)
            validate_experiment_environment(
                selected_nodes, status_by_name, payload.model_ids, payload.execution_strategy,
                payload.rpc_coordinator_node,
            )
            if payload.execution_strategy == "model_parallel_rpc" and read_settings()["worker_api_auth"]:
                raise ValueError(
                    "워커 API 보안 모드에서는 인증 없는 llama.cpp RPC 포트를 열지 않습니다. "
                    "SSH 터널 모드가 추가되기 전에는 신뢰 LAN에서만 보안을 끄고 실행하세요."
                )
            pi_nodes = []
            for name in payload.node_names:
                detected = (status_by_name.get(name, {}).get("profile") or {}).get("platform_kind")
                configured = inventory_by_name.get(name).platform if inventory_by_name.get(name) else "auto"
                if detected == "raspberry-pi" or configured == "raspberry-pi" or readiness_platforms.get(name) == "raspberry-pi":
                    pi_nodes.append(name)
            if pi_nodes and payload.n_gpu_layers != 0 and payload.execution_strategy != "model_parallel_rpc":
                raise ValueError("Raspberry Pi nodes require n_gpu_layers=0: " + ", ".join(str(item) for item in pi_nodes))
            inventories = collect_worker_model_inventories(selected_nodes, timeout=60.0)
            catalog = {item.id: item for item in read_model_catalog()}
            validate_model_preflight(
                node_names=[node.name for node in selected_nodes],
                inventories={item.node: item for item in inventories},
                model_ids=payload.model_ids,
                execution_strategy=payload.execution_strategy,
                rpc_coordinator_node=payload.rpc_coordinator_node,
                catalog=catalog,
            )
            definition = save_experiment_definition(payload)
            active = experiments.start(payload.model_copy(update={"experiment_id": definition["experiment_id"]}))
        except ClusterError as exc:
            failure = exc.to_failure_record()
            raise DashboardServiceError(http_status_for_failure(failure), failure.to_dict()) from exc
        except ValueError as exc:
            raise DashboardServiceError(400, str(exc)) from exc
        return {"ok": True, "experiment": active, "definition": definition}

    def experiments(self) -> Dict[str, Any]:
        return {
            "active": experiments.active(), "jobs": experiments.jobs(), "runs": read_run_summaries(),
            "suites": read_suite_summaries(), "experiment_groups": read_experiment_groups(),
        }

    def experiment_groups(self) -> Dict[str, Any]:
        return {"experiment_groups": read_experiment_groups()}

    def cancel_experiment(self) -> Dict[str, Any]:
        try:
            active = experiments.cancel()
        except ValueError as exc:
            raise DashboardServiceError(400, str(exc)) from exc
        return {"ok": True, "experiment": active}

    def run(self, run_id: str) -> Dict[str, Any]:
        if not run_id.replace("_", "").isalnum():
            raise DashboardServiceError(400, "Invalid run id")
        try:
            summary = _run_repository().read_summary(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(500, "Run summary is corrupted") from exc
        suites_by_id = {str(suite["suite_id"]): suite for suite in read_suite_summaries(limit=0)}
        return _with_suite_metadata(summary, suites_by_id)

    def responses(self, run_id: str) -> Dict[str, Any]:
        """Expose persisted raw responses without changing result CSV semantics."""
        if not run_id.replace("_", "").isalnum():
            raise DashboardServiceError(400, "Invalid run id")
        try:
            _run_repository().read_summary(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(500, "Run summary is corrupted") from exc
        return {"run_id": run_id, "responses": _run_repository().read_responses(run_id)}


COMPATIBILITY_EXPORTS = (
    "ActionManager", "ActionPayload", "DASHBOARD_TOKEN", "DEFAULTS_PATH", "ENVIRONMENT_DIR",
    "EventBus", "ExperimentManager", "ExperimentPayload", "INVENTORY_PATH", "JOBS_DIR",
    "MODEL_CATALOG_CACHE_PATH", "MODEL_CATALOG_PATH", "Node", "NodePayload", "PROJECT_LAYOUT", "PROJECT_ROOT", "RESULTS_DIR",
    "RUNTIME_DIR", "SETTINGS_PATH", "StatusMonitor", "_environment_placeholder",
    "_node_environment_fingerprint", "_suite_document", "_suite_model_records", "actions", "collect_worker_model_inventories", "ensure_runtime",
    "events", "experiments", "list_models", "normalize_environment_report", "probe_candidate",
    "read_all_nodes", "read_enabled_nodes", "read_environment_reports", "read_model_catalog",
    "read_run_summaries", "read_settings", "read_suite_summaries", "reconcile_interrupted_suites",
    "serialize_node", "status_monitor", "utc_now", "validate_experiment_environment",
    "write_all_nodes", "write_environment_report", "write_suite_summary",
)
