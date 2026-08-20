"""FastAPI transport schemas for the Dashboard API.

These Pydantic models validate wire payloads only.  Dashboard services convert
them explicitly to the existing domain/benchmark configuration types before
performing orchestration.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from cluster.benchmark.runner import normalize_model_ids


class NodePayload(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    role: str = "worker"
    host: str = Field(min_length=1, max_length=45)
    user: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_][a-zA-Z0-9_-]*$")
    ssh_port: int = Field(22, ge=1, le=65535)
    api_port: int = Field(8000, ge=1, le=65535)
    project_dir: str = Field(min_length=2, max_length=512)
    enabled: bool = True
    identity_file: str = Field("", max_length=512)
    platform: str = "auto"

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"head", "worker"}:
            raise ValueError("role must be head or worker")
        return value

    @field_validator("project_dir")
    @classmethod
    def validate_project_dir(cls, value: str) -> str:
        if (
            not value.startswith(("/home/", "/opt/", "/srv/"))
            or ".." in Path(value).parts
            or not re.fullmatch(r"/[a-zA-Z0-9._/-]+", value)
        ):
            raise ValueError("project_dir must be a safe absolute path")
        normalized = str(Path(value))
        parts = Path(normalized).parts
        if normalized in {"/home", "/opt", "/srv"} or (
            len(parts) >= 2 and parts[1] == "home" and len(parts) < 4
        ):
            raise ValueError("project_dir must name a dedicated project directory")
        return value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        try:
            address = ipaddress.ip_address(value.strip())
        except ValueError as exc:
            raise ValueError("host must be a private IPv4 address") from exc
        allowed = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("127.0.0.0/8"),
        )
        if address.version != 4 or not any(address in network for network in allowed):
            raise ValueError("host must belong to the head node's private LAN")
        return str(address)

    @field_validator("identity_file")
    @classmethod
    def validate_identity_file(cls, value: str) -> str:
        if value:
            raise ValueError("identity_file is managed by the head node")
        return ""

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"auto", "jetson", "raspberry-pi"}:
            raise ValueError("platform must be auto, jetson or raspberry-pi")
        return value


class ActionPayload(BaseModel):
    action: str
    node_names: List[str] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class ExperimentPayload(BaseModel):
    experiment_id: str = Field("", max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$|^$")
    name: str = "cluster-load-test"
    node_names: List[str] = Field(min_length=1, max_length=4)
    model_id: str = ""
    model_ids: List[str] = Field(default_factory=list, max_length=32)
    continue_on_model_error: bool = True
    model_cooldown_s: float = Field(2.0, ge=0.0, le=300.0)
    n_ctx: int = Field(1024, ge=128, le=4096)
    n_gpu_layers: int = Field(30, ge=0, le=120)
    requests: int = Field(20, ge=1, le=10_000)
    concurrency: int = Field(4, ge=1, le=256)
    max_tokens: int = Field(128, ge=1, le=1024)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    seed: int = Field(42, ge=-1, le=2_147_483_647)
    warmup_requests: int = Field(1, ge=0, le=10)
    prompt: str = Field(min_length=1, max_length=20_000)
    persist_prompt: bool = True
    require_uniform_config: bool = True
    execution_strategy: str = "replicated_round_robin"
    sweep_mode: str = "cumulative"
    rpc_split_mode: str = "layer"
    rpc_split_policy: str = "auto"
    rpc_tensor_split: List[float] = Field(default_factory=list, max_length=4)
    rpc_coordinator_node: Optional[str] = Field(None, max_length=80)
    acknowledge_experimental_rpc: bool = False

    @model_validator(mode="after")
    def normalize_models(self) -> "ExperimentPayload":
        models = normalize_model_ids(self.model_id, self.model_ids)
        self.model_ids = models
        self.model_id = models[0]
        return self


class ClusterSettingsPayload(BaseModel):
    worker_api_auth: Optional[bool] = None
    dashboard_token_auth: Optional[bool] = None
    dashboard_token: str = Field("", max_length=256)
