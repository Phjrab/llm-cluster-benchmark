"""Strategy registry and side-effect-free scenario definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Protocol, Sequence, Tuple

from cluster.domain.experiment import ExperimentConfig
from cluster.domain.strategy import ExecutionStrategy

from .rpc_selection import select_rpc_coordinator


class Participant(Protocol):
    name: str


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    label: str
    nodes: Tuple[Participant, ...]
    mapping: str
    concurrency_scope: str = "physical"
    execution_backend: str = "worker"


@dataclass(frozen=True)
class StrategyDescription:
    id: str
    label: str
    model_placement: str
    request_mapping: str
    min_nodes: int
    max_nodes: int
    experimental: bool
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "model_placement": self.model_placement,
            "request_mapping": self.request_mapping,
            "min_nodes": self.min_nodes,
            "max_nodes": self.max_nodes,
            "experimental": self.experimental,
            "summary": self.summary,
        }


class BenchmarkStrategy:
    description: StrategyDescription
    execution_backend = "worker"
    result_model_placement = "replicated"
    cumulative_scaling = False

    def validate(self, nodes: Sequence[Participant], config: ExperimentConfig) -> None:
        count = len(nodes)
        if not self.description.min_nodes <= count <= self.description.max_nodes:
            if self.description.min_nodes == self.description.max_nodes == 1:
                raise ValueError("단일 노드 기준선은 정확히 1대의 worker가 필요합니다")
            raise ValueError("선택한 실험 방식은 2대 이상의 worker가 필요합니다")
        self._validate_workers_only(nodes)

    @staticmethod
    def _validate_workers_only(nodes: Sequence[Participant]) -> None:
        controllers = [
            str(getattr(node, "name", getattr(node, "host", "controller")))
            for node in nodes
            if not hasattr(node, "name") or getattr(node, "role", "worker") != "worker"
        ]
        if controllers:
            raise ValueError(
                "Controller/head는 벤치마크 참여자가 아닙니다. worker만 선택하세요: "
                + ", ".join(controllers)
            )

    def definitions(
        self, nodes: Sequence[Participant], config: ExperimentConfig
    ) -> List[ScenarioDefinition]:
        raise NotImplementedError

    def work_units(self, config: ExperimentConfig, node_count: int) -> int:
        return config.requests


class SingleNodeStrategy(BenchmarkStrategy):
    description = StrategyDescription(
        "single_node", "단일 노드 기준선", "full_model_single", "all_to_one", 1, 1, False,
        "선택한 worker 1대에 전체 모델을 올리고 장치 자체 성능을 측정합니다.",
    )

    def definitions(self, nodes, config):
        return [ScenarioDefinition("main", "단일 노드 기준선", tuple(nodes), "round_robin")]


class ReplicatedRoundRobinStrategy(BenchmarkStrategy):
    description = StrategyDescription(
        "replicated_round_robin", "복제 모델 · 요청 분산", "full_model_per_node", "round_robin", 1, 4, False,
        "각 worker가 전체 모델을 따로 로드하고 여러 사용자 요청을 순서대로 나눕니다.",
    )

    def definitions(self, nodes, config):
        return [ScenarioDefinition("main", "요청 분산", tuple(nodes), "round_robin")]


class BroadcastCompareStrategy(BenchmarkStrategy):
    description = StrategyDescription(
        "broadcast_compare", "동일 요청 전체 전송", "full_model_per_node", "broadcast", 2, 4, False,
        "같은 요청을 모든 worker 복제본에 보내 지연과 출력 일치도를 비교합니다.",
    )

    def definitions(self, nodes, config):
        return [ScenarioDefinition("broadcast", "동일 요청 전체 전송", tuple(nodes), "broadcast", "logical_group")]

    def work_units(self, config, node_count):
        return config.requests * node_count


class NodeSweepStrategy(BenchmarkStrategy):
    cumulative_scaling = True
    description = StrategyDescription(
        "node_sweep", "노드 수 확장 스윕", "full_model_per_node", "scenario_round_robin", 2, 4, False,
        "1대, 2대, … 조건을 반복해 worker 추가에 따른 속도 향상과 효율을 비교합니다.",
    )

    def definitions(self, nodes, config):
        if str(config.sweep_mode) == "cumulative":
            return [
                ScenarioDefinition(f"nodes-{size}", f"누적 {size}대", tuple(nodes[:size]), "round_robin")
                for size in range(1, len(nodes) + 1)
            ]
        return [
            ScenarioDefinition(f"node-{index}", f"개별 · {node.name}", (node,), "round_robin")
            for index, node in enumerate(nodes, start=1)
        ]

    def work_units(self, config, node_count):
        return config.requests * node_count


class ModelParallelRpcStrategy(BenchmarkStrategy):
    """Plan a sharded model around a selected worker coordinator."""

    description = StrategyDescription(
        "model_parallel_rpc", "모델 분할 추론 · RPC", "sharded_model", "one_coordinator", 2, 4, True,
        "한 모델의 가중치와 계산을 여러 노드 장치에 분할하고 각 토큰을 함께 계산합니다.",
    )
    execution_backend = "rpc"
    result_model_placement = "sharded"

    def validate(self, nodes, config):
        count = len(nodes)
        if not self.description.min_nodes <= count <= self.description.max_nodes:
            raise ValueError("모델 분할 RPC는 2대 이상의 worker가 필요합니다")
        self._validate_workers_only(nodes)
        select_rpc_coordinator(nodes, config.rpc_coordinator_node)
        if not config.acknowledge_experimental_rpc:
            raise ValueError("모델 분할 RPC의 실험적 특성과 LAN 보안 경고를 확인해야 합니다")
        if str(config.rpc_split_policy) == "custom" and len(config.rpc_tensor_split) != count:
            raise ValueError("사용자 지정 분할 비율 수는 선택한 노드 수와 같아야 합니다")

    def definitions(self, nodes, config):
        coordinator = select_rpc_coordinator(nodes, config.rpc_coordinator_node)
        return [ScenarioDefinition(
            "rpc-sharded", "RPC 모델 분할", tuple(nodes), f"coordinator:{coordinator.name}",
            execution_backend="rpc",
        )]


STRATEGY_REGISTRY: Dict[str, BenchmarkStrategy] = {
    strategy.description.id: strategy
    for strategy in (
        SingleNodeStrategy(),
        ReplicatedRoundRobinStrategy(),
        BroadcastCompareStrategy(),
        NodeSweepStrategy(),
        ModelParallelRpcStrategy(),
    )
}


def get_strategy(value: Any) -> BenchmarkStrategy:
    strategy_id = ExecutionStrategy(value).value
    return STRATEGY_REGISTRY[strategy_id]


def strategy_catalog() -> List[Dict[str, Any]]:
    return [STRATEGY_REGISTRY[item.value].description.to_dict() for item in ExecutionStrategy]


__all__ = ["BenchmarkStrategy", "STRATEGY_REGISTRY", "get_strategy", "strategy_catalog"]
