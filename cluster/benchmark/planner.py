"""Deterministic logical-to-physical benchmark planning."""

from __future__ import annotations

from typing import Any, List, Sequence

from cluster.domain.experiment import ExperimentConfig

from .models import RequestTask, StrategyScenario
from .strategies import get_strategy


def validate_strategy(nodes: Sequence[Any], config: ExperimentConfig) -> None:
    get_strategy(config.execution_strategy).validate(nodes, config)


def strategy_work_units(config: ExperimentConfig, node_count: int) -> int:
    return get_strategy(config.execution_strategy).work_units(config, node_count)


def build_strategy_scenarios(
    config: ExperimentConfig, nodes: Sequence[Any]
) -> List[StrategyScenario]:
    strategy = get_strategy(config.execution_strategy)
    strategy.validate(nodes, config)
    request_id = 0
    built: List[StrategyScenario] = []
    for definition in strategy.definitions(nodes, config):
        tasks: List[RequestTask] = []
        for logical_id in range(1, config.requests + 1):
            if definition.mapping == "broadcast":
                targets = list(enumerate(definition.nodes))
            elif definition.mapping.startswith("coordinator:"):
                coordinator_name = definition.mapping.split(":", 1)[1]
                targets = [(0, next(node for node in definition.nodes if node.name == coordinator_name))]
            else:
                target_index = (logical_id - 1) % len(definition.nodes)
                targets = [(target_index, definition.nodes[target_index])]
            for replica_index, target in targets:
                request_id += 1
                tasks.append(RequestTask(request_id, logical_id, definition.scenario_id, target.name, replica_index))
        built.append(StrategyScenario(
            definition.scenario_id,
            definition.label,
            [node.name for node in definition.nodes],
            tasks,
            definition.concurrency_scope,
            definition.execution_backend,
        ))
    return built


__all__ = ["build_strategy_scenarios", "strategy_work_units", "validate_strategy"]
