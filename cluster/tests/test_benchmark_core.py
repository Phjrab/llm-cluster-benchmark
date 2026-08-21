"""Phase 06 golden tests for benchmark planning, execution, and metrics."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from cluster.benchmark.executor import ScenarioExecutor
from cluster.benchmark.metrics import add_cumulative_scaling, aggregate_records, percentile
from cluster.benchmark.models import RequestTask, StrategyScenario
from cluster.benchmark.planner import build_strategy_scenarios
from cluster.benchmark.strategies import STRATEGY_REGISTRY
from cluster.clusterctl import Node
from cluster.domain.controller import ControllerConfig
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.worker import WorkerNode
from cluster.worker.inference import LlamaCppInferenceBackend


def worker(name: str, suffix: int) -> WorkerNode:
    return WorkerNode(
        name=name,
        host=f"192.168.10.{suffix}",
        user="bench",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/bench/{name}/llm-cluster",
    )


def record(task: RequestTask, node: WorkerNode, digest: str = "same") -> dict:
    return {
        "request_id": task.request_id,
        "logical_request_id": task.logical_request_id,
        "scenario_id": task.scenario_id,
        "replica_index": task.replica_index,
        "node": node.name,
        "ok": True,
        "ttft_s": 0.1,
        "e2e_s": 1.0,
        "generated_tokens": 10,
        "tokens_per_s": 10.0,
        "output_sha256": digest,
    }


class StrategyGoldenTests(unittest.TestCase):
    def test_registry_contains_every_supported_strategy(self) -> None:
        self.assertEqual(
            list(STRATEGY_REGISTRY),
            [
                "single_node",
                "replicated_round_robin",
                "broadcast_compare",
                "node_sweep",
                "model_parallel_rpc",
            ],
        )

    def test_five_requests_round_robin_over_two_workers(self) -> None:
        nodes = [worker("w1", 11), worker("w2", 12)]
        config = ExperimentConfig(node_names=["w1", "w2"], requests=5)
        scenario = build_strategy_scenarios(config, nodes)[0]
        self.assertEqual(
            [task.target_node for task in scenario.tasks],
            ["w1", "w2", "w1", "w2", "w1"],
        )

    def test_round_robin_supports_more_than_four_workers(self) -> None:
        nodes = [worker(f"w{index}", 10 + index) for index in range(1, 7)]
        config = ExperimentConfig(
            node_names=[node.name for node in nodes],
            requests=6,
            execution_strategy="replicated_round_robin",
        )
        scenario = build_strategy_scenarios(config, nodes)[0]
        self.assertEqual([task.target_node for task in scenario.tasks], [node.name for node in nodes])
        self.assertIsNone(STRATEGY_REGISTRY["replicated_round_robin"].description.max_nodes)

    def test_three_broadcast_logical_requests_make_six_physical_calls(self) -> None:
        nodes = [worker("w1", 11), worker("w2", 12)]
        config = ExperimentConfig(
            node_names=["w1", "w2"], requests=3,
            execution_strategy="broadcast_compare",
        )
        scenario = build_strategy_scenarios(config, nodes)[0]
        self.assertEqual(len(scenario.tasks), 6)
        self.assertEqual(
            [(task.logical_request_id, task.target_node) for task in scenario.tasks],
            [(1, "w1"), (1, "w2"), (2, "w1"), (2, "w2"), (3, "w1"), (3, "w2")],
        )
        self.assertEqual(scenario.concurrency_scope, "logical_group")

    def test_sweep_preserves_explicit_worker_order(self) -> None:
        nodes = [worker("w3", 13), worker("w1", 11), worker("w2", 12)]
        config = ExperimentConfig(
            node_names=[node.name for node in nodes], requests=1,
            execution_strategy="node_sweep",
        )
        scenarios = build_strategy_scenarios(config, nodes)
        self.assertEqual(
            [scenario.node_names for scenario in scenarios],
            [["w3"], ["w3", "w1"], ["w3", "w1", "w2"]],
        )

    def test_controller_cannot_enter_worker_plan(self) -> None:
        controller = ControllerConfig(
            "mac-controller",
            Path("/Users/test/project/runtime"),
            Path("/Users/test/project/results"),
        )
        config = ExperimentConfig(node_names=["controller"])
        with self.assertRaisesRegex(ValueError, "worker만"):
            build_strategy_scenarios(config, [controller])

    def test_rpc_planning_targets_explicit_worker_coordinator(self) -> None:
        first = worker("w1", 11)
        coordinator = worker("w2", 12)
        config = ExperimentConfig(
            node_names=["w1", "w2"],
            requests=2,
            execution_strategy="model_parallel_rpc",
            rpc_coordinator_node="w2",
            acknowledge_experimental_rpc=True,
        )
        scenario = build_strategy_scenarios(config, [first, coordinator])[0]
        self.assertEqual(scenario.execution_backend, "rpc")
        self.assertEqual(scenario.node_names, ["w1", "w2"])
        self.assertEqual([task.target_node for task in scenario.tasks], ["w2", "w2"])


class MetricsGoldenTests(unittest.TestCase):
    def test_percentile_p50_and_p95_match_legacy_interpolation(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.95), 3.85)

    def test_exact_answer_agreement_and_logical_physical_counts(self) -> None:
        w1, w2 = worker("w1", 11), worker("w2", 12)
        tasks = [
            RequestTask(1, 1, "broadcast", "w1", 0),
            RequestTask(2, 1, "broadcast", "w2", 1),
            RequestTask(3, 2, "broadcast", "w1", 0),
            RequestTask(4, 2, "broadcast", "w2", 1),
        ]
        records = [record(tasks[0], w1), record(tasks[1], w2), record(tasks[2], w1, "a"), record(tasks[3], w2, "b")]
        summary = aggregate_records(records, 2.0)
        self.assertEqual(summary["logical_requests"], 2)
        self.assertEqual(summary["physical_requests"], 4)
        self.assertEqual(summary["answer_agreement_rate"], 0.5)

    def test_speedup_and_efficiency_use_first_cumulative_scenario(self) -> None:
        summaries = [
            {"cluster_tokens_per_s": 10.0, "nodes": ["w1"]},
            {"cluster_tokens_per_s": 18.0, "nodes": ["w1", "w2"]},
        ]
        add_cumulative_scaling(summaries)
        self.assertEqual(summaries[1]["speedup_vs_baseline"], 1.8)
        self.assertEqual(summaries[1]["scaling_efficiency"], 0.9)


class ExecutorGoldenTests(unittest.TestCase):
    def test_cancellation_stops_new_submission_and_warmup_is_not_measured(self) -> None:
        node = worker("w1", 11)
        cancelled = threading.Event()
        calls = []

        def stream(target, _config, task, warmup=False):
            calls.append((task.request_id, warmup))
            if not warmup:
                cancelled.set()
            return record(task, target)

        executor = ScenarioExecutor(stream, lambda *_args: {})
        config = ExperimentConfig(
            node_names=["w1"], requests=10, concurrency=2, warmup_requests=2
        )
        executor.warmup([node], config, threading.Event())
        scenario = StrategyScenario(
            "main", "requests", ["w1"],
            [RequestTask(index, index, "main", "w1") for index in range(1, 11)],
        )
        measured, _ = executor.execute(
            scenario, {"w1": node}, config, lambda *_args, **_kwargs: None,
            cancelled, 0, 10,
        )
        self.assertEqual([warmup for _, warmup in calls[:2]], [True, True])
        self.assertTrue(all(item.get("warmup") is not True for item in measured))
        self.assertLessEqual(len(measured), config.concurrency)

    def test_worker_backend_serializes_generation_per_node(self) -> None:
        active = 0
        maximum = 0
        state_lock = threading.Lock()

        class FakeLlama:
            def create_chat_completion(self, **_kwargs):
                nonlocal active, maximum
                with state_lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.03)
                with state_lock:
                    active -= 1
                yield {"choices": [{"delta": {"content": "ok"}}]}

        with tempfile.TemporaryDirectory() as directory:
            backend = LlamaCppInferenceBackend(Path(directory), llama_factory=lambda **_: None)
            backend.llm = FakeLlama()

            def consume() -> None:
                list(backend.stream_chat(
                    message="test", history=[], max_tokens=1,
                    temperature=0.0, top_p=1.0,
                ))

            threads = [threading.Thread(target=consume) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
