"""Side-effect-free benchmark experiment configuration and validation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Union

from .errors import DomainValidationError
from .identifiers import (
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
    model_id: str = "qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"
    n_ctx: int = 1024
    n_gpu_layers: int = 30
    requests: int = 20
    concurrency: int = 4
    max_tokens: int = 128
    temperature: float = 0.0
    top_p: float = 0.9
    seed: int = 42
    warmup_requests: int = 1
    prompt: str = "엣지 장치에서 의료 LLM을 실행할 때의 장점과 한계를 한 문단으로 설명해줘."
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

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ExperimentConfig":
        known = {item.name for item in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in raw.items() if key in known})

    def validate(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DomainValidationError("Experiment name cannot be empty")
        if self.experiment_id:
            validate_experiment_id(self.experiment_id)
        if not isinstance(self.node_names, list) or not self.node_names:
            raise DomainValidationError("Select at least one node")
        if len(self.node_names) > 4:
            raise DomainValidationError("Select at most four nodes")
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
        if not _is_integer(self.n_ctx) or not 128 <= self.n_ctx <= 4096:
            raise DomainValidationError("n_ctx must be between 128 and 4096")
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
        if not isinstance(self.require_uniform_config, bool):
            raise DomainValidationError("require_uniform_config must be a boolean")
        if not isinstance(self.acknowledge_experimental_rpc, bool):
            raise DomainValidationError("acknowledge_experimental_rpc must be a boolean")

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


__all__ = ["ExperimentConfig", "normalize_model_ids", "validate_model_id"]
