"""Typed strategy identifiers with unchanged external string values."""

from __future__ import annotations

from enum import Enum
from typing import Tuple


class _WireStringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ExecutionStrategy(_WireStringEnum):
    SINGLE_NODE = "single_node"
    REPLICATED_ROUND_ROBIN = "replicated_round_robin"
    BROADCAST_COMPARE = "broadcast_compare"
    NODE_SWEEP = "node_sweep"
    MODEL_PARALLEL_RPC = "model_parallel_rpc"


class SweepMode(_WireStringEnum):
    CUMULATIVE = "cumulative"
    INDIVIDUAL = "individual"


class RpcSplitMode(_WireStringEnum):
    LAYER = "layer"
    ROW = "row"


class RpcSplitPolicy(_WireStringEnum):
    AUTO = "auto"
    EQUAL = "equal"
    CUSTOM = "custom"


EXECUTION_STRATEGY_ORDER: Tuple[ExecutionStrategy, ...] = (
    ExecutionStrategy.SINGLE_NODE,
    ExecutionStrategy.REPLICATED_ROUND_ROBIN,
    ExecutionStrategy.BROADCAST_COMPARE,
    ExecutionStrategy.NODE_SWEEP,
    ExecutionStrategy.MODEL_PARALLEL_RPC,
)

# Compatibility surface imported by the legacy runner and dashboard.
EXECUTION_STRATEGIES = {strategy.value for strategy in EXECUTION_STRATEGY_ORDER}
