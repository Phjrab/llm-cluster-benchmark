from __future__ import annotations

import ast
import inspect
import json
import math
import unittest
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path

import cluster.domain.controller as controller_module
import cluster.domain.errors as errors_module
import cluster.domain.experiment as experiment_module
import cluster.domain.identifiers as identifiers_module
import cluster.domain.layout as layout_module
import cluster.domain.strategy as strategy_module
import cluster.domain.worker as worker_module
import cluster.integrations.legacy_inventory as legacy_inventory_module
from cluster.benchmark.runner import ExperimentConfig as LegacyRunnerExperimentConfig
from cluster.clusterctl import Node as LegacyNode
from cluster.domain.controller import ControllerConfig, ControllerPlatform
from cluster.domain.errors import DomainValidationError, ErrorCode, FailureRecord
from cluster.domain.experiment import (
    ExperimentConfig,
    normalize_model_ids,
    validate_model_id,
)
from cluster.domain.identifiers import (
    validate_experiment_id,
    validate_node_id,
    validate_run_id,
    validate_suite_id,
)
from cluster.domain.layout import ProjectLayout
from cluster.domain.worker import WorkerInventory, WorkerNode, WorkerPlatform
from cluster.domain.strategy import (
    EXECUTION_STRATEGIES,
    ExecutionStrategy,
    RpcSplitMode,
    RpcSplitPolicy,
    SweepMode,
)
from cluster.integrations.legacy_inventory import adapt_legacy_inventory


DEFAULT_MODEL_ID = "qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"


def make_worker(**overrides: object) -> WorkerNode:
    values: dict[str, object] = {
        "name": "jetson-01",
        "host": "192.168.0.26",
        "user": "jetson_orin_nano",
        "ssh_port": 22,
        "api_port": 8000,
        "project_dir": "/home/jetson_orin_nano/llm-cluster-benchmark",
        "enabled": True,
        "platform": WorkerPlatform.JETSON,
        "identity_file": None,
    }
    values.update(overrides)
    return WorkerNode(**values)  # type: ignore[arg-type]


def legacy_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "jetson-worker-01",
        "role": "worker",
        "host": "192.168.0.27",
        "user": "jetson",
        "ssh_port": "22",
        "api_port": "8000",
        "project_dir": "/home/jetson/llm-cluster-benchmark",
        "enabled": "true",
        "identity_file": "",
        "platform": "jetson",
    }
    values.update(overrides)
    return values


class ControllerWorkerRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = ControllerConfig(
            host="mac-controller.local",
            runtime_dir=Path("/tmp/llm-cluster/.run/cluster"),
            results_dir=Path("/tmp/llm-cluster/.run/cluster/results"),
            platform=ControllerPlatform.MACOS,
        )
        self.worker = make_worker()

    def test_controller_and_worker_are_distinct_domain_types(self) -> None:
        self.assertFalse(isinstance(self.controller, WorkerNode))
        self.assertFalse(hasattr(self.controller, "role"))
        self.assertFalse(hasattr(self.worker, "role"))
        self.assertEqual(self.controller.platform.value, "macos")
        self.assertEqual(self.worker.platform.value, "jetson")

    def test_controller_rejects_invalid_platform_and_unsafe_paths(self) -> None:
        invalid_cases = (
            {"host": " "},
            {"runtime_dir": Path("relative/runtime")},
            {"runtime_dir": Path("/")},
            {"runtime_dir": Path("/tmp")},
            {"runtime_dir": Path("/Users/tester")},
            {"runtime_dir": Path("/tmp/bad\x00runtime")},
            {"results_dir": Path("relative/results")},
            {"results_dir": Path("/")},
            {"results_dir": Path("/home/tester")},
            {"platform": "linux"},
        )
        values = {
            "host": "mac-controller.local",
            "runtime_dir": Path("/tmp/llm-cluster/.run/controller"),
            "results_dir": Path("/tmp/llm-cluster/.run/controller/results"),
            "platform": ControllerPlatform.MACOS,
        }
        for changes in invalid_cases:
            with self.subTest(changes=changes), self.assertRaises(DomainValidationError):
                ControllerConfig(**{**values, **changes})

    def test_worker_inventory_accepts_only_workers(self) -> None:
        inventory = WorkerInventory(workers=(self.worker,))
        self.assertEqual(inventory.workers, (self.worker,))
        with self.assertRaises(DomainValidationError):
            WorkerInventory(workers=(self.controller,))  # type: ignore[arg-type]

    def test_worker_inventory_preserves_order_and_rejects_duplicate_names(self) -> None:
        second = make_worker(
            name="pi-01",
            host="192.168.0.30",
            user="pi",
            project_dir="/home/pi/llm-cluster-benchmark",
            platform=WorkerPlatform.RASPBERRY_PI,
            enabled=False,
        )
        inventory = WorkerInventory(workers=(self.worker, second))
        self.assertEqual([worker.name for worker in inventory.workers], ["jetson-01", "pi-01"])
        self.assertFalse(inventory.workers[1].enabled)
        with self.assertRaises(DomainValidationError):
            WorkerInventory(workers=(self.worker, replace(self.worker, host="192.168.0.28")))

    def test_worker_inventory_rejects_duplicate_endpoints_and_more_than_four_enabled_workers(self) -> None:
        duplicate_endpoint = make_worker(name="jetson-02")
        with self.assertRaises(DomainValidationError):
            WorkerInventory(workers=(self.worker, duplicate_endpoint))

        workers = tuple(
            make_worker(
                name=f"worker-{index}",
                host=f"192.168.0.{30 + index}",
                user="edge",
                project_dir="/home/edge/llm-cluster-benchmark",
            )
            for index in range(1, 6)
        )
        with self.assertRaises(DomainValidationError):
            WorkerInventory(workers=workers)

    def test_worker_properties_keep_existing_wire_meaning(self) -> None:
        self.assertEqual(self.worker.api_url, "http://192.168.0.26:8000")
        self.assertEqual(self.worker.ssh_target, "jetson_orin_nano@192.168.0.26")
        self.assertEqual(WorkerPlatform.AUTO.value, "auto")
        self.assertEqual(WorkerPlatform.RASPBERRY_PI.value, "raspberry-pi")

    def test_worker_validation_rejects_unsafe_values(self) -> None:
        invalid_cases = (
            {"name": "../jetson"},
            {"host": "8.8.8.8"},
            {"host": "not a host"},
            {"host": "attacker.example.com"},
            {"host": "100.64.0.10"},
            {"host": "169.254.10.20"},
            {"user": "bad user"},
            {"ssh_port": 0},
            {"ssh_port": 65_536},
            {"api_port": 0},
            {"api_port": 65_536},
            {"project_dir": "relative/project"},
            {"project_dir": "/home/jetson"},
            {"project_dir": "/home/jetson/../escape"},
            {"identity_file": "relative/id_ed25519"},
            {"enabled": "true"},
            {"platform": "unsupported"},
        )
        for changes in invalid_cases:
            with self.subTest(changes=changes), self.assertRaises(DomainValidationError):
                make_worker(**changes)


class LegacyInventoryCompatibilityTests(unittest.TestCase):
    def test_legacy_head_is_reported_but_never_becomes_a_worker(self) -> None:
        conversion = adapt_legacy_inventory(
            [
                legacy_row(
                    name="jetson-head",
                    role="head",
                    host="127.0.0.1",
                    user="jetson_orin_nano",
                    project_dir="/home/jetson_orin_nano/project/llm/local_llm_bench",
                ),
                legacy_row(name="jetson-worker-01"),
                legacy_row(
                    name="pi-worker-01",
                    host="192.168.0.31",
                    user="pi",
                    project_dir="/home/pi/llm-cluster-benchmark",
                    enabled="false",
                    platform="raspberry-pi",
                ),
            ]
        )

        self.assertIsInstance(conversion.workers, WorkerInventory)
        self.assertEqual(
            [worker.name for worker in conversion.workers.workers],
            ["jetson-worker-01", "pi-worker-01"],
        )
        self.assertEqual(tuple(conversion.excluded_heads), ("jetson-head",))
        self.assertNotIn("jetson-head", {worker.name for worker in conversion.workers.workers})
        self.assertFalse(conversion.workers.workers[1].enabled)

    def test_legacy_defaults_are_normalized_without_changing_column_meaning(self) -> None:
        conversion = adapt_legacy_inventory(
            [legacy_row(platform="", identity_file="", enabled="yes")]
        )
        worker = conversion.workers.workers[0]
        self.assertIs(worker.platform, WorkerPlatform.AUTO)
        self.assertIsNone(worker.identity_file)
        self.assertTrue(worker.enabled)
        self.assertEqual(worker.ssh_port, 22)
        self.assertEqual(worker.api_port, 8000)

    def test_head_only_legacy_inventory_does_not_create_a_controller_or_participant(self) -> None:
        conversion = adapt_legacy_inventory(
            [
                legacy_row(
                    name="old-head",
                    role="head",
                    host="127.0.0.1",
                    project_dir="/home/jetson/project/llm/local_llm_bench",
                )
            ]
        )
        self.assertEqual(conversion.workers.workers, ())
        self.assertEqual(tuple(conversion.excluded_heads), ("old-head",))

    def test_invalid_legacy_role_is_not_silently_reinterpreted(self) -> None:
        with self.assertRaises(DomainValidationError):
            adapt_legacy_inventory([legacy_row(role="controller")])

    def test_actual_legacy_node_objects_use_the_same_pure_adapter(self) -> None:
        conversion = adapt_legacy_inventory(
            [
                LegacyNode(
                    name="legacy-head",
                    role="head",
                    host="127.0.0.1",
                    user="jetson",
                    ssh_port=22,
                    api_port=8000,
                    project_dir="/home/jetson/project/llm/local_llm_bench",
                    enabled=True,
                    platform="jetson",
                ),
                LegacyNode(
                    name="legacy-worker",
                    role="worker",
                    host="192.168.0.27",
                    user="jetson",
                    ssh_port=22,
                    api_port=8000,
                    project_dir="/home/jetson/llm-cluster-benchmark",
                    enabled=True,
                    platform="jetson",
                ),
            ]
        )
        self.assertEqual(conversion.excluded_heads, ("legacy-head",))
        self.assertEqual(tuple(worker.name for worker in conversion.workers), ("legacy-worker",))

    def test_legacy_hostname_and_identity_are_preserved_without_automatic_activation(self) -> None:
        conversion = adapt_legacy_inventory(
            [
                legacy_row(
                    name="hostname-worker",
                    host="worker.local",
                    identity_file="~/.ssh/id_ed25519",
                ),
                legacy_row(
                    name="rekey-worker",
                    host="192.168.0.28",
                    identity_file="$HOME/.ssh/id_ed25519",
                ),
            ]
        )
        self.assertEqual(tuple(worker.name for worker in conversion.workers), ("rekey-worker",))
        self.assertIsNone(conversion.workers.workers[0].identity_file)
        self.assertEqual(
            tuple(record.name for record in conversion.legacy_worker_records),
            ("hostname-worker", "rekey-worker"),
        )
        self.assertEqual(
            tuple(record.name for record in conversion.unresolved_workers),
            ("hostname-worker",),
        )
        self.assertEqual(
            conversion.legacy_worker_records[0].identity_file,
            "~/.ssh/id_ed25519",
        )
        self.assertTrue(any("re-keying" in warning for warning in conversion.warnings))


class StrategyIdentifierTests(unittest.TestCase):
    def test_strategy_values_preserve_external_json_identifiers(self) -> None:
        expected = {
            "single_node",
            "replicated_round_robin",
            "broadcast_compare",
            "node_sweep",
            "model_parallel_rpc",
        }
        self.assertEqual({strategy.value for strategy in ExecutionStrategy}, expected)
        self.assertEqual(set(EXECUTION_STRATEGIES), expected)
        self.assertEqual(
            json.loads(json.dumps({"strategy": ExecutionStrategy.MODEL_PARALLEL_RPC})),
            {"strategy": "model_parallel_rpc"},
        )
        self.assertEqual(str(ExecutionStrategy.SINGLE_NODE), "single_node")

    def test_auxiliary_strategy_identifiers_preserve_legacy_strings(self) -> None:
        self.assertEqual({item.value for item in SweepMode}, {"cumulative", "individual"})
        self.assertEqual({item.value for item in RpcSplitMode}, {"layer", "row"})
        self.assertEqual({item.value for item in RpcSplitPolicy}, {"auto", "equal", "custom"})


class ExperimentConfigTests(unittest.TestCase):
    def make_config(self, **changes: object) -> ExperimentConfig:
        config = ExperimentConfig(node_names=["jetson-01"])
        return replace(config, **changes)

    def assert_invalid(self, **changes: object) -> None:
        with self.assertRaises(DomainValidationError):
            config = self.make_config(**changes)
            config.validate()

    def test_old_config_defaults_and_unknown_key_compatibility(self) -> None:
        config = ExperimentConfig.from_dict(
            {"node_names": ["jetson-01"], "legacy_future_field": "ignored"}
        )
        config.validate()
        self.assertTrue(is_dataclass(config))
        self.assertEqual(config.name, "cluster-load-test")
        self.assertEqual(config.model_id, DEFAULT_MODEL_ID)
        self.assertEqual(config.n_ctx, 1024)
        self.assertEqual(config.n_gpu_layers, 30)
        self.assertEqual(config.requests, 20)
        self.assertEqual(config.concurrency, 4)
        self.assertEqual(config.max_tokens, 128)
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(config.top_p, 0.9)
        self.assertEqual(config.seed, 42)
        self.assertEqual(config.warmup_requests, 1)
        self.assertTrue(config.require_uniform_config)
        self.assertEqual(config.request_timeout_s, 600.0)
        self.assertIs(config.execution_strategy, ExecutionStrategy.REPLICATED_ROUND_ROBIN)
        self.assertIs(config.sweep_mode, SweepMode.CUMULATIVE)
        self.assertIs(config.rpc_split_mode, RpcSplitMode.LAYER)
        self.assertIs(config.rpc_split_policy, RpcSplitPolicy.AUTO)
        self.assertEqual(config.rpc_tensor_split, [])
        self.assertFalse(config.acknowledge_experimental_rpc)
        self.assertEqual(config.suite_id, "")
        self.assertEqual(config.model_index, 1)
        self.assertEqual(config.model_count, 1)
        self.assertEqual(config.rpc_coordinator_node, None)

    def test_config_serializes_strategy_as_the_existing_json_string(self) -> None:
        config = ExperimentConfig.from_dict(
            {
                "node_names": ["jetson-01"],
                "execution_strategy": "single_node",
            }
        )
        payload = json.loads(json.dumps(asdict(config), ensure_ascii=False))
        self.assertEqual(payload["execution_strategy"], "single_node")
        self.assertEqual(payload["node_names"], ["jetson-01"])

    def test_numeric_limits_accept_boundaries(self) -> None:
        minimum = self.make_config(
            n_ctx=128,
            n_gpu_layers=0,
            requests=1,
            concurrency=1,
            max_tokens=1,
            temperature=0.0,
            top_p=0.0,
            seed=-1,
            warmup_requests=0,
            request_timeout_s=0.001,
        )
        maximum = self.make_config(
            n_ctx=4096,
            n_gpu_layers=120,
            requests=10_000,
            concurrency=256,
            max_tokens=1024,
            temperature=2.0,
            top_p=1.0,
            seed=2_147_483_647,
            warmup_requests=10,
        )
        minimum.validate()
        maximum.validate()

    def test_numeric_limits_reject_out_of_range_or_non_finite_values(self) -> None:
        invalid_cases = (
            {"n_ctx": 127},
            {"n_ctx": 4097},
            {"n_gpu_layers": -1},
            {"n_gpu_layers": 121},
            {"requests": 0},
            {"requests": 10_001},
            {"concurrency": 0},
            {"concurrency": 257},
            {"max_tokens": 0},
            {"max_tokens": 1025},
            {"temperature": -0.01},
            {"temperature": 2.01},
            {"temperature": math.nan},
            {"top_p": -0.01},
            {"top_p": 1.01},
            {"top_p": math.inf},
            {"seed": -2},
            {"seed": 2_147_483_648},
            {"warmup_requests": -1},
            {"warmup_requests": 11},
            {"request_timeout_s": 0},
            {"request_timeout_s": math.inf},
            {"request_timeout_s": math.nan},
        )
        for changes in invalid_cases:
            with self.subTest(changes=changes):
                self.assert_invalid(**changes)

    def test_declared_scalar_and_container_types_are_enforced(self) -> None:
        invalid_cases = (
            {"node_names": "jetson-01"},
            {"n_ctx": 128.5},
            {"n_ctx": "128"},
            {"n_gpu_layers": True},
            {"requests": True},
            {"requests": 1.5},
            {"concurrency": 1.5},
            {"max_tokens": 1.5},
            {"temperature": True},
            {"top_p": False},
            {"seed": 42.0},
            {"warmup_requests": False},
            {"request_timeout_s": "600"},
            {"model_count": 1.5},
            {"model_index": True},
            {"require_uniform_config": "true"},
            {"acknowledge_experimental_rpc": "false"},
            {"rpc_tensor_split": "12"},
            {"rpc_tensor_split": [True]},
        )
        for changes in invalid_cases:
            with self.subTest(changes=changes):
                self.assert_invalid(**changes)

    def test_required_text_node_and_suite_metadata_validation(self) -> None:
        self.assert_invalid(name=" ")
        self.assert_invalid(prompt=" ")
        self.assert_invalid(node_names=[])
        self.assert_invalid(node_names=["jetson-01", "jetson-01"])
        self.assert_invalid(node_names=[f"worker-{index}" for index in range(1, 6)])
        self.assert_invalid(experiment_id="../../experiment")
        self.assert_invalid(suite_id="../../suite")
        self.assert_invalid(model_count=0)
        self.assert_invalid(model_count=2, model_index=0)
        self.assert_invalid(model_count=2, model_index=3)
        self.make_config(
            experiment_id="pi5-scaling",
            suite_id="suite_20260820_ab12",
            model_count=3,
            model_index=2,
        ).validate()

    def test_model_identifier_and_suite_normalization_preserve_legacy_behavior(self) -> None:
        self.assertEqual(validate_model_id("models/example.gguf"), "models/example.gguf")
        self.assertEqual(normalize_model_ids("legacy/model.gguf", []), ["legacy/model.gguf"])
        self.assertEqual(
            normalize_model_ids("stale.gguf", ["a.gguf", "b.gguf"]),
            ["a.gguf", "b.gguf"],
        )
        for unsafe in (
            "",
            "model.bin",
            "/models/a.gguf",
            "../a.gguf",
            "a/../b.gguf",
            "./a.gguf",
            "a//b.gguf",
            "a\\b.gguf",
            "bad\x00model.gguf",
            "bad\nmodel.gguf",
        ):
            with self.subTest(model_id=unsafe), self.assertRaises(DomainValidationError):
                validate_model_id(unsafe)
        with self.assertRaises(DomainValidationError):
            normalize_model_ids("", ["a.gguf", "a.gguf"])

    def test_rpc_coordinator_validation_is_pure_and_selection_scoped(self) -> None:
        valid = self.make_config(
            node_names=["jetson-01", "jetson-02"],
            execution_strategy=ExecutionStrategy.MODEL_PARALLEL_RPC,
            rpc_coordinator_node="jetson-02",
            acknowledge_experimental_rpc=True,
        )
        valid.validate()

        legacy_empty = ExperimentConfig.from_dict(
            {
                "node_names": ["jetson-01", "jetson-02"],
                "execution_strategy": "model_parallel_rpc",
                "rpc_coordinator_node": "",
                "acknowledge_experimental_rpc": True,
            }
        )
        legacy_empty.validate()
        self.assertIsNone(legacy_empty.rpc_coordinator_node)

        self.assert_invalid(
            node_names=["jetson-01", "jetson-02"],
            execution_strategy=ExecutionStrategy.MODEL_PARALLEL_RPC,
            rpc_coordinator_node="jetson-03",
            acknowledge_experimental_rpc=True,
        )
        self.assert_invalid(
            execution_strategy=ExecutionStrategy.REPLICATED_ROUND_ROBIN,
            rpc_coordinator_node="jetson-01",
        )

    def test_custom_rpc_tensor_split_matches_selected_worker_count(self) -> None:
        valid = self.make_config(
            node_names=["jetson-01", "jetson-02"],
            execution_strategy=ExecutionStrategy.MODEL_PARALLEL_RPC,
            rpc_split_policy=RpcSplitPolicy.CUSTOM,
            rpc_tensor_split=[0.6, 0.4],
            acknowledge_experimental_rpc=True,
        )
        valid.validate()
        for split in ([1.0], [1.0, 0.0], [1.0, math.inf]):
            with self.subTest(split=split):
                self.assert_invalid(
                    node_names=["jetson-01", "jetson-02"],
                    execution_strategy=ExecutionStrategy.MODEL_PARALLEL_RPC,
                    rpc_split_policy=RpcSplitPolicy.CUSTOM,
                    rpc_tensor_split=split,
                    acknowledge_experimental_rpc=True,
                )

    def test_stale_custom_rpc_policy_does_not_block_non_rpc_strategies(self) -> None:
        config = self.make_config(
            execution_strategy=ExecutionStrategy.SINGLE_NODE,
            rpc_split_policy=RpcSplitPolicy.CUSTOM,
            rpc_tensor_split=[],
        )
        config.validate()


class IdentifierAndLayoutTests(unittest.TestCase):
    def test_shared_identifiers_accept_current_generated_forms(self) -> None:
        samples = (
            (validate_node_id, "jetson-01"),
            (validate_experiment_id, "pi5-scaling_01"),
            (validate_suite_id, "suite_20260820_ab12"),
            (validate_run_id, "20260820_123456_abc123"),
        )
        for validator, value in samples:
            with self.subTest(validator=validator.__name__, value=value):
                self.assertEqual(validator(value), value)

    def test_shared_identifiers_reject_path_or_command_like_values(self) -> None:
        validators = (validate_node_id, validate_experiment_id, validate_suite_id, validate_run_id)
        for validator in validators:
            for value in ("", "../escape", "name/child", "name child", "name;command"):
                with self.subTest(validator=validator.__name__, value=value), self.assertRaises(
                    DomainValidationError
                ):
                    validator(value)

    def test_project_layout_derives_existing_compatibility_paths_without_io(self) -> None:
        layout = ProjectLayout(root=Path("/opt/llm-cluster-benchmark"))
        self.assertEqual(layout.cluster_dir, layout.root / "cluster")
        self.assertEqual(layout.runtime_dir, layout.root / ".run" / "cluster")
        self.assertEqual(layout.results_dir, layout.runtime_dir / "results")
        self.assertEqual(layout.experiments_dir, layout.runtime_dir / "experiments")
        self.assertEqual(layout.inventory_path, layout.runtime_dir / "nodes.local.csv")
        self.assertEqual(
            layout.run_dir("20260820_123456_abc123"),
            layout.results_dir / "20260820_123456_abc123",
        )
        self.assertEqual(
            layout.experiment_path("pi5-scaling"),
            layout.experiments_dir / "pi5-scaling.json",
        )
        self.assertEqual(
            layout.suite_path("suite_20260820_ab12"),
            layout.suites_dir / "suite_20260820_ab12.json",
        )
        self.assertEqual(
            layout.model_path("qwen/model.gguf"),
            layout.models_dir / "qwen" / "model.gguf",
        )
        for invalid_call in (
            lambda: layout.run_dir("../run"),
            lambda: layout.experiment_path("../experiment"),
            lambda: layout.suite_path("../suite"),
            lambda: layout.model_path("../model.gguf"),
        ):
            with self.assertRaises(DomainValidationError):
                invalid_call()
        with self.assertRaises(DomainValidationError):
            ProjectLayout(root=Path("relative/project"))
        for unsafe_root in (
            Path("/"),
            Path("/home"),
            Path("/home/tester"),
            Path("/opt"),
            Path("/srv"),
            Path("/tmp"),
            Path("/Users/tester"),
            Path("/tmp/bad\x00root"),
        ):
            with self.subTest(root=unsafe_root), self.assertRaises(DomainValidationError):
                ProjectLayout(root=unsafe_root)


class StructuredFailureTests(unittest.TestCase):
    def test_error_code_family_matches_master_spec(self) -> None:
        expected = {
            "WORKER_OFFLINE",
            "WORKER_AUTH_FAILED",
            "WORKER_TIMEOUT",
            "MODEL_MISSING",
            "MODEL_LOAD_FAILED",
            "MODEL_LOAD_OOM",
            "MODEL_CORRUPTED",
            "BACKEND_NOT_READY",
            "BACKEND_MISMATCH",
            "CONFIG_MISMATCH",
            "REQUEST_TIMEOUT",
            "INFERENCE_FAILED",
            "RPC_NOT_PREPARED",
            "RPC_DEVICE_FAILED",
            "RPC_COORDINATOR_FAILED",
            "RPC_CONNECTION_FAILED",
            "RPC_MODEL_LOAD_FAILED",
            "RPC_CLEANUP_FAILED",
            "CANCELLED",
            "UNKNOWN",
        }
        self.assertEqual({code.value for code in ErrorCode}, expected)

    def test_failure_record_has_json_ready_structured_fields(self) -> None:
        record = FailureRecord(
            code=ErrorCode.MODEL_MISSING,
            stage="preflight",
            message="Required model is not installed",
            node="jetson-01",
            model_id="models/a.gguf",
            evidence={"installed": False},
            solutions=("Install and verify the model before starting the experiment.",),
        )
        payload = record.to_dict()
        self.assertEqual(payload["code"], "MODEL_MISSING")
        self.assertEqual(payload["stage"], "preflight")
        self.assertEqual(payload["node"], "jetson-01")
        self.assertEqual(payload["model_id"], "models/a.gguf")
        self.assertEqual(payload["evidence"], {"installed": False})
        self.assertEqual(len(payload["solutions"]), 1)
        json.dumps(payload)

    def test_domain_validation_error_is_value_error_with_structured_code(self) -> None:
        with self.assertRaises(DomainValidationError) as raised:
            validate_node_id("../bad")
        self.assertIsInstance(raised.exception, ValueError)
        self.assertEqual(raised.exception.code, ErrorCode.CONFIG_MISMATCH)


class PureDomainBoundaryTests(unittest.TestCase):
    def test_domain_and_legacy_adapter_have_no_framework_or_side_effect_imports(self) -> None:
        modules = (
            controller_module,
            errors_module,
            experiment_module,
            identifiers_module,
            layout_module,
            strategy_module,
            worker_module,
            legacy_inventory_module,
        )
        forbidden_import_roots = {
            "fastapi",
            "pydantic",
            "subprocess",
            "urllib",
            "requests",
            "httpx",
            "psutil",
            "jtop",
            "socket",
        }
        forbidden_project_dependencies = (
            "cluster.clusterctl",
            "cluster.benchmark",
            "cluster.dashboard",
            "cluster.worker",
        )
        filesystem_call_names = {
            "open",
            "read_text",
            "read_bytes",
            "write_text",
            "write_bytes",
            "mkdir",
            "exists",
            "is_file",
            "is_dir",
            "stat",
            "chmod",
            "unlink",
            "rename",
            "glob",
            "rglob",
            "iterdir",
        }

        for module in modules:
            source_path = Path(inspect.getsourcefile(module) or "")
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            imported_modules: list[str] = []
            called_names: list[str] = []
            for item in ast.walk(tree):
                if isinstance(item, ast.Import):
                    imported_modules.extend(alias.name for alias in item.names)
                elif isinstance(item, ast.ImportFrom) and item.module:
                    imported_modules.append(item.module)
                elif isinstance(item, ast.Call):
                    if isinstance(item.func, ast.Name):
                        called_names.append(item.func.id)
                    elif isinstance(item.func, ast.Attribute):
                        called_names.append(item.func.attr)

            with self.subTest(module=module.__name__):
                self.assertFalse(
                    {name.split(".", 1)[0] for name in imported_modules}
                    & forbidden_import_roots
                )
                self.assertFalse(
                    any(
                        imported.startswith(prefix)
                        for imported in imported_modules
                        for prefix in forbidden_project_dependencies
                    )
                )
                self.assertFalse(set(called_names) & filesystem_call_names)

    def test_experiment_config_is_owned_by_the_domain_layer(self) -> None:
        self.assertEqual(ExperimentConfig.__module__, "cluster.domain.experiment")
        self.assertIs(LegacyRunnerExperimentConfig, ExperimentConfig)


if __name__ == "__main__":
    unittest.main()
