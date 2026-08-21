"""Model inventory aggregation and strict experiment preflight rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from cluster.domain.errors import ClusterError, ErrorCode
from cluster.domain.model import ModelCatalogEntry, ModelInventoryEntry


class ModelPreflightError(ClusterError, ValueError):
    """A start-blocking, structured model availability or integrity failure."""


@dataclass(frozen=True)
class WorkerModelInventory:
    node: str
    models: tuple[ModelInventoryEntry, ...]
    error: str = ""

    @property
    def online(self) -> bool:
        return not self.error

    def by_id(self) -> dict[str, ModelInventoryEntry]:
        return {model.id: model for model in self.models}

    def to_dict(self) -> dict[str, Any]:
        return {"node": self.node, "models": [model.to_dict() for model in self.models], "error": self.error}


def parse_worker_inventory(node: str, raw_models: Iterable[Mapping[str, Any]]) -> WorkerModelInventory:
    models: list[ModelInventoryEntry] = []
    for raw in raw_models:
        try:
            models.append(
                ModelInventoryEntry(
                    id=str(raw.get("id") or ""),
                    filename=str(raw.get("filename") or raw.get("name") or ""),
                    size_bytes=int(raw.get("size_bytes") or 0),
                    sha256=str(raw.get("sha256") or ""),
                    quantization=(str(raw["quantization"]) if raw.get("quantization") is not None else None),
                    checksum_valid=(
                        raw.get("checksum_valid")
                        if isinstance(raw.get("checksum_valid"), bool)
                        else False
                    ),
                    source_revision=str(raw.get("source_revision") or ""),
                    architecture=str(raw.get("architecture") or ""),
                    chat_template_hash=str(raw.get("chat_template_hash") or ""),
                    license_accepted=raw.get("license_accepted") is True,
                    metadata_inspected=raw.get("metadata_inspected") is True,
                )
            )
        except (TypeError, ValueError):
            # A malformed remote record is not silently treated as a usable model.
            continue
    return WorkerModelInventory(node=node, models=tuple(models))


def aggregate_catalog(
    inventories: Iterable[WorkerModelInventory], catalog: Iterable[ModelCatalogEntry]
) -> list[dict[str, Any]]:
    """Merge per-worker filesystem facts with independent catalog metadata."""
    catalog_by_id = {entry.id: entry for entry in catalog}
    observed: dict[str, dict[str, Any]] = {}
    for inventory in inventories:
        for model in inventory.models:
            item = observed.setdefault(
                model.id,
                {
                    "id": model.id,
                    "filename": model.filename,
                    "size_bytes": model.size_bytes,
                    "size_gb": round(model.size_bytes / (1024**3), 4),
                    "quantization": model.quantization,
                    "installed_nodes": [],
                    "checksums": {},
                    "inventories": {},
                },
            )
            item["installed_nodes"].append(inventory.node)
            item["checksums"][inventory.node] = model.sha256
            item["inventories"][inventory.node] = model.to_dict()
    result: list[dict[str, Any]] = []
    for model_id in sorted(set(catalog_by_id).union(observed)):
        entry = catalog_by_id.get(model_id)
        item = dict(observed.get(model_id) or {"id": model_id, "filename": model_id.rsplit("/", 1)[-1], "size_bytes": 0, "size_gb": 0.0, "quantization": None, "installed_nodes": [], "checksums": {}, "inventories": {}})
        if entry is not None:
            item["catalog"] = entry.to_dict()
            item["quantization"] = item.get("quantization") or entry.quantization
        else:
            item["catalog"] = None
        item["installed_nodes"] = sorted(item["installed_nodes"])
        item["available"] = bool(item["installed_nodes"])
        result.append(item)
    return result


def validate_model_preflight(
    *,
    node_names: Sequence[str],
    inventories: Mapping[str, WorkerModelInventory],
    model_ids: Sequence[str],
    execution_strategy: str,
    rpc_coordinator_node: Optional[str],
    catalog: Mapping[str, ModelCatalogEntry],
) -> None:
    """Reject absent, malformed, or checksum-mismatched model files before a job exists."""
    required_nodes = list(node_names)
    if execution_strategy == "model_parallel_rpc":
        required_nodes = [rpc_coordinator_node] if rpc_coordinator_node else []
    observed_checksums: dict[str, str] = {}
    for node_name in required_nodes:
        inventory = inventories.get(node_name)
        if inventory is None or not inventory.online:
            raise ModelPreflightError(
                f"{node_name}: worker model inventory is unavailable",
                code=ErrorCode.WORKER_OFFLINE,
                stage="model_preflight",
                node=node_name,
                evidence={"inventory_error": inventory.error if inventory else "missing"},
            )
        installed = inventory.by_id()
        for model_id in model_ids:
            model = installed.get(model_id)
            if model is None:
                raise ModelPreflightError(
                    f"{node_name}: model is missing: {model_id}",
                    code=ErrorCode.MODEL_MISSING,
                    stage="model_preflight",
                    node=node_name,
                    model_id=model_id,
                    evidence={"installed_model_ids": sorted(installed)},
                )
            catalog_entry = catalog.get(model_id)
            expected = catalog_entry.sha256 if catalog_entry else ""
            if not model.checksum_valid or (expected and model.sha256 != expected):
                raise ModelPreflightError(
                    f"{node_name}: model checksum is invalid: {model_id}",
                    code=ErrorCode.MODEL_CORRUPTED,
                    stage="model_preflight",
                    node=node_name,
                    model_id=model_id,
                    evidence={"expected_sha256": expected or None, "actual_sha256": model.sha256},
                )
            if catalog_entry is not None:
                if catalog_entry.quantization and model.quantization != catalog_entry.quantization:
                    raise ModelPreflightError(
                        f"{node_name}: model quantization differs from catalog: {model_id}",
                        code=ErrorCode.CONFIG_MISMATCH,
                        stage="model_preflight",
                        node=node_name,
                        model_id=model_id,
                        evidence={"expected_quantization": catalog_entry.quantization, "actual_quantization": model.quantization},
                    )
                if catalog_entry.identity_locked:
                    if model.source_revision != catalog_entry.hf_revision:
                        raise ModelPreflightError(
                            f"{node_name}: model revision differs from catalog: {model_id}",
                            code=ErrorCode.CONFIG_MISMATCH,
                            stage="model_preflight",
                            node=node_name,
                            model_id=model_id,
                            evidence={"expected_revision": catalog_entry.hf_revision, "actual_revision": model.source_revision or None},
                        )
                    if catalog_entry.architecture and model.architecture and model.architecture != catalog_entry.architecture:
                        raise ModelPreflightError(
                            f"{node_name}: model architecture differs from catalog: {model_id}",
                            code=ErrorCode.BACKEND_MISMATCH,
                            stage="model_preflight",
                            node=node_name,
                            model_id=model_id,
                            evidence={"expected_architecture": catalog_entry.architecture, "actual_architecture": model.architecture},
                        )
                    if catalog_entry.requires_license_acceptance and not model.license_accepted:
                        raise ModelPreflightError(
                            f"{node_name}: model license/access has not been accepted: {model_id}",
                            code=ErrorCode.CONFIG_MISMATCH,
                            stage="model_preflight",
                            node=node_name,
                            model_id=model_id,
                            evidence={"license": catalog_entry.license, "gated": catalog_entry.gated},
                            solutions=("Model Library에서 라이선스·접근 조건을 확인한 뒤 고정된 source metadata로 설치하세요.",),
                        )
            previous = observed_checksums.get(model_id)
            if previous is not None and previous != model.sha256:
                raise ModelPreflightError(
                    f"{node_name}: model checksum differs across selected workers: {model_id}",
                    code=ErrorCode.MODEL_CORRUPTED,
                    stage="model_preflight",
                    node=node_name,
                    model_id=model_id,
                    evidence={"expected_sha256": previous, "actual_sha256": model.sha256},
                )
            observed_checksums[model_id] = model.sha256


__all__ = ["ModelPreflightError", "WorkerModelInventory", "aggregate_catalog", "parse_worker_inventory", "validate_model_preflight"]
