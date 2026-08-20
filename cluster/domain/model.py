"""Pure model inventory, catalog, and deterministic recommendation rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

from .errors import DomainValidationError


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_QUANTIZATION_RE = re.compile(r"(?:^|[-_.])(Q\d(?:_[A-Z0-9]+)*)?(?:[-_.]|$)", re.IGNORECASE)


def validate_model_checksum(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise DomainValidationError("Model SHA-256 must be a lowercase 64-character hex digest")
    return normalized


def infer_quantization(filename: str) -> Optional[str]:
    """Extract a conservative GGUF quantization label from a filename only."""
    match = _QUANTIZATION_RE.search(filename.upper())
    return match.group(1).upper() if match and match.group(1) else None


@dataclass(frozen=True)
class ModelInventoryEntry:
    id: str
    filename: str
    size_bytes: int
    sha256: str
    quantization: Optional[str]
    checksum_valid: bool = True

    def __post_init__(self) -> None:
        from .experiment import validate_model_id

        validate_model_id(self.id)
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise DomainValidationError("Model filename cannot be empty")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise DomainValidationError("Model size_bytes must be a non-negative integer")
        object.__setattr__(self, "sha256", validate_model_checksum(self.sha256))
        if self.quantization is not None and not isinstance(self.quantization, str):
            raise DomainValidationError("Model quantization must be a string or null")
        if not isinstance(self.checksum_valid, bool):
            raise DomainValidationError("Model checksum_valid must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "quantization": self.quantization,
            "checksum_valid": self.checksum_valid,
        }


@dataclass(frozen=True)
class ModelCatalogEntry:
    """Metadata-only catalog record; it intentionally contains no local path."""

    id: str
    family: str = ""
    vendor: str = ""
    parameter_count_b: Optional[float] = None
    context_length: Optional[int] = None
    license: str = ""
    description: str = ""
    recommended_platforms: tuple[str, ...] = ()
    estimated_memory_mb: Optional[int] = None
    quantization: Optional[str] = None
    source_url: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        from .experiment import validate_model_id

        validate_model_id(self.id)
        if self.parameter_count_b is not None and self.parameter_count_b <= 0:
            raise DomainValidationError("parameter_count_b must be positive")
        if self.context_length is not None and self.context_length < 1:
            raise DomainValidationError("context_length must be positive")
        if self.estimated_memory_mb is not None and self.estimated_memory_mb < 1:
            raise DomainValidationError("estimated_memory_mb must be positive")
        if self.sha256:
            object.__setattr__(self, "sha256", validate_model_checksum(self.sha256))
        platforms = tuple(sorted({str(item).strip().lower() for item in self.recommended_platforms if str(item).strip()}))
        object.__setattr__(self, "recommended_platforms", platforms)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelCatalogEntry":
        return cls(
            id=str(raw.get("id") or ""), family=str(raw.get("family") or ""), vendor=str(raw.get("vendor") or ""),
            parameter_count_b=float(raw["parameter_count_b"]) if raw.get("parameter_count_b") is not None else None,
            context_length=int(raw["context_length"]) if raw.get("context_length") is not None else None,
            license=str(raw.get("license") or ""), description=str(raw.get("description") or ""),
            recommended_platforms=tuple(raw.get("recommended_platforms") or ()),
            estimated_memory_mb=int(raw["estimated_memory_mb"]) if raw.get("estimated_memory_mb") is not None else None,
            quantization=str(raw["quantization"]) if raw.get("quantization") is not None else None,
            source_url=str(raw.get("source_url") or ""), sha256=str(raw.get("sha256") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "family": self.family, "vendor": self.vendor,
            "parameter_count_b": self.parameter_count_b, "context_length": self.context_length,
            "license": self.license, "description": self.description,
            "recommended_platforms": list(self.recommended_platforms),
            "estimated_memory_mb": self.estimated_memory_mb, "quantization": self.quantization,
            "source_url": self.source_url, "sha256": self.sha256,
        }


def recommend_models(
    entries: Iterable[ModelCatalogEntry], *, platform: str, memory_total_mb: Optional[int], limit: int = 5
) -> list[ModelCatalogEntry]:
    """Return a stable platform/memory rule ranking without an LLM or network I/O."""
    normalized_platform = str(platform or "").strip().lower()
    memory = int(memory_total_mb) if memory_total_mb is not None else None

    def rank(entry: ModelCatalogEntry) -> tuple[int, int, int, int, str]:
        platform_match = 0 if not entry.recommended_platforms or normalized_platform in entry.recommended_platforms else 1
        if memory is None:
            memory_fit = 1 if entry.estimated_memory_mb is None else 0
        elif entry.estimated_memory_mb is None:
            memory_fit = 1
        else:
            memory_fit = 0 if entry.estimated_memory_mb <= int(memory * 0.75) else 2
        quantization = (entry.quantization or "").upper()
        if normalized_platform == "raspberry-pi":
            quantization_fit = 0 if quantization.startswith(("Q2", "Q3", "Q4", "Q5")) else 1
        else:
            quantization_fit = 0 if quantization.startswith(("Q3", "Q4", "Q5", "Q6")) else 1
        estimated = entry.estimated_memory_mb if entry.estimated_memory_mb is not None else 1_000_000_000
        return (platform_match, memory_fit, quantization_fit, estimated, entry.id)

    return sorted(entries, key=rank)[: max(0, limit)]


__all__ = ["ModelCatalogEntry", "ModelInventoryEntry", "infer_quantization", "recommend_models", "validate_model_checksum"]
