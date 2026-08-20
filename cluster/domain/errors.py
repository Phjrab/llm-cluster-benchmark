"""Structured, side-effect-free error primitives for cluster domains."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


class ErrorCode(str, Enum):
    """Stable wire identifiers for deterministic failure classification."""

    WORKER_OFFLINE = "WORKER_OFFLINE"
    WORKER_AUTH_FAILED = "WORKER_AUTH_FAILED"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"
    MODEL_MISSING = "MODEL_MISSING"
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    MODEL_LOAD_OOM = "MODEL_LOAD_OOM"
    MODEL_CORRUPTED = "MODEL_CORRUPTED"
    BACKEND_NOT_READY = "BACKEND_NOT_READY"
    BACKEND_MISMATCH = "BACKEND_MISMATCH"
    CONFIG_MISMATCH = "CONFIG_MISMATCH"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
    INFERENCE_FAILED = "INFERENCE_FAILED"
    RPC_NOT_PREPARED = "RPC_NOT_PREPARED"
    RPC_DEVICE_FAILED = "RPC_DEVICE_FAILED"
    RPC_COORDINATOR_FAILED = "RPC_COORDINATOR_FAILED"
    RPC_CONNECTION_FAILED = "RPC_CONNECTION_FAILED"
    RPC_MODEL_LOAD_FAILED = "RPC_MODEL_LOAD_FAILED"
    RPC_CLEANUP_FAILED = "RPC_CLEANUP_FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class FailureRecord:
    """Serializable failure evidence without infrastructure dependencies."""

    code: ErrorCode
    stage: str
    message: str
    node: Optional[str] = None
    model_id: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    solutions: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.code, ErrorCode):
            try:
                object.__setattr__(self, "code", ErrorCode(str(self.code)))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Unsupported error code: {self.code}") from exc
        if not isinstance(self.stage, str) or not self.stage.strip():
            raise ValueError("Failure stage cannot be empty")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("Failure message cannot be empty")
        object.__setattr__(self, "evidence", dict(self.evidence))
        object.__setattr__(self, "solutions", tuple(self.solutions))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "stage": self.stage,
            "node": self.node,
            "model_id": self.model_id,
            "message": self.message,
            "evidence": dict(self.evidence),
            "solutions": list(self.solutions),
        }


class ClusterError(Exception):
    """Base exception carrying a stable error code and structured context."""

    default_code = ErrorCode.UNKNOWN
    default_stage = "unknown"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[ErrorCode] = None,
        stage: Optional[str] = None,
        node: Optional[str] = None,
        model_id: Optional[str] = None,
        evidence: Optional[Mapping[str, Any]] = None,
        solutions: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.stage = stage or self.default_stage
        self.node = node
        self.model_id = model_id
        self.evidence = dict(evidence or {})
        self.solutions = tuple(solutions)

    def to_failure_record(self) -> FailureRecord:
        return FailureRecord(
            code=self.code,
            stage=self.stage,
            node=self.node,
            model_id=self.model_id,
            message=str(self),
            evidence=self.evidence,
            solutions=self.solutions,
        )


class DomainValidationError(ClusterError, ValueError):
    """ValueError-compatible domain validation failure."""

    default_code = ErrorCode.CONFIG_MISMATCH
    default_stage = "validation"
