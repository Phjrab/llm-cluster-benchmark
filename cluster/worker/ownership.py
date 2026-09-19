"""Durable Worker-side ownership fencing for mutating operations."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from cluster.infrastructure.storage import atomic_write_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkerOwnershipError(RuntimeError):
    pass


class WorkerOwnershipRegistry:
    """One durable owner per Worker; ownership never expires by TTL."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._thread_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with self._thread_lock:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _read(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerOwnershipError("WORKER_OWNERSHIP_CORRUPT") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkerOwnershipError("WORKER_OWNERSHIP_CORRUPT")
        return value

    @staticmethod
    def _identity(value: Mapping[str, Any]) -> tuple[str, str, int]:
        try:
            return (
                str(value["owner_id"]), str(value["lease_id"]), int(value["fencing_epoch"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkerOwnershipError("INVALID_OWNER_CONTEXT") from exc

    def acquire(self, context: Mapping[str, Any]) -> dict[str, Any]:
        identity = self._identity(context)
        if not all(identity[:2]) or identity[2] < 1:
            raise WorkerOwnershipError("INVALID_OWNER_CONTEXT")
        with self._locked():
            current = self._read()
            if current is not None and current.get("status") in {"held", "quarantined"}:
                if self._identity(current) != identity:
                    raise WorkerOwnershipError("WORKER_OWNED_BY_ANOTHER_ATTEMPT")
                return current
            value = {
                "schema_version": 1,
                "artifact_type": "worker_resource_owner",
                "owner_id": identity[0],
                "lease_id": identity[1],
                "fencing_epoch": identity[2],
                "status": "held",
                "acquired_at": _now(),
                "heartbeat_at": _now(),
            }
            atomic_write_text(
                self.path,
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                default_mode=0o600,
            )
            return value

    def require(self, context: Mapping[str, Any] | None) -> None:
        with self._locked():
            current = self._read()
            if current is None or current.get("status") == "released":
                return
            if context is None or self._identity(current) != self._identity(context):
                raise WorkerOwnershipError("STALE_OR_MISSING_OWNER_CONTEXT")
            if current.get("status") != "held":
                raise WorkerOwnershipError("WORKER_OWNERSHIP_QUARANTINED")

    def release(self, context: Mapping[str, Any], *, cleanup_verified: bool) -> dict[str, Any]:
        identity = self._identity(context)
        with self._locked():
            current = self._read()
            if current is None or self._identity(current) != identity:
                raise WorkerOwnershipError("STALE_RESOURCE_OWNER")
            if current.get("status") == "released":
                return current
            current["status"] = "released" if cleanup_verified else "quarantined"
            current["released_at" if cleanup_verified else "quarantined_at"] = _now()
            atomic_write_text(
                self.path,
                json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                default_mode=0o600,
            )
            return current

    def status(self) -> dict[str, Any] | None:
        with self._locked():
            current = self._read()
            return json.loads(json.dumps(current)) if current is not None else None


__all__ = ["WorkerOwnershipError", "WorkerOwnershipRegistry"]
