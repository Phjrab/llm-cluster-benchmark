"""Pure domain model for the Mac controller and inference workers."""

from .controller import ControllerConfig, ControllerPlatform
from .errors import ClusterError, DomainValidationError, ErrorCode, FailureRecord
from .events import ClusterEvent, EventChannel
from .failures import FAILURE_GUIDE, failure_from_exception, failure_from_message, http_status_for_failure
from .experiment import ExperimentConfig, normalize_model_ids, validate_model_id
from .identifiers import validate_experiment_id, validate_node_id, validate_run_id, validate_suite_id
from .layout import ProjectLayout
from .model import ModelCatalogEntry, ModelInventoryEntry, infer_quantization, recommend_models, validate_model_checksum
from .strategy import (
    EXECUTION_STRATEGIES,
    EXECUTION_STRATEGY_ORDER,
    ExecutionStrategy,
    RpcSplitMode,
    RpcSplitPolicy,
    SweepMode,
)
from .worker import WorkerInventory, WorkerNode, WorkerPlatform

__all__ = [
    "ClusterError",
    "ClusterEvent",
    "ControllerConfig",
    "ControllerPlatform",
    "DomainValidationError",
    "ErrorCode",
    "EventChannel",
    "FAILURE_GUIDE",
    "EXECUTION_STRATEGIES",
    "EXECUTION_STRATEGY_ORDER",
    "ExecutionStrategy",
    "ExperimentConfig",
    "FailureRecord",
    "ModelCatalogEntry",
    "ModelInventoryEntry",
    "ProjectLayout",
    "RpcSplitMode",
    "RpcSplitPolicy",
    "SweepMode",
    "WorkerInventory",
    "WorkerNode",
    "WorkerPlatform",
    "normalize_model_ids",
    "failure_from_exception",
    "failure_from_message",
    "http_status_for_failure",
    "infer_quantization",
    "recommend_models",
    "validate_experiment_id",
    "validate_model_id",
    "validate_model_checksum",
    "validate_node_id",
    "validate_run_id",
    "validate_suite_id",
]
