"""Deterministic, side-effect-free failure normalization and guidance."""

from __future__ import annotations

from typing import Mapping, Optional

from .errors import ClusterError, ErrorCode, FailureRecord


FAILURE_GUIDE: Mapping[ErrorCode, tuple[str, ...]] = {
    ErrorCode.WORKER_OFFLINE: ("워커 전원·네트워크·Worker API 상태를 확인하세요.",),
    ErrorCode.WORKER_AUTH_FAILED: ("Worker API 인증 토큰 설정이 Controller와 일치하는지 확인하세요.",),
    ErrorCode.WORKER_TIMEOUT: ("워커 부하와 네트워크를 확인하고 요청 제한 시간을 조정하세요.",),
    ErrorCode.MODEL_MISSING: ("선택한 Worker에 동일한 GGUF 모델을 설치·검증하세요.",),
    ErrorCode.MODEL_LOAD_FAILED: ("모델 파일과 Worker 로그를 확인한 뒤 다시 준비하세요.",),
    ErrorCode.MODEL_LOAD_OOM: ("더 작은 양자화 모델·컨텍스트·GPU 레이어 수를 사용하세요.",),
    ErrorCode.MODEL_CORRUPTED: ("모델 SHA-256을 검증하고 손상된 GGUF를 다시 배포하세요.",),
    ErrorCode.BACKEND_NOT_READY: ("Worker 환경 점검 후 llama backend를 설치·검증하세요.",),
    ErrorCode.BACKEND_MISMATCH: ("Worker 플랫폼에 맞는 CUDA 또는 OpenBLAS backend를 다시 빌드하세요.",),
    ErrorCode.CONFIG_MISMATCH: ("실험 파라미터와 선택 Worker 구성을 다시 확인하세요.",),
    ErrorCode.REQUEST_TIMEOUT: ("요청 제한 시간과 Worker의 모델 처리 상태를 확인하세요.",),
    ErrorCode.INFERENCE_FAILED: ("Worker inference 로그와 모델 런타임 상태를 확인하세요.",),
    ErrorCode.RPC_NOT_PREPARED: ("선택 Worker의 pinned llama.cpp RPC runtime을 준비하세요.",),
    ErrorCode.RPC_DEVICE_FAILED: ("RPC device Worker의 런타임·네트워크·포트를 확인하세요.",),
    ErrorCode.RPC_COORDINATOR_FAILED: ("선택한 RPC coordinator Worker와 llama-server 로그를 확인하세요.",),
    ErrorCode.RPC_CONNECTION_FAILED: ("Coordinator endpoint와 private LAN 연결을 확인하세요.",),
    ErrorCode.RPC_MODEL_LOAD_FAILED: ("Coordinator Worker의 GGUF 경로와 메모리를 확인하세요.",),
    ErrorCode.RPC_CLEANUP_FAILED: ("남아 있는 RPC 프로세스를 확인한 뒤 안전하게 정리하세요.",),
    ErrorCode.CANCELLED: ("취소 요청으로 실행이 종료되었습니다. 필요한 경우 결과 journal을 검토하세요.",),
    ErrorCode.UNKNOWN: ("원본 오류와 Worker 로그를 보존한 뒤 환경 점검을 실행하세요.",),
}


def failure_record(code: ErrorCode, message: str, *, stage: str, node: Optional[str] = None, model_id: Optional[str] = None, evidence: Optional[Mapping[str, object]] = None) -> FailureRecord:
    return FailureRecord(code=code, stage=stage, message=message or code.value, node=node, model_id=model_id, evidence=dict(evidence or {}), solutions=FAILURE_GUIDE[code])


def failure_from_exception(exc: Exception, *, stage: str, node: Optional[str] = None, model_id: Optional[str] = None, fallback: ErrorCode = ErrorCode.UNKNOWN) -> FailureRecord:
    if isinstance(exc, ClusterError):
        return failure_record(exc.code, str(exc), stage=exc.stage or stage, node=exc.node or node, model_id=exc.model_id or model_id, evidence=exc.evidence)
    if isinstance(exc, FileNotFoundError):
        code = ErrorCode.MODEL_MISSING
    elif isinstance(exc, TimeoutError):
        code = ErrorCode.REQUEST_TIMEOUT
    elif isinstance(exc, (ConnectionError, OSError)):
        code = ErrorCode.WORKER_OFFLINE
    elif isinstance(exc, MemoryError):
        code = ErrorCode.MODEL_LOAD_OOM
    else:
        return failure_from_message(str(exc), stage=stage, node=node, model_id=model_id, fallback=fallback)
    return failure_record(code, str(exc), stage=stage, node=node, model_id=model_id, evidence={"exception_type": type(exc).__name__})


def failure_from_message(message: str, *, stage: str, node: Optional[str] = None, model_id: Optional[str] = None, fallback: ErrorCode = ErrorCode.INFERENCE_FAILED) -> FailureRecord:
    normalized = message.lower()
    if "cancel" in normalized:
        code = ErrorCode.CANCELLED
    elif "timed out" in normalized or "timeout" in normalized:
        code = ErrorCode.REQUEST_TIMEOUT
    elif "unauthorized" in normalized or "forbidden" in normalized or "401" in normalized or "403" in normalized:
        code = ErrorCode.WORKER_AUTH_FAILED
    elif "not found" in normalized and ("model" in normalized or "gguf" in normalized):
        code = ErrorCode.MODEL_MISSING
    elif "out of memory" in normalized or "cuda oom" in normalized or "oom" in normalized:
        code = ErrorCode.MODEL_LOAD_OOM
    elif "corrupt" in normalized or "checksum" in normalized:
        code = ErrorCode.MODEL_CORRUPTED
    elif "backend" in normalized and ("ready" in normalized or "unavailable" in normalized):
        code = ErrorCode.BACKEND_NOT_READY
    elif "connection" in normalized or "refused" in normalized or "unreachable" in normalized:
        code = ErrorCode.WORKER_OFFLINE if fallback is not ErrorCode.RPC_CONNECTION_FAILED else fallback
    else:
        code = fallback
    return failure_record(code, message or code.value, stage=stage, node=node, model_id=model_id, evidence={"raw_error": message})


def http_status_for_failure(failure: FailureRecord) -> int:
    """Map stable codes to HTTP semantics without importing FastAPI."""
    if failure.code is ErrorCode.WORKER_AUTH_FAILED:
        return 401
    if failure.code in {ErrorCode.MODEL_MISSING, ErrorCode.MODEL_CORRUPTED}:
        return 404
    if failure.code in {ErrorCode.CONFIG_MISMATCH}:
        return 400
    if failure.code is ErrorCode.MODEL_LOAD_OOM:
        return 507
    if failure.code in {ErrorCode.REQUEST_TIMEOUT, ErrorCode.WORKER_TIMEOUT}:
        return 504
    if failure.code in {ErrorCode.BACKEND_NOT_READY, ErrorCode.BACKEND_MISMATCH, ErrorCode.WORKER_OFFLINE}:
        return 503
    return 500


__all__ = ["FAILURE_GUIDE", "failure_from_exception", "failure_from_message", "failure_record", "http_status_for_failure"]
