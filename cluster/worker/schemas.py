"""Worker HTTP boundary schemas.

These Pydantic models are intentionally separate from the inference backend so
the backend remains usable in route tests without FastAPI application globals.
"""

from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel, Field


DEFAULT_N_CTX = 4096
DEFAULT_N_GPU_LAYERS = 8
DEFAULT_MAX_TOKENS = 256


class SelectModelRequest(BaseModel):
    model_id: str = Field(..., description="Relative model path from the worker models directory")
    n_ctx: int = Field(DEFAULT_N_CTX, ge=128, le=16384)
    n_gpu_layers: int = Field(DEFAULT_N_GPU_LAYERS, ge=0, le=120)


class VerifyModelRequest(BaseModel):
    model_id: str = Field(..., description="Relative GGUF path from the worker models directory")
    expected_sha256: str = Field("", max_length=64)


class DeleteModelRequest(BaseModel):
    model_id: str = Field(..., description="Relative GGUF path from the worker models directory")


class InstallModelRequest(BaseModel):
    model_id: str = Field(..., description="Relative GGUF path from the worker models directory")
    source_url: str = Field(..., min_length=8, max_length=2048)
    expected_sha256: str = Field(..., min_length=64, max_length=64)
    metadata: Dict[str, object] = Field(default_factory=dict, description="Pinned source and accepted-license metadata")


class ChatStreamRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: List[Dict[str, str]] = Field(default_factory=list)
    max_tokens: int = Field(DEFAULT_MAX_TOKENS, ge=1, le=1024)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)


class ClusterChatRequest(ChatStreamRequest):
    seed: int = Field(42, ge=-1, le=2_147_483_647)
