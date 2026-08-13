from __future__ import annotations

import importlib
import tempfile
import subprocess
import os
import sys
import threading
import time
import json
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
    benchmark_parameters,
    build_strategy_scenarios,
    normalize_model_ids,
    percentile,
    run_experiment,
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

    def test_report_metadata_omits_prompt_text(self) -> None:
        config = ExperimentConfig(node_names=["head"], prompt="sensitive benchmark prompt")
        metadata = benchmark_parameters(config)
        self.assertNotIn("prompt", metadata)
        self.assertEqual(metadata["prompt_chars"], len(config.prompt))
        self.assertEqual(len(metadata["prompt_sha256"]), 64)

    def test_rejects_unsafe_model_path(self) -> None:
        config = ExperimentConfig(node_names=["jetson-head"], model_id="../model.gguf")
        with self.assertRaisesRegex(ValueError, "safe relative"):
            config.validate()

    def test_suite_coordinates_are_validated(self) -> None:
        ExperimentConfig(
            node_names=["jetson-head"],
            suite_id="suite_20260813_ab12",
            model_index=2,
            model_count=3,
        ).validate()
        with self.assertRaisesRegex(ValueError, "model_index"):
            ExperimentConfig(node_names=["jetson-head"], model_index=0, model_count=2).validate()
        with self.assertRaisesRegex(ValueError, "suite_id"):
            ExperimentConfig(node_names=["jetson-head"], suite_id="../../suite").validate()

    def test_model_suite_normalizes_legacy_payload_and_rejects_duplicates(self) -> None:
        legacy = normalize_model_ids("legacy/model.gguf", [])
        self.assertEqual(legacy, ["legacy/model.gguf"])
        selected = normalize_model_ids("stale.gguf", ["a.gguf", "b.gguf"])
        self.assertEqual(selected, ["a.gguf", "b.gguf"])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            normalize_model_ids("", ["a.gguf", "a.gguf"])
        with self.assertRaisesRegex(ValueError, "safe relative"):
            normalize_model_ids("", ["../escape.gguf"])

    def test_run_persists_suite_and_model_identity_in_summary_config_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "nodes.csv"
            inventory.write_text(INVENTORY, encoding="utf-8")
            config = ExperimentConfig(
                experiment_id="multi-model-comparison",
                node_names=["jetson-head"],
                model_id="models/example.gguf",
                n_ctx=128,
                n_gpu_layers=0,
                requests=1,
                concurrency=1,
                max_tokens=1,
                warmup_requests=0,
                suite_id="suite_20260813_ab12",
                model_index=2,
                model_count=3,
            )
            emitted = []

            def fake_request(node, _config, task):
                return {
                    "request_id": task.request_id,
                    "logical_request_id": task.logical_request_id,
                    "scenario_id": task.scenario_id,
                    "replica_index": task.replica_index,
                    "node": node.name,
                    "assigned_node": node.name,
                    "node_host": node.host,
                    "started_at": "2026-08-13T00:00:00+00:00",
                    "ok": True,
                    "ttft_s": 0.01,
                    "e2e_s": 0.02,
                    "server_ttft_s": 0.01,
                    "server_generation_s": 0.01,
                    "generated_tokens": 1,
                    "tokens_per_s": 100.0,
                    "output_chars": 1,
                    "output_sha256": "abc",
                    "error": "",
                    "warmup": False,
                }

            loaded = {
                "node": "jetson-head",
                "loaded": True,
                "model_id": config.model_id,
                "n_ctx": config.n_ctx,
                "n_gpu_layers": config.n_gpu_layers,
                "n_batch": 512,
            }
            with mock.patch("cluster.benchmark.runner._load_model", return_value=loaded), mock.patch(
                "cluster.benchmark.runner._stream_request", side_effect=fake_request
            ):
                summary = run_experiment(
                    config,
                    inventory_path=inventory,
                    results_root=root / "results",
                    progress=emitted.append,
                )

            self.assertEqual(summary["suite_id"], config.suite_id)
            self.assertEqual(summary["model_id"], config.model_id)
            self.assertEqual(summary["model_index"], 2)
            self.assertEqual(summary["model_count"], 3)
            saved_config = json.loads((Path(summary["result_dir"]) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_config["suite_id"], config.suite_id)
            self.assertTrue(emitted)
            self.assertTrue(all(event["suite_id"] == config.suite_id for event in emitted))
            self.assertTrue(all(event["model_id"] == config.model_id for event in emitted))

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

    def test_broadcast_concurrency_releases_slots_by_logical_group(self) -> None:
        nodes = [
            Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True),
            Node("worker", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True),
        ]
        config = ExperimentConfig(
            node_names=[node.name for node in nodes],
            execution_strategy="broadcast_compare",
            requests=2,
            concurrency=1,
        )
        scenario = build_strategy_scenarios(config, nodes)[0]
        lock = threading.Lock()
        first_started = 0
        first_completed = 0
        overlap = False
        both_started = threading.Event()

        def fake_request(node, _config, task):
            nonlocal first_started, first_completed, overlap
            if task.logical_request_id == 1:
                with lock:
                    first_started += 1
                    if first_started == 2:
                        both_started.set()
                self.assertTrue(both_started.wait(1))
                if node.name == "worker":
                    time.sleep(0.08)
                with lock:
                    first_completed += 1
            else:
                with lock:
                    overlap = overlap or first_completed < 2
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "node": node.name,
                "ok": True,
                "ttft_s": 0.01,
                "e2e_s": 0.02,
                "generated_tokens": 1,
                "tokens_per_s": 50.0,
            }

        with mock.patch("cluster.benchmark.runner._stream_request", side_effect=fake_request):
            records, _ = _measure_scenario(
                scenario,
                {node.name: node for node in nodes},
                config,
                lambda *_args, **_kwargs: None,
                threading.Event(),
                0,
                len(scenario.tasks),
            )
        self.assertEqual(len(records), 4)
        self.assertFalse(overlap)

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

    def test_warmup_cancellation_does_not_queue_every_request(self) -> None:
        head = Node("head", "head", "127.0.0.1", "jetson", 22, 8000, "/opt/llm", True)
        worker = Node("worker", "worker", "192.168.0.2", "jetson", 22, 8000, "/opt/llm", True)
        config = ExperimentConfig(
            node_names=["head", "worker"],
            model_id="models/test.gguf",
            requests=1,
            max_tokens=1,
            warmup_requests=10,
        )
        cancelled = threading.Event()
        started = threading.Event()
        calls = 0
        lock = threading.Lock()

        def fake_warmup(node, _config, _task, _warmup=False):
            nonlocal calls
            with lock:
                calls += 1
                if calls == 2:
                    started.set()
            started.wait(1)
            cancelled.set()
            time.sleep(0.03)
            return {"node": node.name, "ok": True, "error": ""}

        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "cluster.benchmark.runner.load_nodes", return_value=[head, worker]
        ), mock.patch(
            "cluster.benchmark.runner._load_model",
            side_effect=lambda node, _config: {
                "node": node.name,
                "model_id": config.model_id,
                "n_ctx": config.n_ctx,
                "n_gpu_layers": config.n_gpu_layers,
                "n_batch": 128,
            },
        ), mock.patch(
            "cluster.benchmark.runner._stream_request", side_effect=fake_warmup
        ):
            summary = run_experiment(
                config,
                inventory_path=Path(directory) / "nodes.csv",
                results_root=Path(directory) / "results",
                cancel_event=cancelled,
            )
        self.assertEqual(summary["status"], "cancelled")
        self.assertLessEqual(calls, len(config.node_names))

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


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("pydantic"),
    "dashboard runtime dependencies are not installed",
)
class DashboardSuitePersistenceTests(unittest.TestCase):
    @staticmethod
    def _load_dashboard(root: Path):
        inventory = root / "nodes.csv"
        inventory.write_text(INVENTORY, encoding="utf-8")
        if "cluster.dashboard.app" in sys.modules:
            return sys.modules["cluster.dashboard.app"]
        with mock.patch.dict(
            os.environ,
            {
                "CLUSTER_INVENTORY": str(inventory),
                "CLUSTER_RESULTS_DIR": str(root / "results"),
                "CLUSTER_RUNTIME_DIR": str(root / "runtime"),
            },
        ), mock.patch.object(threading.Thread, "start", return_value=None):
            return importlib.import_module("cluster.dashboard.app")

    def test_run_list_merges_persisted_suite_status_and_unrun_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self._load_dashboard(root)
            results = root / "results"
            run_dir = results / "run_1"
            run_dir.mkdir(parents=True)
            model_summary = {
                "run_id": "run_1",
                "suite_id": "suite_test_cancelled",
                "experiment_id": "experiment-1",
                "model_id": "models/a.gguf",
                "model_index": 1,
                "model_count": 2,
                "status": "cancelled",
            }
            (run_dir / "summary.json").write_text(
                json.dumps(model_summary), encoding="utf-8"
            )
            suite = dashboard._suite_document(
                suite_id="suite_test_cancelled",
                experiment_id="experiment-1",
                name="cancelled suite",
                status="cancelled",
                model_ids=["models/a.gguf", "models/b.gguf"],
                attempted_models=1,
                completed_models=0,
                total_work_units=2,
                completed_work_units=1,
                continue_on_model_error=False,
                model_cooldown_s=0,
                started_at="2026-08-13T00:00:00+00:00",
                finished_at="2026-08-13T00:01:00+00:00",
                summaries=[model_summary],
                errors=[],
            )

            with mock.patch.object(dashboard, "RESULTS_DIR", results):
                dashboard.write_suite_summary(suite)
                runs = dashboard.read_run_summaries()
                suites = dashboard.read_suite_summaries()

            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["suite_status"], "cancelled")
            self.assertEqual(runs[0]["suite_attempted_models"], 1)
            self.assertEqual(runs[0]["suite_models"][1]["status"], "unrun")
            self.assertEqual(suites[0]["models"][1]["model_id"], "models/b.gguf")

    def test_startup_reconciles_only_nonterminal_suite_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self._load_dashboard(root)
            results = root / "results"
            running = dashboard._suite_document(
                suite_id="suite_interrupted",
                experiment_id="experiment-1",
                name="interrupted suite",
                status="running",
                model_ids=["models/a.gguf", "models/b.gguf"],
                attempted_models=1,
                completed_models=1,
                total_work_units=2,
                completed_work_units=1,
                continue_on_model_error=False,
                model_cooldown_s=0,
                started_at="2026-08-13T00:00:00+00:00",
                summaries=[
                    {
                        "run_id": "run_1",
                        "model_id": "models/a.gguf",
                        "model_index": 1,
                        "status": "completed",
                    }
                ],
                errors=[],
                cleanup_statuses={1: "completed"},
            )
            completed = {**running, "suite_id": "suite_completed", "status": "completed"}

            with mock.patch.object(dashboard, "RESULTS_DIR", results):
                dashboard.write_suite_summary(running)
                dashboard.write_suite_summary(completed)
                self.assertEqual(dashboard.reconcile_interrupted_suites(), 1)
                suites = {
                    suite["suite_id"]: suite
                    for suite in dashboard.read_suite_summaries(limit=0)
                }

            interrupted = suites["suite_interrupted"]
            self.assertEqual(interrupted["status"], "failed")
            self.assertTrue(interrupted["interrupted"])
            self.assertEqual(interrupted["interrupted_from_status"], "running")
            self.assertEqual(interrupted["errors"][-1]["stage"], "dashboard_restart")
            self.assertEqual(interrupted["models"][1]["status"], "unrun")
            self.assertEqual(suites["suite_completed"]["status"], "completed")

    def test_final_success_is_unloaded_and_unload_failure_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self._load_dashboard(root)
            results = root / "results"
            manager = dashboard.ExperimentManager()
            manager._active = {
                "suite_id": "suite_test_cleanup",
                "started_at": "2026-08-13T00:00:00+00:00",
                "status": "queued",
            }
            config = ExperimentConfig(
                experiment_id="experiment-1",
                name="cleanup suite",
                node_names=["jetson-head"],
                requests=1,
                concurrency=1,
                max_tokens=1,
                warmup_requests=0,
            )

            def fake_run(model_config, **_kwargs):
                return {
                    "run_id": f"run_{model_config.model_index}",
                    "suite_id": model_config.suite_id,
                    "experiment_id": model_config.experiment_id,
                    "model_id": model_config.model_id,
                    "model_index": model_config.model_index,
                    "model_count": model_config.model_count,
                    "status": "completed",
                }

            with mock.patch.object(dashboard, "RESULTS_DIR", results), mock.patch.object(
                dashboard, "run_experiment", side_effect=fake_run
            ), mock.patch.object(
                manager,
                "_unload_models",
                side_effect=[[], ["jetson-head: unload failed"]],
            ) as unload, mock.patch.object(dashboard.status_monitor, "refresh_now"):
                manager._run(
                    config,
                    ["models/a.gguf", "models/b.gguf"],
                    continue_on_model_error=False,
                    model_cooldown_s=0,
                    cancel_event=threading.Event(),
                )

            suite = json.loads(
                (results / "_suites" / "suite_test_cleanup.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(unload.call_count, 2)
            self.assertEqual(manager.active()["status"], "partial")
            self.assertEqual(suite["status"], "partial")
            self.assertEqual(suite["models"][1]["cleanup_status"], "failed")
            self.assertEqual(suite["errors"][0]["stage"], "unload")


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
