"""Pure model catalog, inventory, memory-fit, and recommendation rules.

The catalog describes *candidate* GGUF artifacts. A catalog record never means
that a model has been downloaded or is ready to benchmark on a Worker. Runtime
readiness is determined from Worker inventory, capability, identity locks, and
platform smoke evidence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from .errors import DomainValidationError


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_QUANTIZATION_RE = re.compile(r"^(?:Q[2-8](?:_[01]|_K(?:_[SML])?)?|IQ[1-4](?:_[A-Z0-9]+)?|F16|BF16)$", re.IGNORECASE)
_FILENAME_QUANTIZATION_RE = re.compile(r"(?:^|[-_.])(Q\d(?:_[A-Z0-9]+)*)?(?:[-_.]|$)", re.IGNORECASE)
_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")


class ModelTier(str, Enum):
    CORE_STABLE = "core_stable"
    CORE_MODERN = "core_modern"
    EXPERIMENTAL_EDGE = "experimental_edge"
    OPTIONAL_REFERENCE = "optional_reference"


class ModelVerificationStatus(str, Enum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    RECOMMENDED = "recommended"
    COMPATIBLE = "compatible"
    STRESS_TEST = "stress_test"
    RPC_ONLY = "rpc_only"
    UNSUPPORTED = "unsupported"
    DEPRECATED = "deprecated"


class ProvenanceStatus(str, Enum):
    OFFICIAL = "official"
    COMMUNITY_REVIEW = "community_review"
    UNKNOWN = "unknown"


def _as_tuple(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, set)):
        return ()
    return tuple(str(value).strip() for value in values if isinstance(value, str) and value.strip())


def _positive_float(value: Any, field: str, *, allow_none: bool = True) -> Optional[float]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) <= 0:
        raise DomainValidationError(f"{field} must be a positive finite number")
    return float(value)


def _positive_int(value: Any, field: str, *, allow_none: bool = True, minimum: int = 1) -> Optional[int]:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise DomainValidationError(f"{field} must be an integer >= {minimum}")
    return value


def validate_model_checksum(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise DomainValidationError("Model SHA-256 must be a lowercase 64-character hex digest")
    return normalized


def validate_quantization(value: str) -> str:
    normalized = str(value or "").strip().upper()
    if not _QUANTIZATION_RE.fullmatch(normalized):
        raise DomainValidationError(f"Unsupported GGUF quantization: {value}")
    return normalized


def infer_quantization(filename: str) -> Optional[str]:
    """Extract a conservative GGUF quantization label from a filename only."""
    match = _FILENAME_QUANTIZATION_RE.search(filename.upper())
    return match.group(1).upper() if match and match.group(1) else None


@dataclass(frozen=True)
class ModelInventoryEntry:
    id: str
    filename: str
    size_bytes: int
    sha256: str
    quantization: Optional[str]
    checksum_valid: bool = True
    source_revision: str = ""
    architecture: str = ""
    chat_template_hash: str = ""
    license_accepted: bool = False
    metadata_inspected: bool = False

    def __post_init__(self) -> None:
        from .experiment import validate_model_id

        validate_model_id(self.id)
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise DomainValidationError("Model filename cannot be empty")
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise DomainValidationError("Model size_bytes must be a non-negative integer")
        object.__setattr__(self, "sha256", validate_model_checksum(self.sha256))
        if self.quantization is not None:
            object.__setattr__(self, "quantization", validate_quantization(self.quantization))
        for field in ("source_revision", "architecture", "chat_template_hash"):
            if not isinstance(getattr(self, field), str):
                raise DomainValidationError(f"Model {field} must be a string")
        if not isinstance(self.checksum_valid, bool) or not isinstance(self.license_accepted, bool) or not isinstance(self.metadata_inspected, bool):
            raise DomainValidationError("Model inventory booleans must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "filename": self.filename, "size_bytes": self.size_bytes,
            "sha256": self.sha256, "quantization": self.quantization,
            "checksum_valid": self.checksum_valid, "source_revision": self.source_revision,
            "architecture": self.architecture, "chat_template_hash": self.chat_template_hash,
            "license_accepted": self.license_accepted, "metadata_inspected": self.metadata_inspected,
        }


@dataclass(frozen=True)
class ModelCatalogEntry:
    """Metadata-only catalog record; it intentionally contains no local path."""

    id: str
    display_name: str = ""
    family: str = ""
    vendor: str = ""
    architecture: str = ""
    parameter_count_b: Optional[float] = None  # legacy alias for total count
    parameters_total_b: Optional[float] = None
    parameters_effective_b: Optional[float] = None
    parameter_reporting_note: str = ""
    instruction_tuned: bool = True
    modalities: tuple[str, ...] = ("text",)
    supported_languages: tuple[str, ...] = ()
    korean_support: str = "unknown"
    context_length: Optional[int] = None  # legacy alias for advertised context
    context_length_advertised: Optional[int] = None
    default_context: int = 4096
    verified_context_lengths: tuple[int, ...] = ()
    supports_reasoning_modes: bool = False
    reasoning_modes: tuple[str, ...] = ()
    default_reasoning_mode: Optional[str] = None
    hf_repo: str = ""
    hf_revision: str = ""
    gguf_filename: str = ""
    size_bytes: Optional[int] = None
    sha256: str = ""
    official_gguf: bool = False
    provenance_status: ProvenanceStatus | str = ProvenanceStatus.UNKNOWN
    license: str = ""
    license_review_required: bool = False
    gated: bool = False
    source_url: str = ""
    recommended_platforms: tuple[str, ...] = ()
    recommendation_tier: ModelTier | str = ModelTier.CORE_STABLE
    benchmark_roles: tuple[str, ...] = ()
    estimated_memory_mb: Optional[int] = None  # legacy fallback, never preferred over size_bytes
    kv_cache_bytes_per_token: Optional[int] = None
    compute_buffers_mb: int = 256
    backend_overhead_mb: int = 256
    quantization: Optional[str] = None
    description: str = ""  # legacy English description
    summary_ko: str = ""
    recommendation_reason_ko: tuple[str, ...] = ()
    cautions_ko: tuple[str, ...] = ()
    verification_status: ModelVerificationStatus | str = ModelVerificationStatus.CANDIDATE
    verified_llama_cpp_commits: tuple[str, ...] = ()
    verified_platforms: tuple[str, ...] = ()
    last_verified_at: Optional[str] = None

    def __post_init__(self) -> None:
        from .experiment import validate_model_id

        validate_model_id(self.id)
        for field in ("display_name", "family", "vendor", "architecture", "parameter_reporting_note", "korean_support", "hf_repo", "hf_revision", "gguf_filename", "license", "source_url", "description", "summary_ko"):
            if not isinstance(getattr(self, field), str):
                raise DomainValidationError(f"Model {field} must be a string")
        total = self.parameters_total_b if self.parameters_total_b is not None else self.parameter_count_b
        total = _positive_float(total, "parameters_total_b")
        effective = _positive_float(self.parameters_effective_b, "parameters_effective_b")
        object.__setattr__(self, "parameters_total_b", total)
        object.__setattr__(self, "parameter_count_b", total)
        object.__setattr__(self, "parameters_effective_b", effective)
        advertised = self.context_length_advertised if self.context_length_advertised is not None else self.context_length
        advertised = _positive_int(advertised, "context_length_advertised")
        object.__setattr__(self, "context_length_advertised", advertised)
        object.__setattr__(self, "context_length", advertised)
        if not isinstance(self.default_context, int) or isinstance(self.default_context, bool) or self.default_context < 128:
            raise DomainValidationError("default_context must be an integer >= 128")
        if advertised is not None and self.default_context > advertised:
            raise DomainValidationError("default_context cannot exceed context_length_advertised")
        object.__setattr__(self, "size_bytes", _positive_int(self.size_bytes, "size_bytes", minimum=1))
        object.__setattr__(self, "estimated_memory_mb", _positive_int(self.estimated_memory_mb, "estimated_memory_mb", minimum=1))
        object.__setattr__(self, "kv_cache_bytes_per_token", _positive_int(self.kv_cache_bytes_per_token, "kv_cache_bytes_per_token", minimum=1))
        for field in ("compute_buffers_mb", "backend_overhead_mb"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise DomainValidationError(f"{field} must be a non-negative integer")
        if self.hf_repo and not _HF_REPO_RE.fullmatch(self.hf_repo):
            raise DomainValidationError("hf_repo must be a Hugging Face owner/repository identifier")
        object.__setattr__(self, "quantization", validate_quantization(self.quantization) if self.quantization is not None else infer_quantization(self.gguf_filename or self.id))
        if self.sha256:
            object.__setattr__(self, "sha256", validate_model_checksum(self.sha256))
        if not isinstance(self.official_gguf, bool) or not isinstance(self.license_review_required, bool) or not isinstance(self.gated, bool) or not isinstance(self.instruction_tuned, bool) or not isinstance(self.supports_reasoning_modes, bool):
            raise DomainValidationError("Model catalog booleans must be boolean")
        try:
            object.__setattr__(self, "provenance_status", ProvenanceStatus(self.provenance_status))
            object.__setattr__(self, "recommendation_tier", ModelTier(self.recommendation_tier))
            object.__setattr__(self, "verification_status", ModelVerificationStatus(self.verification_status))
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("Unsupported catalog status value") from exc
        if self.official_gguf and self.provenance_status is not ProvenanceStatus.OFFICIAL:
            raise DomainValidationError("official_gguf requires official provenance_status")
        object.__setattr__(self, "modalities", _as_tuple(self.modalities))
        object.__setattr__(self, "supported_languages", _as_tuple(self.supported_languages))
        object.__setattr__(self, "recommended_platforms", tuple(sorted(set(_as_tuple(self.recommended_platforms)))))
        object.__setattr__(self, "benchmark_roles", _as_tuple(self.benchmark_roles))
        object.__setattr__(self, "recommendation_reason_ko", _as_tuple(self.recommendation_reason_ko))
        object.__setattr__(self, "cautions_ko", _as_tuple(self.cautions_ko))
        object.__setattr__(self, "verified_llama_cpp_commits", tuple(sorted(set(_as_tuple(self.verified_llama_cpp_commits)))))
        object.__setattr__(self, "verified_platforms", tuple(sorted(set(_as_tuple(self.verified_platforms)))))
        contexts = self.verified_context_lengths
        if not isinstance(contexts, (list, tuple, set)) or any(not isinstance(value, int) or isinstance(value, bool) or value < 128 for value in contexts):
            raise DomainValidationError("verified_context_lengths must contain integer contexts >= 128")
        object.__setattr__(self, "verified_context_lengths", tuple(sorted(set(contexts))))
        modes = _as_tuple(self.reasoning_modes)
        if self.supports_reasoning_modes and not modes:
            raise DomainValidationError("reasoning_modes are required when supports_reasoning_modes is true")
        if self.default_reasoning_mode is not None and self.default_reasoning_mode not in modes:
            raise DomainValidationError("default_reasoning_mode must be included in reasoning_modes")
        object.__setattr__(self, "reasoning_modes", modes)

    @property
    def identity_locked(self) -> bool:
        return bool(self.hf_repo and self.hf_revision and self.gguf_filename and self.sha256)

    @property
    def requires_license_acceptance(self) -> bool:
        return self.license_review_required or self.gated or not bool(self.license)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelCatalogEntry":
        verification = raw.get("verification") if isinstance(raw.get("verification"), Mapping) else {}
        return cls(
            id=str(raw.get("id") or ""), display_name=str(raw.get("display_name") or ""), family=str(raw.get("family") or ""), vendor=str(raw.get("vendor") or ""), architecture=str(raw.get("architecture") or ""),
            parameter_count_b=raw.get("parameter_count_b"), parameters_total_b=raw.get("parameters_total_b"), parameters_effective_b=raw.get("parameters_effective_b"), parameter_reporting_note=str(raw.get("parameter_reporting_note") or ""),
            instruction_tuned=raw.get("instruction_tuned", True), modalities=tuple(raw.get("modalities") or ("text",)), supported_languages=tuple(raw.get("supported_languages") or ()), korean_support=str(raw.get("korean_support") or "unknown"),
            context_length=raw.get("context_length"), context_length_advertised=raw.get("context_length_advertised"), default_context=int(raw.get("default_context", 4096)), verified_context_lengths=tuple(raw.get("verified_context_lengths") or ()),
            supports_reasoning_modes=raw.get("supports_reasoning_modes", False), reasoning_modes=tuple(raw.get("reasoning_modes") or ()), default_reasoning_mode=raw.get("default_reasoning_mode"),
            hf_repo=str(raw.get("hf_repo") or ""), hf_revision=str(raw.get("hf_revision") or ""), gguf_filename=str(raw.get("gguf_filename") or ""), size_bytes=raw.get("size_bytes"), sha256=str(raw.get("sha256") or ""),
            official_gguf=raw.get("official_gguf", False), provenance_status=raw.get("provenance_status", "unknown"), license=str(raw.get("license") or ""), license_review_required=raw.get("license_review_required", False), gated=raw.get("gated", False), source_url=str(raw.get("source_url") or ""),
            recommended_platforms=tuple(raw.get("recommended_platforms") or ()), recommendation_tier=raw.get("recommendation_tier", "core_stable"), benchmark_roles=tuple(raw.get("benchmark_roles") or ()), estimated_memory_mb=raw.get("estimated_memory_mb"), kv_cache_bytes_per_token=raw.get("kv_cache_bytes_per_token"), compute_buffers_mb=int(raw.get("compute_buffers_mb", 256)), backend_overhead_mb=int(raw.get("backend_overhead_mb", 256)),
            quantization=(str(raw["quantization"]) if raw.get("quantization") is not None else None), description=str(raw.get("description") or ""), summary_ko=str(raw.get("summary_ko") or ""), recommendation_reason_ko=tuple(raw.get("recommendation_reason_ko") or ()), cautions_ko=tuple(raw.get("cautions_ko") or ()),
            verification_status=verification.get("status", raw.get("verification_status", "candidate")), verified_llama_cpp_commits=tuple(verification.get("verified_llama_cpp_commits", raw.get("verified_llama_cpp_commits", ())) or ()), verified_platforms=tuple(verification.get("verified_platforms", raw.get("verified_platforms", ())) or ()), last_verified_at=verification.get("last_verified_at", raw.get("last_verified_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "display_name": self.display_name, "family": self.family, "vendor": self.vendor, "architecture": self.architecture,
            "parameter_count_b": self.parameter_count_b, "parameters_total_b": self.parameters_total_b, "parameters_effective_b": self.parameters_effective_b, "parameter_reporting_note": self.parameter_reporting_note,
            "instruction_tuned": self.instruction_tuned, "modalities": list(self.modalities), "supported_languages": list(self.supported_languages), "korean_support": self.korean_support,
            "context_length": self.context_length, "context_length_advertised": self.context_length_advertised, "default_context": self.default_context, "verified_context_lengths": list(self.verified_context_lengths),
            "supports_reasoning_modes": self.supports_reasoning_modes, "reasoning_modes": list(self.reasoning_modes), "default_reasoning_mode": self.default_reasoning_mode,
            "hf_repo": self.hf_repo, "hf_revision": self.hf_revision, "gguf_filename": self.gguf_filename, "quantization": self.quantization, "size_bytes": self.size_bytes, "sha256": self.sha256,
            "official_gguf": self.official_gguf, "provenance_status": self.provenance_status.value, "license": self.license, "license_review_required": self.license_review_required, "gated": self.gated, "source_url": self.source_url,
            "recommended_platforms": list(self.recommended_platforms), "recommendation_tier": self.recommendation_tier.value, "benchmark_roles": list(self.benchmark_roles), "estimated_memory_mb": self.estimated_memory_mb,
            "kv_cache_bytes_per_token": self.kv_cache_bytes_per_token, "compute_buffers_mb": self.compute_buffers_mb, "backend_overhead_mb": self.backend_overhead_mb,
            "description": self.description, "summary_ko": self.summary_ko, "recommendation_reason_ko": list(self.recommendation_reason_ko), "cautions_ko": list(self.cautions_ko), "identity_locked": self.identity_locked,
            "verification": {"status": self.verification_status.value, "verified_llama_cpp_commits": list(self.verified_llama_cpp_commits), "verified_platforms": list(self.verified_platforms), "last_verified_at": self.last_verified_at},
        }


@dataclass(frozen=True)
class MemoryFitEstimate:
    required_mb: Optional[int]
    safe_available_mb: Optional[int]
    system_reserve_mb: Optional[int]
    gguf_mapped_mb: Optional[int]
    kv_cache_mb: Optional[int]
    compute_buffers_mb: Optional[int]
    backend_overhead_mb: Optional[int]
    metadata_mb: int
    fits: Optional[bool]
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_mb": self.required_mb, "safe_available_mb": self.safe_available_mb, "system_reserve_mb": self.system_reserve_mb,
            "gguf_mapped_mb": self.gguf_mapped_mb, "kv_cache_mb": self.kv_cache_mb, "compute_buffers_mb": self.compute_buffers_mb,
            "backend_overhead_mb": self.backend_overhead_mb, "metadata_mb": self.metadata_mb, "fits": self.fits, "reason": self.reason,
        }


@dataclass(frozen=True)
class ModelRecommendation:
    model_id: str
    status: ModelVerificationStatus
    reasons_ko: tuple[str, ...]
    cautions_ko: tuple[str, ...]
    memory: MemoryFitEstimate
    catalog: ModelCatalogEntry

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.model_id, "status": self.status.value, "reasons_ko": list(self.reasons_ko), "cautions_ko": list(self.cautions_ko), "memory": self.memory.to_dict(), "catalog": self.catalog.to_dict()}


def estimate_memory_fit(
    entry: ModelCatalogEntry, *, memory_total_mb: Optional[float], memory_available_mb: Optional[float], context_length: Optional[int] = None,
    observed_size_bytes: Optional[int] = None, reserve_ratio: float = 0.20, reserve_min_mb: int = 1024,
) -> MemoryFitEstimate:
    """Estimate GGUF + KV + runtime costs; never derive fit from parameter count."""
    total = float(memory_total_mb) if isinstance(memory_total_mb, (int, float)) and not isinstance(memory_total_mb, bool) and memory_total_mb > 0 else None
    available = float(memory_available_mb) if isinstance(memory_available_mb, (int, float)) and not isinstance(memory_available_mb, bool) and memory_available_mb > 0 else total
    size = observed_size_bytes if isinstance(observed_size_bytes, int) and observed_size_bytes > 0 else entry.size_bytes
    if size is None:
        return MemoryFitEstimate(None, None, None, None, None, None, None, 128, None, "GGUF 파일 크기 메타데이터가 없어 적합성을 계산할 수 없습니다.")
    mapped = math.ceil(size / (1024 * 1024))
    if total is None or available is None:
        return MemoryFitEstimate(None, None, None, mapped, None, None, None, 128, None, "Worker RAM 정보가 없어 적합성을 계산할 수 없습니다.")
    reserve = max(math.ceil(total * reserve_ratio), reserve_min_mb)
    safe_available = max(0, math.floor(available - reserve))
    ctx = context_length or entry.default_context
    kv_mb = math.ceil((entry.kv_cache_bytes_per_token or 0) * ctx / (1024 * 1024))
    required = mapped + kv_mb + entry.compute_buffers_mb + entry.backend_overhead_mb + 128
    return MemoryFitEstimate(required, safe_available, reserve, mapped, kv_mb, entry.compute_buffers_mb, entry.backend_overhead_mb, 128, required <= safe_available, "")


def recommend_model_candidates(
    entries: Iterable[ModelCatalogEntry], *, platform: str, memory_total_mb: Optional[float], memory_available_mb: Optional[float], backend_verified: bool,
    runtime_commit: str = "", installed_models: Mapping[str, ModelInventoryEntry] | None = None, context_length: int = 4096,
    execution_strategy: str = "replicated_round_robin",
) -> list[ModelRecommendation]:
    """Return explainable Worker-only recommendations without network or LLM use."""
    normalized_platform = str(platform or "").strip().lower()
    installed = dict(installed_models or {})
    values: list[ModelRecommendation] = []
    for entry in entries:
        observed = installed.get(entry.id)
        fit = estimate_memory_fit(entry, memory_total_mb=memory_total_mb, memory_available_mb=memory_available_mb, context_length=context_length, observed_size_bytes=observed.size_bytes if observed else None)
        reasons, cautions = list(entry.recommendation_reason_ko), list(entry.cautions_ko)
        if normalized_platform in {"controller", "mac", "macos", ""}:
            status = ModelVerificationStatus.UNSUPPORTED; cautions.append("Mac Controller는 추론 및 모델 추천 대상이 아닙니다.")
        elif entry.recommended_platforms and normalized_platform not in entry.recommended_platforms:
            status = ModelVerificationStatus.UNSUPPORTED; cautions.append("이 모델은 선택한 Worker 플랫폼의 기본 대상이 아닙니다.")
        elif entry.benchmark_roles == ("model_parallel_rpc",) and execution_strategy != "model_parallel_rpc":
            status = ModelVerificationStatus.RPC_ONLY; cautions.append("이 모델은 model-parallel RPC 실험용으로만 분류됩니다.")
        elif entry.requires_license_acceptance:
            status = ModelVerificationStatus.CANDIDATE; cautions.append("라이선스 또는 접근 조건을 확인·수락한 뒤에만 설치할 수 있습니다.")
        elif not backend_verified:
            status = ModelVerificationStatus.UNSUPPORTED; cautions.append("Worker inference backend가 검증되지 않았습니다.")
        elif fit.fits is False:
            status = ModelVerificationStatus.STRESS_TEST; cautions.append("현재 RAM/컨텍스트 추정치가 안전 여유를 초과합니다.")
        elif fit.fits is None:
            status = ModelVerificationStatus.CANDIDATE; cautions.append(fit.reason)
        elif not entry.identity_locked:
            status = ModelVerificationStatus.CANDIDATE; cautions.append("고정 revision·파일명·SHA-256이 없어 재현 가능한 설치 대상으로 아직 잠기지 않았습니다.")
        else:
            smoke = normalized_platform in entry.verified_platforms and bool(runtime_commit) and runtime_commit in entry.verified_llama_cpp_commits
            if smoke and entry.verification_status in {ModelVerificationStatus.VERIFIED, ModelVerificationStatus.RECOMMENDED}:
                status = ModelVerificationStatus.RECOMMENDED; reasons.append("현재 Worker 플랫폼과 pinned runtime에서 smoke 검증된 조합입니다.")
            else:
                status = ModelVerificationStatus.COMPATIBLE; cautions.append("현재 플랫폼/pinned runtime smoke 검증 전에는 권장 상태로 승격되지 않습니다.")
        values.append(ModelRecommendation(entry.id, status, tuple(dict.fromkeys(reasons or ["카탈로그 메타데이터와 Worker capability로 계산한 결정론적 판정입니다."])), tuple(dict.fromkeys(cautions)), fit, entry))
    rank = {ModelVerificationStatus.RECOMMENDED: 0, ModelVerificationStatus.COMPATIBLE: 1, ModelVerificationStatus.CANDIDATE: 2, ModelVerificationStatus.STRESS_TEST: 3, ModelVerificationStatus.RPC_ONLY: 4, ModelVerificationStatus.UNSUPPORTED: 5, ModelVerificationStatus.DEPRECATED: 6, ModelVerificationStatus.VERIFIED: 1}
    return sorted(values, key=lambda item: (rank[item.status], item.memory.required_mb if item.memory.required_mb is not None else 10**12, item.model_id))


def recommend_models(entries: Iterable[ModelCatalogEntry], *, platform: str, memory_total_mb: Optional[int], limit: int = 5) -> list[ModelCatalogEntry]:
    """Legacy compact ranking API retained for existing callers and clients."""
    detailed = recommend_model_candidates(entries, platform=platform, memory_total_mb=memory_total_mb, memory_available_mb=memory_total_mb, backend_verified=True)
    return [item.catalog for item in detailed[: max(0, limit)]]


def parse_catalog_entries(values: Iterable[Mapping[str, Any]]) -> tuple[ModelCatalogEntry, ...]:
    """Parse one catalog document and fail on duplicate IDs rather than overwrite."""
    parsed = tuple(ModelCatalogEntry.from_dict(value) for value in values)
    identifiers = [entry.id for entry in parsed]
    if len(set(identifiers)) != len(identifiers):
        raise DomainValidationError("Model catalog contains duplicate model IDs")
    return parsed


__all__ = ["MemoryFitEstimate", "ModelCatalogEntry", "ModelInventoryEntry", "ModelRecommendation", "ModelTier", "ModelVerificationStatus", "ProvenanceStatus", "estimate_memory_fit", "infer_quantization", "parse_catalog_entries", "recommend_model_candidates", "recommend_models", "validate_model_checksum", "validate_quantization"]
