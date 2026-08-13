from __future__ import annotations

import tempfile
import subprocess
import os
import threading
from unittest import mock
import unittest
from pathlib import Path

from cluster.benchmark.runner import (
    ExperimentConfig,
    RequestTask,
    StrategyScenario,
    _aggregate,
    _measure_scenario,
    _rpc_platform_from_check,
    _stop_rpc_topology,
    build_strategy_scenarios,
    percentile,
    strategy_work_units,
    validate_platform_layers,
    validate_strategy,
)
from cluster import clusterctl
from cluster.clusterctl import Node, load_nodes, select_nodes


INVENTORY = """name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file
jetson-head,head,127.0.0.1,jetson,22,8000,/opt/llm,true,
jetson-worker-01,worker,192.168.0.27,jetson,22,8000,/opt/llm,true,
jetson-worker-02,worker,192.168.0.28,jetson,22,8000,/opt/llm,false,
"""


class InventoryTests(unittest.TestCase):
    def test_loads_enabled_nodes_and_selects_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.csv"
            path.write_text(INVENTORY, encoding="utf-8")
            nodes = load_nodes(path)
            self.assertEqual([node.name for node in nodes], ["jetson-head", "jetson-worker-01"])
            selected = select_nodes(nodes, ["jetson-worker-01"])
            self.assertEqual(selected[0].role, "worker")
            self.assertEqual(selected[0].platform, "auto")

    def test_loads_platform_column_without_breaking_old_inventory(self) -> None:
        inventory = INVENTORY.replace(
            "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file",
            "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file,platform",
        ).replace("/opt/llm,true,", "/opt/llm,true,,jetson", 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.csv"
            path.write_text(inventory, encoding="utf-8")
            nodes = load_nodes(path)
            self.assertEqual(nodes[0].platform, "jetson")

    def test_rejects_inventory_without_one_enabled_head(self) -> None:
        invalid = INVENTORY.replace("jetson-head,head,127.0.0.1,jetson,22,8000,/opt/llm,true", "jetson-head,head,127.0.0.1,jetson,22,8000,/opt/llm,false")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.csv"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exactly one enabled head"):
                load_nodes(path)


class ExperimentTests(unittest.TestCase):
    def test_validates_reproducible_config(self) -> None:
        config = ExperimentConfig(node_names=["jetson-head"])
        config.validate()

    def test_rejects_unsafe_model_path(self) -> None:
        config = ExperimentConfig(node_names=["jetson-head"], model_id="../model.gguf")
        with self.assertRaisesRegex(ValueError, "safe relative"):
            config.validate()

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.95), 3.85)

    def test_aggregate_keeps_graph_ready_per_node_metrics(self) -> None:
        records = [
            {"request_id": 1, "node": "head", "ok": True, "ttft_s": 0.1, "e2e_s": 1.0, "generated_tokens": 10, "tokens_per_s": 12.0},
            {"request_id": 2, "node": "head", "ok": True, "ttft_s": 0.2, "e2e_s": 2.0, "generated_tokens": 20, "tokens_per_s": 14.0},
            {"request_id": 3, "node": "worker", "ok": False, "ttft_s": None, "e2e_s": 0.5, "generated_tokens": 0, "tokens_per_s": None},
        ]
        result = _aggregate(records, wall_s=2.0)
        self.assertEqual(result["cluster_tokens_per_s"], 15.0)
        self.assertEqual(result["per_node"]["head"]["effective_tokens_per_s"], 15.0)
        self.assertAlmostEqual(result["per_node"]["head"]["ttft_p50_s"], 0.15)
        self.assertEqual(result["per_node"]["worker"]["success_rate"], 0.0)

    def test_experiment_id_is_validated(self) -> None:
        ExperimentConfig(experiment_id="pi5-scaling", node_names=["head"]).validate()
        with self.assertRaisesRegex(ValueError, "experiment_id"):
            ExperimentConfig(experiment_id="../../bad", node_names=["head"]).validate()

    def test_pi_rejects_nonzero_gpu_layers(self) -> None:
        pi = Node("pi", "worker", "192.168.0.30", "pi", 22, 8000, "/home/pi/llm", True, platform="raspberry-pi")
        with self.assertRaisesRegex(ValueError, "n_gpu_layers=0"):
            validate_platform_layers([pi], ExperimentConfig(node_names=["pi"], n_gpu_layers=30))
        validate_platform_layers([pi], ExperimentConfig(node_names=["pi"], n_gpu_layers=0))

    def test_old_config_defaults_to_replicated_round_robin(self) -> None:
        config = ExperimentConfig.from_dict({"node_names": ["head"]})
        self.assertEqual(config.execution_strategy, "replicated_round_robin")

    def test_round_robin_strategy_plan_is_balanced(self) -> None:
        nodes = [
            Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True),
            Node("worker", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True),
        ]
        config = ExperimentConfig(node_names=[node.name for node in nodes], requests=5)
        plan = build_strategy_scenarios(config, nodes)
        counts = {node.name: sum(task.target_node == node.name for task in plan[0].tasks) for node in nodes}
        self.assertEqual(len(plan[0].tasks), 5)
        self.assertLessEqual(abs(counts["head"] - counts["worker"]), 1)

    def test_broadcast_plan_expands_logical_requests(self) -> None:
        nodes = [
            Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True),
            Node("worker", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True),
        ]
        config = ExperimentConfig(
            node_names=[node.name for node in nodes],
            execution_strategy="broadcast_compare",
            requests=3,
        )
        plan = build_strategy_scenarios(config, nodes)
        self.assertEqual(len(plan[0].tasks), 6)
        self.assertEqual(strategy_work_units(config, len(nodes)), 6)

    def test_broadcast_aggregate_separates_logical_and_physical_calls(self) -> None:
        records = [
            {"request_id": 1, "logical_request_id": 1, "scenario_id": "broadcast", "node": "head", "ok": True, "ttft_s": 0.1, "e2e_s": 1.0, "generated_tokens": 4, "tokens_per_s": 4.0, "output_sha256": "same"},
            {"request_id": 2, "logical_request_id": 1, "scenario_id": "broadcast", "node": "worker", "ok": True, "ttft_s": 0.2, "e2e_s": 1.1, "generated_tokens": 4, "tokens_per_s": 4.0, "output_sha256": "same"},
        ]
        result = _aggregate(records, wall_s=1.1)
        self.assertEqual(result["logical_requests"], 1)
        self.assertEqual(result["physical_requests"], 2)
        self.assertEqual(result["answer_agreement_rate"], 1.0)

    def test_node_sweep_preserves_selected_order(self) -> None:
        nodes = [
            Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True),
            Node("w1", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True),
            Node("w2", "worker", "192.168.0.3", "jetson", 22, 8000, "/opt/llm", True),
        ]
        config = ExperimentConfig(
            node_names=[node.name for node in nodes], execution_strategy="node_sweep", requests=2
        )
        plan = build_strategy_scenarios(config, nodes)
        self.assertEqual([scenario.node_names for scenario in plan], [["head"], ["head", "w1"], ["head", "w1", "w2"]])
        self.assertEqual(sum(len(scenario.tasks) for scenario in plan), 6)
        self.assertEqual(strategy_work_units(config, len(nodes)), 6)

    def test_rpc_requires_head_worker_and_acknowledgement(self) -> None:
        head = Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True)
        worker = Node("worker", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True)
        config = ExperimentConfig(
            node_names=["head", "worker"], execution_strategy="model_parallel_rpc"
        )
        with self.assertRaisesRegex(ValueError, "실험적"):
            validate_strategy([head, worker], config)
        config.acknowledge_experimental_rpc = True
        validate_strategy([head, worker], config)

    def test_cancellation_does_not_queue_the_entire_scenario(self) -> None:
        head = Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True)
        config = ExperimentConfig(node_names=["head"], requests=20, concurrency=2)
        scenario = StrategyScenario(
            "main",
            "cancel-test",
            ["head"],
            [RequestTask(index, index, "main", "head") for index in range(1, 21)],
        )
        cancelled = threading.Event()

        def fake_request(_node, _config, task):
            cancelled.set()
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "node": "head",
                "ok": True,
                "ttft_s": 0.01,
                "e2e_s": 0.02,
                "generated_tokens": 1,
                "tokens_per_s": 50.0,
            }

        with mock.patch("cluster.benchmark.runner._stream_request", side_effect=fake_request) as stream:
            records, _ = _measure_scenario(
                scenario,
                {"head": head},
                config,
                lambda *_args, **_kwargs: None,
                cancelled,
                0,
                20,
            )
        self.assertLessEqual(stream.call_count, config.concurrency)
        self.assertLessEqual(len(records), config.concurrency)

    def test_rpc_check_identifies_pi_head_for_loopback_device(self) -> None:
        head = Node("pi-head", "head", "127.0.0.1", "pi", 22, 8000, "/opt/llm", True)
        self.assertEqual(
            _rpc_platform_from_check(
                head,
                {"stdout": "[OK] llama.cpp RPC commit=abc platform=raspberry-pi", "stderr": ""},
            ),
            "raspberry-pi",
        )

    def test_rpc_cleanup_reports_failed_stop(self) -> None:
        head = Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True)
        worker = Node("worker", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True)
        with mock.patch(
            "cluster.benchmark.runner._rpc_runtime_command",
            return_value={"ok": False, "stdout": "", "stderr": "stop failed"},
        ):
            errors = _stop_rpc_topology(head, [worker])
        self.assertEqual(len(errors), 2)
        self.assertTrue(all("stop failed" in error for error in errors))


class PlatformPlanTests(unittest.TestCase):
    def test_platform_plans_select_distinct_backends(self) -> None:
        script = Path(__file__).resolve().parents[1] / "worker_setup.sh"
        jetson = subprocess.run(
            [str(script), "--plan-only", "--platform", "jetson"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        pi = subprocess.run(
            [str(script), "--plan-only", "--platform", "raspberry-pi"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn("backend=cuda", jetson)
        self.assertIn("GGML_CUDA=ON", jetson)
        self.assertIn("backend=openblas n_gpu_layers=0", pi)
        self.assertIn("libopenblas-dev", pi)

    def test_worker_api_auth_is_disabled_by_default_and_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "settings.json"
            environment = dict(os.environ)
            environment.pop("CLUSTER_WORKER_AUTH", None)
            with mock.patch.object(clusterctl, "DEFAULT_SETTINGS", settings), mock.patch.dict(
                os.environ, environment, clear=True
            ):
                self.assertFalse(clusterctl.worker_auth_enabled())
                settings.write_text('{"worker_api_auth": true}\n', encoding="utf-8")
                self.assertTrue(clusterctl.worker_auth_enabled())
                os.environ["CLUSTER_WORKER_AUTH"] = "false"
                self.assertFalse(clusterctl.worker_auth_enabled())


if __name__ == "__main__":
    unittest.main()
