"""FastAPI transport schemas for the Dashboard API.

These Pydantic models validate wire payloads only.  Dashboard services convert
them explicitly to the existing domain/benchmark configuration types before
performing orchestration.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

    @model_validator(mode="after")
    def validate_home_owner(self) -> "NodePayload":
        parts = Path(self.project_dir).parts
        if len(parts) >= 3 and parts[1] == "home" and parts[2] != self.user:
            raise ValueError("project_dir below /home must belong to the SSH user")
        return self


class NodeRenamePayload(BaseModel):
    new_name: str = Field(min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


class NodeDeletePayload(BaseModel):
    """Optional remote cleanup requested while disconnecting a Worker."""

    remove_worker_files: bool = False
    confirmed: bool = False


class JetsonPowerModePayload(BaseModel):
    """A locally advertised nvpmodel ID; the Worker validates it again."""

    mode_id: int = Field(ge=0, le=99_999)


class TrashPurgePayload(BaseModel):
    """Explicit, checksum-bound approval for irreversible result deletion."""

    confirmed: bool = False
    archive_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class SshHostKeyPinPayload(BaseModel):
    fingerprint: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^SHA256:[A-Za-z0-9+/]+={0,2}$",
    )
    confirmed: bool = False


class ActionPayload(BaseModel):
    action: str
    node_names: List[str] = Field(default_factory=list)
    options: Dict[str, Any] = Field(default_factory=dict)


class ModelInstallPayload(BaseModel):
    nodes: List[str] = Field(min_length=1, max_length=32)
    source: Literal["direct"] = "direct"
    confirmed: bool = False

    @field_validator("nodes")
    @classmethod
    def validate_nodes(cls, values: List[str]) -> List[str]:
        if len(values) != len(set(values)) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value) for value in values):
            raise ValueError("nodes must contain unique valid Worker names")
        return values


class ModelLicenseAcceptancePayload(BaseModel):
    accepted: Literal[True]
    confirmed: Literal[True]
    license_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ExperimentPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    experiment_id: str = Field("", max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$|^$")
    name: str = "cluster-load-test"
    node_names: List[str] = Field(min_length=1)
    model_id: str = ""
    model_ids: List[str] = Field(default_factory=list, max_length=32)
    continue_on_model_error: bool = True
    model_cooldown_s: float = Field(2.0, ge=0.0, le=300.0)
    max_parallel_jobs: Literal[1, 2] = Field(1, exclude=True)
    n_ctx: int = Field(4096, ge=128, le=16384)
    n_gpu_layers: int = Field(30, ge=0, le=120)
    n_threads: Optional[int] = Field(None, ge=1, le=1024, strict=True)
    n_batch: Optional[int] = Field(None, ge=1, le=16384, strict=True)
    requests: int = Field(20, ge=1, le=10_000)
    concurrency: int = Field(4, ge=1, le=256)
    max_tokens: int = Field(128, ge=1, le=1024)
    temperature: float = Field(0.0, ge=0.0, le=2.0)
    top_p: float = Field(0.9, ge=0.0, le=1.0)
    seed: int = Field(42, ge=-1, le=2_147_483_647)
    warmup_requests: int = Field(1, ge=0, le=10)
    prompt: str = Field(min_length=1, max_length=20_000)
    persist_prompt: bool = True
    response_storage_mode: Literal["full", "hash_only", "none"] = "full"
    require_uniform_config: bool = True
    execution_strategy: str = "replicated_round_robin"
    sweep_mode: str = "cumulative"
    rpc_split_mode: str = "layer"
    rpc_split_policy: str = "auto"
    rpc_tensor_split: List[float] = Field(default_factory=list)
    rpc_gpu_layers: Union[Literal["all"], int] = "all"
    rpc_coordinator_node: Optional[str] = Field(None, max_length=80)
    acknowledge_experimental_rpc: bool = False

    @field_validator("rpc_gpu_layers", mode="before")
    @classmethod
    def validate_rpc_gpu_layers(cls, value: Any) -> Any:
        if value == "all":
            return value
        if type(value) is not int or not 0 <= value <= 999:
            raise ValueError("rpc_gpu_layers must be all or an integer between 0 and 999")
        return value

    @model_validator(mode="after")
    def normalize_models(self) -> "ExperimentPayload":
        models = normalize_model_ids(self.model_id, self.model_ids)
        self.model_ids = models
        self.model_id = models[0]
        return self


class ClusterSettingsPayload(BaseModel):
    worker_api_auth: Optional[bool] = None
    dashboard_token_auth: Optional[bool] = None
    ssh_host_key_policy: Optional[str] = Field(None, pattern=r"^(trusted_lan|pinned)$")
    dashboard_token: str = Field("", max_length=256)


class SweepPromptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    model_ref: Optional[str] = Field(None, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    text: str = Field(min_length=1, max_length=20_000)
    mode: Literal["same_text", "token_length_profile"] = "same_text"
    target_input_tokens: Optional[int] = Field(None, ge=1, le=16_384)

    @model_validator(mode="after")
    def validate_mode(self) -> "SweepPromptPayload":
        if (self.mode == "token_length_profile") != (self.target_input_tokens is not None):
            raise ValueError("token_length_profile requires target_input_tokens")
        return self


class SweepPreviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec: Dict[str, Any]
    model_selections: Dict[str, str] = Field(min_length=1, max_length=128)
    prompts: List[SweepPromptPayload] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_bounds(self) -> "SweepPreviewPayload":
        if len({(item.model_ref, item.ref) for item in self.prompts}) != len(self.prompts):
            raise ValueError("prompt model/ref pairs must be unique")
        if sum(len(item.text.encode("utf-8")) for item in self.prompts) > 256_000:
            raise ValueError("prompt payload exceeds byte budget")
        return self


class SweepSaveDraftPayload(SweepPreviewPayload):
    sweep_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class SweepLifecyclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    plan_revision: int = Field(ge=1, le=2_147_483_647)
    plan_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class SweepReasonPayload(SweepLifecyclePayload):
    reason: str = Field(min_length=1, max_length=512)


class SweepCloneConditionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_sweep_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
