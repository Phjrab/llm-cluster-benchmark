"""Pure compatibility adapter for legacy head/worker inventory records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Tuple

from cluster.domain.errors import DomainValidationError
from cluster.domain.worker import (
    WorkerInventory,
    WorkerNode,
    WorkerPlatform,
    validate_worker_host,
    validate_worker_project_dir,
)


_MISSING = object()


@dataclass(frozen=True)
class LegacyNodeRecord:
    name: str
    role: str
    host: str
    user: str
    ssh_port: int
    api_port: int
    project_dir: str
    enabled: bool
    identity_file: Optional[str] = None
    platform: WorkerPlatform = WorkerPlatform.AUTO


@dataclass(frozen=True)
class LegacyInventoryConversion:
    workers: WorkerInventory
    excluded_heads: Tuple[str, ...]
    legacy_head_records: Tuple[LegacyNodeRecord, ...]
    legacy_worker_records: Tuple[LegacyNodeRecord, ...]
    unresolved_workers: Tuple[LegacyNodeRecord, ...]
    warnings: Tuple[str, ...]

    @property
    def legacy_head(self) -> Optional[LegacyNodeRecord]:
        return self.legacy_head_records[0] if len(self.legacy_head_records) == 1 else None


def _value(row: Any, key: str, default: Any = _MISSING) -> Any:
    if isinstance(row, Mapping):
        if key in row:
            return row[key]
    else:
        value = getattr(row, key, _MISSING)
        if value is not _MISSING:
            return value
    if default is not _MISSING:
        return default
    raise DomainValidationError(f"Legacy inventory row is missing {key}")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def adapt_legacy_inventory(rows: Iterable[Any]) -> LegacyInventoryConversion:
    """Convert legacy records without turning a legacy head into a participant."""
    workers = []
    heads = []
    worker_records = []
    unresolved_workers = []
    names = []
    warnings = []

    for line_number, row in enumerate(rows, start=1):
        name = str(_value(row, "name", "")).strip()
        if not name:
            continue
        role = str(_value(row, "role")).strip().lower()
        if role not in {"head", "worker"}:
            raise DomainValidationError(f"Invalid legacy role for {name}: {role}")
        platform_raw = str(_value(row, "platform", "auto") or "auto").strip().lower()
        try:
            platform = WorkerPlatform(platform_raw)
            ssh_port = int(_value(row, "ssh_port"))
            api_port = int(_value(row, "api_port"))
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"Invalid legacy inventory row {line_number}: {exc}") from exc
        identity = str(_value(row, "identity_file", "") or "").strip() or None
        record = LegacyNodeRecord(
            name=name,
            role=role,
            host=str(_value(row, "host")).strip(),
            user=str(_value(row, "user")).strip(),
            ssh_port=ssh_port,
            api_port=api_port,
            project_dir=str(_value(row, "project_dir")).strip(),
            enabled=_as_bool(_value(row, "enabled")),
            identity_file=identity,
            platform=platform,
        )
        if not record.host:
            raise DomainValidationError(f"Host is empty for {name}")
        if not 1 <= record.ssh_port <= 65535 or not 1 <= record.api_port <= 65535:
            raise DomainValidationError(f"Ports must be between 1 and 65535 for {name}")
        validate_worker_project_dir(record.project_dir, record.user)
        names.append(name)
        if role == "head":
            heads.append(record)
            warnings.append(
                f"Legacy head '{name}' was excluded from Worker inventory and was not converted to Controller"
            )
            continue
        worker_records.append(record)
        try:
            validate_worker_host(record.host)
        except DomainValidationError:
            unresolved_workers.append(record)
            warnings.append(
                f"Legacy worker '{name}' was preserved but not activated because its host is not a private IPv4 address"
            )
            continue
        if record.identity_file:
            warnings.append(
                f"Legacy worker '{name}' identity_file was preserved as migration metadata and requires explicit re-keying"
            )
        workers.append(
            WorkerNode(
                name=record.name,
                host=record.host,
                user=record.user,
                ssh_port=record.ssh_port,
                api_port=record.api_port,
                project_dir=record.project_dir,
                enabled=record.enabled,
                identity_file=None,
                platform=record.platform,
            )
        )

    if len(names) != len(set(names)):
        raise DomainValidationError("Legacy inventory contains duplicate node names")
    if sum(1 for head in heads if head.enabled) > 1:
        raise DomainValidationError("Legacy inventory contains more than one enabled head")
    return LegacyInventoryConversion(
        workers=WorkerInventory(tuple(workers)),
        excluded_heads=tuple(head.name for head in heads),
        legacy_head_records=tuple(heads),
        legacy_worker_records=tuple(worker_records),
        unresolved_workers=tuple(unresolved_workers),
        warnings=tuple(warnings),
    )


__all__ = [
    "LegacyInventoryConversion",
    "LegacyNodeRecord",
    "adapt_legacy_inventory",
]
