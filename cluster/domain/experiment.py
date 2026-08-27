"""Side-effect-free benchmark experiment configuration and validation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Union

from .errors import DomainValidationError
from .identifiers import (
    validate_campaign_id,
    validate_experiment_id,
    validate_model_id,
    validate_node_id,
    validate_suite_id,
)
from .strategy import ExecutionStrategy, RpcSplitMode, RpcSplitPolicy, SweepMode


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


@dataclass
class ExperimentConfig:
    experiment_id: str = ""
    name: str = "cluster-load-test"
    node_names: List[str] = field(default_factory=list)
    model_id: str = "qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    n_ctx: int = 4096
    n_gpu_layers: int = 30
    requests: int = 20
    concurrency: int = 4
    max_tokens: int = 128
    temperature: float = 0.0
    top_p: float = 0.9
    seed: int = 42
    warmup_requests: int = 1
    prompt: str = "엣지 장치에서 의료 LLM을 실행할 때의 장점과 한계를 한 문단으로 설명해줘."
    persist_prompt: bool = True
    require_uniform_config: bool = True
    request_timeout_s: float = 600.0
    execution_strategy: Union[ExecutionStrategy, str] = ExecutionStrategy.REPLICATED_ROUND_ROBIN
    sweep_mode: Union[SweepMode, str] = SweepMode.CUMULATIVE
    rpc_split_mode: Union[RpcSplitMode, str] = RpcSplitMode.LAYER
    rpc_split_policy: Union[RpcSplitPolicy, str] = RpcSplitPolicy.AUTO
    rpc_tensor_split: List[float] = field(default_factory=list)
    acknowledge_experimental_rpc: bool = False
    suite_id: str = ""
    model_index: int = 1
    model_count: int = 1
    rpc_coordinator_node: Optional[str] = None
    experiment_type: str = ""
    campaign_id: str = ""
    campaign_cell_id: str = ""
    campaign_attempt_id: str = ""
    repeat_index: int = 0
    order_index: int = 0
    experiment_lock_id: str = ""
    experiment_lock_sha256: str = ""
    model_lock_entry: str = ""
    prompt_set_version: int = 0
    runtime_lock_version: int = 0
    condition_profile_id: str = ""
    measurement_quality_policy: str = ""
    pilot_id: str = ""
    pilot_cell_id: str = ""
    pilot_repeat_index: int = 0
    pilot_order_index: int = 0
    ignored_config_keys: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls, raw: Dict[str, Any], *, strict: bool = False
    ) -> "ExperimentConfig":
        known = {item.name for item in cls.__dataclass_fields__.values()}
        unknown = sorted(str(key) for key in raw if key not in known)
        if strict and unknown:
            raise DomainValidationError(
                "Unknown experiment configuration keys: " + ", ".join(unknown)
            )
        values = {key: value for key, value in raw.items() if key in known}
        recorded = values.get("ignored_config_keys", [])
        if recorded and not isinstance(recorded, list):
            raise DomainValidationError("ignored_config_keys must be a list")
        values["ignored_config_keys"] = sorted(
            set(str(item) for item in recorded) | set(unknown)
        )
        return cls(**values)

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DomainValidationError("Experiment name cannot be empty")
        if self.experiment_id:
            validate_experiment_id(self.experiment_id)
        if not isinstance(self.node_names, list) or not self.node_names:
            raise DomainValidationError("Select at least one node")
        for node_name in self.node_names:
            validate_node_id(node_name)
        if len(set(self.node_names)) != len(self.node_names):
            raise DomainValidationError("node_names must not contain duplicates")
        validate_model_id(self.model_id)
        if self.suite_id:
            validate_suite_id(self.suite_id)
        if (
            not _is_integer(self.model_count)
            or not _is_integer(self.model_index)
            or self.model_count < 1
            or not 1 <= self.model_index <= self.model_count
        ):
            raise DomainValidationError("model_index must be between 1 and model_count")
        if not _is_integer(self.n_ctx) or not 128 <= self.n_ctx <= 16384:
            raise DomainValidationError("n_ctx must be between 128 and 16384")
        if not _is_integer(self.n_gpu_layers) or not 0 <= self.n_gpu_layers <= 120:
            raise DomainValidationError("n_gpu_layers must be between 0 and 120")
        if not _is_integer(self.requests) or not 1 <= self.requests <= 10_000:
            raise DomainValidationError("requests must be between 1 and 10000")
        if not _is_integer(self.concurrency) or not 1 <= self.concurrency <= 256:
            raise DomainValidationError("concurrency must be between 1 and 256")
        if not _is_integer(self.max_tokens) or not 1 <= self.max_tokens <= 1024:
            raise DomainValidationError("max_tokens must be between 1 and 1024")
        if not _is_finite_number(self.temperature) or not 0.0 <= self.temperature <= 2.0:
            raise DomainValidationError("temperature must be between 0 and 2")
        if not _is_finite_number(self.top_p) or not 0.0 <= self.top_p <= 1.0:
            raise DomainValidationError("top_p must be between 0 and 1")
        if not _is_integer(self.seed) or not -1 <= self.seed <= 2_147_483_647:
            raise DomainValidationError("seed must be between -1 and 2147483647")
        if not _is_integer(self.warmup_requests) or not 0 <= self.warmup_requests <= 10:
            raise DomainValidationError("warmup_requests must be between 0 and 10")
        if not _is_finite_number(self.request_timeout_s) or self.request_timeout_s <= 0:
            raise DomainValidationError("request_timeout_s must be a positive finite number")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise DomainValidationError("prompt cannot be empty")
        if not isinstance(self.persist_prompt, bool):
            raise DomainValidationError("persist_prompt must be a boolean")
        if not isinstance(self.require_uniform_config, bool):
            raise DomainValidationError("require_uniform_config must be a boolean")
        if not isinstance(self.acknowledge_experimental_rpc, bool):
            raise DomainValidationError("acknowledge_experimental_rpc must be a boolean")
        if (
            not isinstance(self.ignored_config_keys, list)
            or any(not isinstance(item, str) or not item for item in self.ignored_config_keys)
        ):
            raise DomainValidationError("ignored_config_keys must contain non-empty strings")

        formal_research_values = (
            self.experiment_type,
            self.campaign_id,
            self.campaign_cell_id,
            self.campaign_attempt_id,
            self.experiment_lock_id,
            self.experiment_lock_sha256,
            self.model_lock_entry,
            self.condition_profile_id,
            self.measurement_quality_policy,
        )
        has_formal_identity = bool(self.campaign_id) or any(
            bool(value) for value in formal_research_values[2:]
        ) or any(
            value != 0
            for value in (self.repeat_index, self.order_index, self.prompt_set_version, self.runtime_lock_version)
        )
        has_pilot_identity = bool(self.pilot_id or self.pilot_cell_id) or any(
            value != 0 for value in (self.pilot_repeat_index, self.pilot_order_index)
        )
        if has_formal_identity and has_pilot_identity:
            raise DomainValidationError("formal campaign and pilot identity cannot be mixed")
        if has_formal_identity:
            if self.experiment_type != "formal":
                raise DomainValidationError("campaign research identity requires experiment_type=formal")
            validate_campaign_id(self.campaign_id)
            if (
                not isinstance(self.campaign_cell_id, str)
                or not self.campaign_cell_id
                or len(self.campaign_cell_id) > 512
                or "/" in self.campaign_cell_id
                or "\\" in self.campaign_cell_id
                or any(ord(char) < 32 or ord(char) == 127 for char in self.campaign_cell_id)
            ):
                raise DomainValidationError("campaign_cell_id is invalid")
            if (
                not isinstance(self.campaign_attempt_id, str)
                or not self.campaign_attempt_id.startswith("attempt_")
                or not self.campaign_attempt_id.replace("_", "").isalnum()
            ):
                raise DomainValidationError("campaign_attempt_id is invalid")
            if not _is_integer(self.repeat_index) or self.repeat_index < 1:
                raise DomainValidationError("repeat_index must be a positive integer")
            if not _is_integer(self.order_index) or self.order_index < 1:
                raise DomainValidationError("order_index must be a positive integer")
            if (
                not isinstance(self.experiment_lock_sha256, str)
                or len(self.experiment_lock_sha256) != 64
                or any(char not in "0123456789abcdef" for char in self.experiment_lock_sha256)
            ):
                raise DomainValidationError("experiment_lock_sha256 must be lowercase SHA-256")
            for label, value in (
                ("experiment_lock_id", self.experiment_lock_id),
                ("model_lock_entry", self.model_lock_entry),
                ("condition_profile_id", self.condition_profile_id),
                ("measurement_quality_policy", self.measurement_quality_policy),
            ):
                if not isinstance(value, str) or not value.strip():
                    raise DomainValidationError(f"{label} must be a non-empty string")
            if not _is_integer(self.prompt_set_version) or self.prompt_set_version < 1:
                raise DomainValidationError("prompt_set_version must be a positive integer")
            if not _is_integer(self.runtime_lock_version) or self.runtime_lock_version < 1:
                raise DomainValidationError("runtime_lock_version must be a positive integer")
        elif has_pilot_identity:
            if self.experiment_type != "pilot":
                raise DomainValidationError("pilot research identity requires experiment_type=pilot")
            for label, value in (
                ("pilot_id", self.pilot_id),
                ("pilot_cell_id", self.pilot_cell_id),
            ):
                if (
                    not isinstance(value, str)
                    or not value
                    or len(value) > 512
                    or "/" in value
                    or "\\" in value
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                ):
                    raise DomainValidationError(f"{label} is invalid")
            if not _is_integer(self.pilot_repeat_index) or self.pilot_repeat_index < 1:
                raise DomainValidationError("pilot_repeat_index must be a positive integer")
            if not _is_integer(self.pilot_order_index) or self.pilot_order_index < 1:
                raise DomainValidationError("pilot_order_index must be a positive integer")
        elif self.experiment_type not in {"", "smoke"}:
            raise DomainValidationError("experiment_type requires matching research identity")

        try:
            strategy = ExecutionStrategy(self.execution_strategy)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"Unsupported execution_strategy: {self.execution_strategy}") from exc
        try:
            sweep_mode = SweepMode(self.sweep_mode)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("sweep_mode must be cumulative or individual") from exc
        try:
            split_mode = RpcSplitMode(self.rpc_split_mode)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("rpc_split_mode must be layer or row") from exc
        try:
            split_policy = RpcSplitPolicy(self.rpc_split_policy)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("rpc_split_policy must be auto, equal or custom") from exc

        if not isinstance(self.rpc_tensor_split, list):
            raise DomainValidationError("rpc_tensor_split must be a list")
        invalid_split = any(
            not _is_finite_number(value) or float(value) <= 0
            for value in self.rpc_tensor_split
        )
        if invalid_split:
            raise DomainValidationError("rpc_tensor_split values must be positive finite numbers")
        if (
            strategy is ExecutionStrategy.MODEL_PARALLEL_RPC
            and split_policy is RpcSplitPolicy.CUSTOM
            and len(self.rpc_tensor_split) != len(self.node_names)
        ):
            raise DomainValidationError("rpc_tensor_split must contain one value for each selected node")

        coordinator = self.rpc_coordinator_node
        if coordinator == "":
            coordinator = None
        if coordinator is not None:
            validate_node_id(coordinator)
            if strategy is not ExecutionStrategy.MODEL_PARALLEL_RPC:
                raise DomainValidationError("rpc_coordinator_node is only valid for model_parallel_rpc")
            if coordinator not in self.node_names:
                raise DomainValidationError("rpc_coordinator_node must be one of node_names")

        self.execution_strategy = strategy
        self.sweep_mode = sweep_mode
        self.rpc_split_mode = split_mode
        self.rpc_split_policy = split_policy
        self.rpc_coordinator_node = coordinator


def normalized_config_identity(config: ExperimentConfig) -> Dict[str, Any]:
    """Return the deterministic, privacy-safe execution identity."""
    identity: Dict[str, Any] = {}
    for name in sorted(config.__dataclass_fields__):
        if name in {"prompt", "ignored_config_keys"}:
            continue
        value = getattr(config, name)
        if isinstance(value, Enum):
            value = value.value
        identity[name] = value
    identity["prompt_sha256"] = hashlib.sha256(
        config.prompt.encode("utf-8")
    ).hexdigest()
    return identity


def config_fingerprint(config: ExperimentConfig) -> str:
    """Hash canonical JSON so dict order and prompt persistence cannot alter identity."""
    canonical = json.dumps(
        normalized_config_identity(config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

def normalize_model_ids(model_id: str, model_ids: Sequence[str]) -> List[str]:
    """Normalize legacy single-model and suite payloads without ambiguity."""
    normalized = list(model_ids) if model_ids else ([model_id] if model_id else [])
    if not normalized:
        raise DomainValidationError("Select at least one model")
    for item in normalized:
        validate_model_id(item)
    if len(set(normalized)) != len(normalized):
        raise DomainValidationError("model_ids must not contain duplicates")
    return normalized


__all__ = [
    "ExperimentConfig",
    "config_fingerprint",
    "normalized_config_identity",
    "normalize_model_ids",
    "validate_model_id",
]
