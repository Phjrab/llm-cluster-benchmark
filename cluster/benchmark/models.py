"""Immutable planning records shared by benchmark strategies and executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class RequestTask:
    request_id: int
    logical_request_id: int
    scenario_id: str
    target_node: str
    replica_index: int = 0


@dataclass(frozen=True)
class StrategyScenario:
    scenario_id: str
    label: str
    node_names: List[str]
    tasks: List[RequestTask]
    concurrency_scope: str = "physical"
    execution_backend: str = "worker"


__all__ = ["RequestTask", "StrategyScenario"]
