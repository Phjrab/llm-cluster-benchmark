"""Phase 14 cross-phase regression, security, and public-contract gates."""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster import clusterctl
from cluster.benchmark import runner as benchmark_runner
from cluster.benchmark.metrics import aggregate_records
from cluster.benchmark.planner import build_strategy_scenarios
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.worker import WorkerNode


ROOT = Path(__file__).resolve().parents[2]


def worker(name: str, suffix: int) -> WorkerNode:
    return WorkerNode(
        name=name,
        host=f"192.168.20.{suffix}",
        user="bench",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/bench/{name}/llm-cluster",
    )


def request_record(
    request_id: int,
    *,
    node: str,
    ok: bool,
    ttft_s: float | None,
    e2e_s: float,
    tokens: int,
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "logical_request_id": request_id,
        "scenario_id": "main",
        "replica_index": 0,
        "node": node,
        "ok": ok,
        "ttft_s": ttft_s,
        "e2e_s": e2e_s,
        "generated_tokens": tokens,
        "tokens_per_s": tokens / e2e_s if ok else None,
        "output_sha256": f"digest-{request_id}" if ok else "",
    }


def declared_routes(path: Path) -> set[tuple[str, str]]:
    """Collect literal FastAPI route decorators without starting either app."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    routes: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH"} or not decorator.args:
                continue
            route = decorator.args[0]
            if isinstance(route, ast.Constant) and isinstance(route.value, str):
                routes.add((method, route.value))
    return routes


class BenchmarkCompatibilityMatrixTests(unittest.TestCase):
    def test_shipped_experiment_defaults_select_a_worker_not_legacy_head(self) -> None:
        payload = json.loads(
            (ROOT / "cluster" / "config" / "experiment_defaults.json").read_text(
                encoding="utf-8"
            )
        )
        config = ExperimentConfig.from_dict(payload)
        config.validate()
        self.assertEqual(config.node_names, ["edge-worker-01"])
        self.assertNotIn("head", config.node_names[0].lower())

    def test_single_node_is_all_to_one_and_rejects_extra_workers(self) -> None:
        first = worker("worker-01", 11)
        second = worker("worker-02", 12)
        config = ExperimentConfig(
            node_names=[first.name],
            requests=4,
            execution_strategy="single_node",
        )
        scenario = build_strategy_scenarios(config, [first])[0]
        self.assertEqual(scenario.scenario_id, "main")
        self.assertEqual(scenario.node_names, [first.name])
        self.assertEqual(
            [(task.logical_request_id, task.target_node) for task in scenario.tasks],
            [(1, first.name), (2, first.name), (3, first.name), (4, first.name)],
        )
        with self.assertRaisesRegex(ValueError, "1대"):
            build_strategy_scenarios(config, [first, second])

    def test_metric_schema_excludes_failures_from_latency_and_keeps_throughput_math(self) -> None:
        records = [
            request_record(1, node="worker-01", ok=True, ttft_s=0.1, e2e_s=0.5, tokens=10),
            request_record(2, node="worker-02", ok=True, ttft_s=0.3, e2e_s=1.5, tokens=20),
            request_record(3, node="worker-02", ok=False, ttft_s=None, e2e_s=99.0, tokens=0),
        ]
        summary = aggregate_records(records, wall_s=2.0)
        self.assertEqual(summary["requests"], 3)
        self.assertEqual(summary["logical_requests"], 3)
        self.assertEqual(summary["physical_requests"], 3)
        self.assertEqual(summary["successful"], 2)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["success_rate"], round(2 / 3, 6))
        self.assertEqual(summary["requests_per_s"], 1.0)
        self.assertEqual(summary["total_generated_tokens"], 30)
        self.assertEqual(summary["cluster_tokens_per_s"], 15.0)
        self.assertAlmostEqual(summary["ttft_p50_s"], 0.2)
        self.assertAlmostEqual(summary["ttft_p95_s"], 0.29)
        self.assertAlmostEqual(summary["e2e_p50_s"], 1.0)
        self.assertAlmostEqual(summary["e2e_p95_s"], 1.45)
        self.assertEqual(summary["per_node"]["worker-02"]["requests"], 2)
        self.assertEqual(summary["per_node"]["worker-02"]["failed"], 1)

    def test_run_experiment_accepts_controller_worker_only_inventory(self) -> None:
        """The Mac Controller inventory must not require a synthetic legacy head."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "nodes.csv"
            inventory.write_text(
                "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file,platform\n"
                "worker-01,worker,192.168.20.11,bench,22,8000,"
                "/home/bench/worker-01/llm-cluster,true,,jetson\n",
                encoding="utf-8",
            )
            config = ExperimentConfig(
                experiment_id="worker-only-contract",
                node_names=["worker-01"],
                model_id="models/tiny.gguf",
                n_ctx=128,
                n_gpu_layers=0,
                requests=2,
                concurrency=1,
                max_tokens=2,
                warmup_requests=0,
                prompt="ping",
                execution_strategy="single_node",
            )

            def load_model(node: object, experiment: ExperimentConfig) -> dict[str, object]:
                return {
                    "node": node.name,
                    "model_id": experiment.model_id,
                    "n_ctx": experiment.n_ctx,
                    "n_gpu_layers": experiment.n_gpu_layers,
                    "n_batch": None,
                }

            def stream(node: object, _config: ExperimentConfig, task: object, warmup: bool = False) -> dict[str, object]:
                self.assertFalse(warmup)
                return {
                    "request_id": task.request_id,
                    "logical_request_id": task.logical_request_id,
                    "scenario_id": task.scenario_id,
                    "replica_index": task.replica_index,
                    "node": node.name,
                    "assigned_node": node.name,
                    "node_host": node.host,
                    "started_at": "2026-08-20T00:00:00+00:00",
                    "ok": True,
                    "ttft_s": 0.01,
                    "e2e_s": 0.02,
                    "server_generation_s": 0.01,
                    "generated_tokens": 1,
                    "tokens_per_s": 50.0,
                    "output_sha256": "digest",
                    "response": "pong",
                    "error": "",
                }

            with mock.patch.object(
                benchmark_runner, "_load_model", side_effect=load_model
            ), mock.patch.object(
                benchmark_runner, "_worker_stream_adapter", side_effect=stream
            ), mock.patch.object(benchmark_runner, "_rpc_backend", return_value=mock.Mock()):
                summary = benchmark_runner.run_experiment(
                    config,
                    inventory_path=inventory,
                    results_root=root / "results",
                )

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["nodes"], ["worker-01"])
        self.assertEqual(summary["execution_strategy"], "single_node")
        self.assertEqual(summary["logical_requests"], 2)
        self.assertEqual(summary["physical_requests"], 2)


class WorkerApiSecurityContractTests(unittest.TestCase):
    @unittest.skipUnless(
        importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"),
        "worker API contract dependencies are not installed",
    )
    def test_worker_token_auth_calls_constant_time_compare(self) -> None:
        from fastapi.testclient import TestClient
        from cluster.worker import app as worker_app

        class Backend:
            def list_models(self) -> list[dict[str, object]]:
                return []

            def current_model_info(self) -> dict[str, object]:
                return {"loaded": False, "model_id": None}

            def readiness(self) -> dict[str, object]:
                return {"ready": True, "error": None}

        class Telemetry:
            def start(self) -> None:
                return None

            def status(self) -> dict[str, object]:
                return {"provider": "fake", "ready": True, "degraded": False, "error": None}

            def snapshot(self) -> dict[str, object]:
                return {"gpu_pct": None, "power_w": None}

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            worker_app, "runtime_backend", return_value={"verified": True, "kind": "cpu", "gpu_offload": False}
        ), mock.patch.object(worker_app, "system_profile", return_value={}):
            app = worker_app.create_app(
                backend=Backend(),
                telemetry=Telemetry(),
                project_root=Path(directory),
                environment={
                    "CLUSTER_PLATFORM": "raspberry-pi",
                    "CLUSTER_NODE_NAME": "worker-01",
                    "CLUSTER_NODE_ROLE": "worker",
                    "CLUSTER_WORKER_AUTH": "true",
                    "CLUSTER_API_TOKEN": "expected-token",
                },
            )
            with mock.patch.object(
                worker_app.secrets,
                "compare_digest",
                wraps=worker_app.secrets.compare_digest,
            ) as compare_digest:
                client = TestClient(app)
                worker_routes = declared_routes(ROOT / "cluster" / "worker" / "routes.py")
                for method, route in worker_routes:
                    with self.subTest(method=method, route=route):
                        denied = client.request(
                            method,
                            route,
                            headers={"X-Cluster-Worker-Token": "wrong-token"},
                        )
                        self.assertEqual(denied.status_code, 401)
                response = client.get(
                    "/cluster/health",
                    headers={"X-Cluster-Worker-Token": "expected-token"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(compare_digest.call_count, len(worker_routes) + 1)
        compare_digest.assert_any_call("wrong-token", "expected-token")
        compare_digest.assert_any_call("expected-token", "expected-token")


class PublicCompatibilityAndPackagingTests(unittest.TestCase):
    def test_runtime_path_overrides_reach_cli_and_worker_auth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            runtime.mkdir()
            (runtime / "settings.json").write_text(
                '{"worker_api_auth": true, "dashboard_token_auth": false}\n',
                encoding="utf-8",
            )
            inventory = runtime / "custom-nodes.csv"
            results = runtime / "custom-results"
            environment = {
                "CLUSTER_RUNTIME_DIR": str(runtime),
                "CLUSTER_INVENTORY": str(inventory),
                "CLUSTER_RESULTS_DIR": str(results),
            }
            with mock.patch.dict("os.environ", environment, clear=False):
                self.assertTrue(clusterctl.worker_auth_enabled())
                self.assertEqual(clusterctl.build_parser().parse_args(["inventory"]).inventory, inventory)
                config_path = Path(directory) / "config.json"
                config_path.write_text("{}\n", encoding="utf-8")
                with mock.patch(
                    "sys.argv", ["runner", "--config", str(config_path)]
                ), mock.patch.object(
                    benchmark_runner, "run_experiment", return_value={"status": "completed"}
                ) as run, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(benchmark_runner.main(), 0)
                self.assertEqual(run.call_args.kwargs["inventory_path"], inventory)
                self.assertEqual(run.call_args.kwargs["results_root"], results)

    def test_clusterctl_main_accepts_worker_only_controller_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "nodes.csv"
            inventory.write_text(
                "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file,platform\n"
                "worker-01,worker,192.168.20.11,bench,22,8000,"
                "/home/bench/worker-01/llm-cluster,true,,jetson\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch(
                "sys.argv",
                ["clusterctl", "--inventory", str(inventory), "inventory"],
            ), contextlib.redirect_stdout(output):
                exit_code = clusterctl.main()

        self.assertEqual(exit_code, 0)
        self.assertIn("worker-01", output.getvalue())

    def test_dashboard_and_worker_api_compatibility_subsets_remain_registered(self) -> None:
        dashboard_routes = declared_routes(ROOT / "cluster" / "dashboard" / "routes.py")
        worker_routes = declared_routes(ROOT / "cluster" / "worker" / "routes.py")
        self.assertTrue(
            {
                ("GET", "/"),
                ("GET", "/dashboard/health"),
                ("GET", "/api/controller/status"),
                ("GET", "/api/bootstrap"),
                ("GET", "/api/events"),
                ("GET", "/api/settings"),
                ("PUT", "/api/settings"),
                ("GET", "/api/status"),
                ("GET", "/api/models"),
                ("POST", "/api/status/refresh"),
                ("POST", "/api/network/scan"),
                ("POST", "/api/nodes/probe"),
                ("POST", "/api/nodes"),
                ("PATCH", "/api/nodes/{node_name}/name"),
                ("DELETE", "/api/nodes/{node_name}"),
                ("POST", "/api/actions"),
                ("GET", "/api/actions"),
                ("GET", "/api/environment"),
                ("POST", "/api/experiments"),
                ("GET", "/api/experiments"),
                ("GET", "/api/experiment-groups"),
                ("POST", "/api/experiments/cancel"),
                ("GET", "/api/runs/{run_id}"),
                ("GET", "/api/runs/{run_id}/responses"),
                ("DELETE", "/api/runs/{run_id}"),
            }.issubset(dashboard_routes)
        )
        self.assertTrue(
            {
                ("GET", "/health"),
                ("GET", "/api/models"),
                ("POST", "/api/select-model"),
                ("POST", "/api/unload-model"),
                ("POST", "/api/chat/stream"),
                ("GET", "/cluster/health"),
                ("GET", "/cluster/models"),
                ("POST", "/cluster/models/verify"),
                ("POST", "/cluster/models/delete"),
                ("POST", "/cluster/models/install"),
                ("POST", "/cluster/chat/stream"),
            }.issubset(worker_routes)
        )

    def test_clusterctl_compatibility_commands_remain_additive(self) -> None:
        parser = clusterctl.build_parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        self.assertTrue(
            {
                "inventory",
                "status",
                "doctor",
                "environment-check",
                "environment-install",
                "discover",
                "setup",
                "sync-code",
                "sync-models",
                "delete-models",
                "install-model-url",
                "prepare",
                "prepare-rpc",
                "start",
                "stop",
                "restart",
                "select-model",
            }.issubset(subparsers.choices)
        )

        status = parser.parse_args(
            [
                "--inventory",
                "/tmp/nodes.csv",
                "--node",
                "worker-01",
                "--node",
                "worker-02",
                "status",
                "--json-out",
                "/tmp/status.json",
            ]
        )
        self.assertEqual(status.command, "status")
        self.assertEqual(status.node, ["worker-01", "worker-02"])
        self.assertEqual(status.json_out, "/tmp/status.json")
        sync_models = parser.parse_args(
            ["sync-models", "--model", "a.gguf", "--model", "b.gguf", "--dry-run"]
        )
        self.assertEqual(sync_models.model, ["a.gguf", "b.gguf"])
        self.assertTrue(sync_models.dry_run)
        environment_install = parser.parse_args(["environment-install", "--confirmed"])
        self.assertTrue(environment_install.confirmed)
        selection = parser.parse_args(
            [
                "select-model",
                "--model-id",
                "models/tiny.gguf",
                "--n-ctx",
                "2048",
                "--n-gpu-layers",
                "0",
            ]
        )
        self.assertEqual(selection.model_id, "models/tiny.gguf")
        self.assertEqual(selection.n_ctx, 2048)
        self.assertEqual(selection.n_gpu_layers, 0)

    def test_controller_dependency_and_import_boundary_excludes_inference_runtime(self) -> None:
        controller_requirements = (ROOT / "requirements-controller.txt").read_text(
            encoding="utf-8"
        ).lower()
        self.assertNotIn("llama-cpp-python", controller_requirements)
        self.assertNotIn("torch", controller_requirements)

        controller_sources = [
            *(ROOT / "cluster" / "dashboard").glob("*.py"),
            ROOT / "cluster" / "cli" / "controller.py",
        ]
        forbidden: list[tuple[str, str]] = []
        for source in controller_sources:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    if module == "llama_cpp" or module.startswith("cluster.worker"):
                        forbidden.append((source.name, module))
        self.assertEqual(forbidden, [])


if __name__ == "__main__":
    unittest.main()
