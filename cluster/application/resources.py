"""Controller-global durable Worker reservations.

The coordinator is deliberately filesystem backed.  Every Controller process that
uses the same runtime directory therefore participates in one admission decision.
The registry is fail closed: corrupt or uncertain entries are never replaced or
expired merely because time passed.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from cluster.infrastructure.storage import atomic_write_text


ACTIVE_RESERVATION_STATES = frozenset({"held", "releasing", "quarantined"})
RESERVATION_STATES = ACTIVE_RESERVATION_STATES | {"released"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResourceRegistryError(RuntimeError):
    """Base class for fail-closed registry errors."""


class ResourceRegistryCorruptionError(ResourceRegistryError):
    """The durable registry cannot be trusted."""


class ResourceConflictError(ResourceRegistryError):
    """An atomic reservation could not be admitted."""

    def __init__(self, code: str, reason: str, conflicts: Sequence[Mapping[str, Any]] = ()) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.conflicts = [dict(item) for item in conflicts]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "conflicts": self.conflicts}


@dataclass(frozen=True)
class WorkerResource:
    """Stable and physical identity for one requested Worker."""

    worker_id: str
    host: str
    api_port: int

    @property
    def canonical_host(self) -> str:
        host = self.host.strip().lower().rstrip(".")
        if host == "localhost" or host == "::1" or host.startswith("127."):
            return "loopback"
        return host

    @property
    def endpoint(self) -> str:
        return f"{self.canonical_host}:{self.api_port}"

    def keys(self, *, rpc: bool = False, coordinator: bool = False) -> tuple[str, ...]:
        host = self.canonical_host
        values = [
            f"worker:{self.worker_id}", f"host:{host}", f"endpoint:{self.endpoint}"
        ]
        if rpc:
            values.append(f"rpc-port:{host}:50052")
        if coordinator:
            values.append(f"rpc-port:{host}:18080")
        return tuple(values)

    def to_dict(self) -> dict[str, Any]:
        return {"worker_id": self.worker_id, "host": self.host, "api_port": self.api_port,
                "physical_endpoint": self.endpoint}


class FilesystemResourceCoordinator:
    """Atomic all-or-nothing resource admission shared by Controller processes."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.registry_path = self.directory / ".resource-registry.json"
        self.lock_path = self.directory / ".resource-registry.lock"
        self.controller_id_path = self.directory / ".controller-id"
        self._thread_lock = threading.RLock()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        self.controller_id = self._load_controller_id()

    def _load_controller_id(self) -> str:
        with self._locked():
            try:
                value = self.controller_id_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                value = "controller_" + secrets.token_hex(16)
                descriptor = os.open(
                    self.controller_id_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(value + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        if not value.startswith("controller_") or len(value) > 80:
            raise ResourceRegistryCorruptionError("controller identity is invalid")
        return value

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        try:
            with self._thread_lock:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _empty(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "artifact_type": "controller_resource_registry",
            "next_fencing_epoch": 1,
            "updated_at": utc_now(),
            "leases": [],
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return self._empty()
        if self.registry_path.is_symlink() or not self.registry_path.is_file():
            raise ResourceRegistryCorruptionError("resource registry must be a regular file")
        try:
            value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResourceRegistryCorruptionError("resource registry is unreadable") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("artifact_type") != "controller_resource_registry"
            or not isinstance(value.get("next_fencing_epoch"), int)
            or int(value["next_fencing_epoch"]) < 1
            or not isinstance(value.get("leases"), list)
        ):
            raise ResourceRegistryCorruptionError("resource registry schema is invalid")
        for lease in value["leases"]:
            if not isinstance(lease, dict) or lease.get("status") not in RESERVATION_STATES:
                raise ResourceRegistryCorruptionError("resource registry lease is invalid")
            if not isinstance(lease.get("resources"), list) or not lease.get("lease_id"):
                raise ResourceRegistryCorruptionError("resource registry lease identity is invalid")
        return value

    def _write_unlocked(self, value: Mapping[str, Any]) -> None:
        document = dict(value)
        document["updated_at"] = utc_now()
        atomic_write_text(
            self.registry_path,
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            default_mode=0o600,
        )

    @staticmethod
    def _active(value: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            dict(item) for item in value.get("leases", [])
            if isinstance(item, Mapping) and item.get("status") in ACTIVE_RESERVATION_STATES
        ]

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            return json.loads(json.dumps(self._read_unlocked()))

    def active(self) -> list[dict[str, Any]]:
        return self._active(self.snapshot())

    def acquire(
        self,
        *,
        owner_job_id: str,
        owner_attempt_id: str,
        workers: Sequence[WorkerResource],
        hold_reason: str,
        admission_limit: int = 1,
        exclusive: bool = False,
        kind: str = "job",
        rpc_coordinator_id: str = "",
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if admission_limit not in {1, 2}:
            raise ValueError("admission_limit must be 1 or 2")
        if not owner_job_id or not owner_attempt_id or not workers:
            raise ValueError("reservation owner and Worker set are required")
        worker_ids = [worker.worker_id for worker in workers]
        if len(worker_ids) != len(set(worker_ids)):
            raise ValueError("reservation Worker IDs must be unique")
        rpc = bool(rpc_coordinator_id)
        resources = sorted(
            {
                key
                for worker in workers
                for key in worker.keys(
                    rpc=rpc,
                    coordinator=rpc and worker.worker_id == rpc_coordinator_id,
                )
            }
        )
        endpoints = [worker.endpoint for worker in workers]
        if len(endpoints) != len(set(endpoints)):
            raise ResourceConflictError(
                "DUPLICATE_PHYSICAL_ENDPOINT",
                "Worker aliases resolve to the same physical endpoint",
            )

        with self._locked():
            registry = self._read_unlocked()
            active = self._active(registry)
            existing = next(
                (item for item in active if item.get("owner_job_id") == owner_job_id), None
            )
            if existing is not None:
                if (
                    existing.get("owner_attempt_id") == owner_attempt_id
                    and existing.get("resources") == resources
                    and bool(existing.get("exclusive")) == exclusive
                ):
                    return {**existing, "_acquired_new": False}
                raise ResourceConflictError(
                    "OWNER_RESERVATION_MISMATCH",
                    "The owner already has a different active reservation",
                    [existing],
                )

            requested = set(resources)
            conflicts = [
                item
                for item in active
                if exclusive
                or bool(item.get("exclusive"))
                or requested.intersection(item.get("resources") or [])
            ]
            if conflicts:
                raise ResourceConflictError(
                    "RESOURCE_BUSY",
                    "The complete Worker set cannot be reserved atomically",
                    conflicts,
                )
            running_jobs = [item for item in active if item.get("kind") == "job"]
            effective_limit = min(
                [admission_limit, *[int(item.get("admission_limit") or 1) for item in running_jobs]]
            )
            if kind == "job" and len(running_jobs) >= effective_limit:
                raise ResourceConflictError(
                    "CONTROLLER_ADMISSION_LIMIT",
                    f"Controller admission limit {effective_limit} is already in use",
                    running_jobs,
                )

            epoch = int(registry["next_fencing_epoch"])
            registry["next_fencing_epoch"] = epoch + 1
            lease = {
                "schema_version": 1,
                "lease_id": "lease_" + secrets.token_hex(16),
                "controller_id": self.controller_id,
                "owner_job_id": owner_job_id,
                "owner_attempt_id": owner_attempt_id,
                "fencing_epoch": epoch,
                "kind": kind,
                "exclusive": exclusive,
                "admission_limit": admission_limit,
                "hold_reason": hold_reason,
                "status": "held",
                "worker_ids": worker_ids,
                "physical_endpoints": endpoints,
                "workers": [worker.to_dict() for worker in workers],
                "resources": resources,
                "acquired_at": utc_now(),
                "heartbeat_at": utc_now(),
                "evidence": dict(evidence or {}),
            }
            registry["leases"].append(lease)
            self._write_unlocked(registry)
            return {**json.loads(json.dumps(lease)), "_acquired_new": True}

    @staticmethod
    def _match(lease: Mapping[str, Any], lease_id: str, owner_job_id: str, fencing_epoch: int) -> bool:
        return (
            lease.get("lease_id") == lease_id
            and lease.get("owner_job_id") == owner_job_id
            and lease.get("fencing_epoch") == fencing_epoch
        )

    def heartbeat(
        self, *, lease_id: str, owner_job_id: str, fencing_epoch: int,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._locked():
            registry = self._read_unlocked()
            lease = next(
                (item for item in registry["leases"] if self._match(item, lease_id, owner_job_id, fencing_epoch)),
                None,
            )
            if lease is None or lease.get("status") not in {"held", "releasing"}:
                raise ResourceConflictError("STALE_RESOURCE_OWNER", "Lease ownership no longer matches")
            lease["heartbeat_at"] = utc_now()
            if evidence:
                lease.setdefault("evidence", {}).update(dict(evidence))
            self._write_unlocked(registry)
            return dict(lease)

    def finish(
        self, *, lease_id: str, owner_job_id: str, fencing_epoch: int,
        cleanup_verified: bool, evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not evidence:
            raise ValueError("lease release requires cleanup evidence")
        with self._locked():
            registry = self._read_unlocked()
            lease = next(
                (item for item in registry["leases"] if self._match(item, lease_id, owner_job_id, fencing_epoch)),
                None,
            )
            if lease is None:
                raise ResourceConflictError("STALE_RESOURCE_OWNER", "Lease ownership no longer matches")
            if lease.get("status") == "released":
                return dict(lease)
            if lease.get("status") == "quarantined" and cleanup_verified:
                raise ResourceConflictError(
                    "RECONCILIATION_REQUIRED", "A quarantined lease needs explicit reconciliation"
                )
            lease["status"] = "released" if cleanup_verified else "quarantined"
            lease["release_started_at"] = lease.get("release_started_at") or utc_now()
            lease["released_at" if cleanup_verified else "quarantined_at"] = utc_now()
            lease["heartbeat_at"] = utc_now()
            lease.setdefault("evidence", {}).update(dict(evidence))
            self._write_unlocked(registry)
            return dict(lease)

    def quarantine(
        self, *, lease_id: str, owner_job_id: str, fencing_epoch: int,
        reason: str, evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.finish(
            lease_id=lease_id,
            owner_job_id=owner_job_id,
            fencing_epoch=fencing_epoch,
            cleanup_verified=False,
            evidence={"quarantine_reason": reason, **dict(evidence or {})},
        )

    def reconcile(
        self, *, lease_id: str, fencing_epoch: int, evidence: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not evidence:
            raise ValueError("explicit reconciliation evidence is required")
        with self._locked():
            registry = self._read_unlocked()
            lease = next(
                (item for item in registry["leases"] if item.get("lease_id") == lease_id), None
            )
            if lease is None or lease.get("fencing_epoch") != fencing_epoch:
                raise ResourceConflictError("STALE_RESOURCE_OWNER", "Lease fencing epoch no longer matches")
            if lease.get("status") != "quarantined":
                raise ResourceConflictError("NOT_QUARANTINED", "Only quarantined leases can be reconciled")
            lease["status"] = "released"
            lease["released_at"] = utc_now()
            lease["reconciled_at"] = utc_now()
            lease.setdefault("evidence", {})["reconciliation"] = dict(evidence)
            self._write_unlocked(registry)
            return dict(lease)

    def conflicts(self, workers: Sequence[WorkerResource]) -> list[dict[str, Any]]:
        keys = {key for worker in workers for key in worker.keys()}
        return [
            item for item in self.active()
            if item.get("exclusive") or keys.intersection(item.get("resources") or [])
        ]

    def quarantine_stale(self, *, heartbeat_before: str) -> list[dict[str, Any]]:
        """Quarantine stale leases; never infer cleanup or release from age."""
        changed: list[dict[str, Any]] = []
        with self._locked():
            registry = self._read_unlocked()
            for lease in registry["leases"]:
                if (
                    lease.get("status") == "held"
                    and str(lease.get("heartbeat_at") or "") < heartbeat_before
                ):
                    lease["status"] = "quarantined"
                    lease["quarantined_at"] = utc_now()
                    lease.setdefault("evidence", {})["quarantine_reason"] = "HEARTBEAT_STALE"
                    changed.append(dict(lease))
            if changed:
                self._write_unlocked(registry)
        return changed


__all__ = [
    "ACTIVE_RESERVATION_STATES",
    "FilesystemResourceCoordinator",
    "ResourceConflictError",
    "ResourceRegistryCorruptionError",
    "ResourceRegistryError",
    "WorkerResource",
]
