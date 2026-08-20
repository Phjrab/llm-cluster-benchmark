"""Phase 07 worker RPC coordinator selection and cleanup gates."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cluster.benchmark.core import BenchmarkRunner
from cluster.benchmark.executor import ScenarioExecutor
from cluster.benchmark.models import RequestTask
from cluster.benchmark.rpc import (
    RpcBackendError,
    RpcSession,
    WorkerRpcBackend,
    select_rpc_coordinator,
)
from cluster.benchmark.transport import stream_rpc_request
from cluster.domain.controller import ControllerConfig
from cluster.domain.errors import ErrorCode
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.worker import WorkerNode


def worker(name: str, host: str, platform: str) -> WorkerNode:
    return WorkerNode(
        name=name,
        host=host,
        user="bench",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/bench/{name}/llm-cluster",
        platform=platform,
    )


def rpc_config(nodes: list[WorkerNode], **overrides: object) -> ExperimentConfig:
    values = {
        "node_names": [node.name for node in nodes],
        "model_id": "test-model.gguf",
        "execution_strategy": "model_parallel_rpc",
        "acknowledge_experimental_rpc": True,
        "warmup_requests": 0,
        "requests": 1,
        "concurrency": 1,
    }
    values.update(overrides)
    return ExperimentConfig(**values)


class CoordinatorSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pi1 = worker("pi-1", "192.168.10.21", "raspberry-pi")
        self.jetson = worker("jetson-1", "192.168.10.11", "jetson")
        self.pi2 = worker("pi-2", "192.168.10.22", "raspberry-pi")

    def test_explicit_selected_worker_wins(self) -> None:
        selected = select_rpc_coordinator(
            [self.jetson, self.pi1], explicit="pi-1"
        )
        self.assertIs(selected, self.pi1)

    def test_automatic_selection_is_jetson_first(self) -> None:
        selected = select_rpc_coordinator([self.pi1, self.jetson, self.pi2])
        self.assertIs(selected, self.jetson)

    def test_pi_only_selection_uses_first_selected_pi(self) -> None:
        selected = select_rpc_coordinator([self.pi2, self.pi1])
        self.assertIs(selected, self.pi2)

    def test_invalid_explicit_coordinator_is_structured(self) -> None:
        with self.assertRaises(RpcBackendError) as caught:
            select_rpc_coordinator([self.pi1, self.jetson], explicit="missing")
        self.assertEqual(caught.exception.code, ErrorCode.CONFIG_MISMATCH)

    def test_controller_and_legacy_head_cannot_be_coordinator(self) -> None:
        controller = ControllerConfig(
            "mac-controller",
            Path("/Users/test/project/runtime"),
            Path("/Users/test/project/results"),
        )
        with self.assertRaises(RpcBackendError):
            select_rpc_coordinator([controller, self.jetson])
        legacy_head = SimpleNamespace(name="head", role="head")
        with self.assertRaises(RpcBackendError):
            select_rpc_coordinator([legacy_head, self.jetson])


class WorkerRpcBackendTests(unittest.TestCase):
    def test_topology_and_command_use_actual_worker_coordinator(self) -> None:
        pi = worker("pi-1", "192.168.10.21", "raspberry-pi")
        jetson = worker("jetson-1", "192.168.10.11", "jetson")
        commands: list[tuple[str, str, tuple[str, ...]]] = []

        def runtime(node: WorkerNode, action: str, *args: str, timeout: int = 0) -> dict:
            commands.append((node.name, action, args))
            if action == "check":
                return {
                    "node": node.name,
                    "ok": True,
                    "stdout": f"platform={node.platform}",
                    "stderr": "",
                }
            return {"node": node.name, "ok": True, "stdout": "ok", "stderr": ""}

        def remote(node: WorkerNode, argv: list[str], timeout: int = 0) -> SimpleNamespace:
            if argv[:2] == ["test", "-f"]:
                self.assertEqual(
                    argv[2],
                    "/home/bench/jetson-1/llm-cluster/models/test-model.gguf",
                )
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="pinned-commit\n", stderr="")

        backend = WorkerRpcBackend(runtime, lambda *args, **kwargs: {"ok": True}, remote)
        config = rpc_config(
            [pi, jetson],
            rpc_coordinator_node="jetson-1",
            rpc_split_policy="custom",
            rpc_tensor_split=[1.0, 3.0],
        )
        session = backend.start([pi, jetson], config, lambda *args, **kwargs: None)

        self.assertEqual(session.coordinator.name, "jetson-1")
        self.assertEqual(session.url, "http://192.168.10.11:18080")
        self.assertEqual(session.topology["coordinator"], "jetson-1")
        self.assertEqual(session.topology["participants"], ["pi-1", "jetson-1"])
        self.assertEqual(session.topology["rpc_workers"], ["pi-1"])
        self.assertEqual(session.topology["resolved_device_order"], ["pi-1", "jetson-1"])
        self.assertEqual(session.topology["tensor_split"], [1.0, 3.0])
        coordinator_start = next(
            args for name, action, args in commands
            if name == "jetson-1" and action == "start-coordinator"
        )
        self.assertEqual(coordinator_start[-1], "0.0.0.0")
        self.assertNotIn("127.0.0.1:18080", session.url)
        session.close()

    def test_pi_only_topology_includes_coordinator_loopback_device(self) -> None:
        pi1 = worker("pi-1", "192.168.10.21", "raspberry-pi")
        pi2 = worker("pi-2", "192.168.10.22", "raspberry-pi")

        def runtime(node: WorkerNode, action: str, *args: str, timeout: int = 0) -> dict:
            return {
                "node": node.name,
                "ok": True,
                "stdout": f"platform={node.platform}" if action == "check" else "ok",
                "stderr": "",
            }

        backend = WorkerRpcBackend(
            runtime,
            lambda *args, **kwargs: {"ok": True},
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout="pinned-commit\n", stderr=""
            ),
        )
        session = backend.start([pi2, pi1], rpc_config([pi2, pi1]), lambda *args, **kwargs: None)
        self.assertEqual(session.coordinator.name, "pi-2")
        self.assertEqual(session.topology["rpc_device_nodes"], ["pi-1", "pi-2"])
        self.assertEqual(session.topology["resolved_device_order"], ["pi-1", "pi-2"])
        session.close()

    def test_preflight_and_cleanup_failures_have_stable_codes(self) -> None:
        first = worker("w1", "192.168.10.11", "jetson")
        second = worker("w2", "192.168.10.12", "jetson")

        backend = WorkerRpcBackend(
            lambda node, action, *args, timeout=0: {
                "node": node.name, "ok": False, "stdout": "", "stderr": "missing"
            },
            lambda *args, **kwargs: {},
            lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
        )
        with self.assertRaises(RpcBackendError) as caught:
            backend.start([first, second], rpc_config([first, second]), lambda *args, **kwargs: None)
        self.assertEqual(caught.exception.code, ErrorCode.RPC_NOT_PREPARED)

        session = RpcSession(first, "http://example", {}, [], lambda: ["worker still running"])
        with self.assertRaises(RpcBackendError) as cleanup:
            session.close()
        self.assertEqual(cleanup.exception.code, ErrorCode.RPC_CLEANUP_FAILED)

    def test_device_model_and_coordinator_failures_have_stable_codes(self) -> None:
        first = worker("w1", "192.168.10.11", "jetson")
        second = worker("w2", "192.168.10.12", "jetson")
        config = rpc_config([first, second], rpc_coordinator_node="w1")

        cases = [
            ("start-worker", True, ErrorCode.RPC_DEVICE_FAILED),
            (None, False, ErrorCode.RPC_MODEL_LOAD_FAILED),
            ("start-coordinator", True, ErrorCode.RPC_COORDINATOR_FAILED),
        ]
        for failing_action, model_exists, expected in cases:
            with self.subTest(code=expected):
                def runtime(node, action, *args, timeout=0):
                    if action == "check":
                        return {
                            "node": node.name, "ok": True,
                            "stdout": "platform=jetson", "stderr": "",
                        }
                    if action == failing_action:
                        return {
                            "node": node.name, "ok": False,
                            "stdout": "", "stderr": "bind failed",
                        }
                    return {"node": node.name, "ok": True, "stdout": "ok", "stderr": ""}

                def remote(node, argv, timeout=0):
                    if argv[:2] == ["test", "-f"]:
                        return SimpleNamespace(
                            returncode=0 if model_exists else 1, stdout="", stderr="missing"
                        )
                    return SimpleNamespace(returncode=0, stdout="commit\n", stderr="")

                backend = WorkerRpcBackend(
                    runtime, lambda *args, **kwargs: {"ok": True}, remote
                )
                with self.assertRaises(RpcBackendError) as caught:
                    backend.start([first, second], config, lambda *args, **kwargs: None)
                self.assertEqual(caught.exception.code, expected)

    def test_rpc_transport_marks_connection_failure(self) -> None:
        coordinator = worker("w1", "192.168.10.11", "jetson")
        config = rpc_config(
            [coordinator, worker("w2", "192.168.10.12", "jetson")],
            rpc_coordinator_node="w1",
        )
        task = RequestTask(1, 1, "rpc-sharded", "w1")
        with mock.patch(
            "cluster.benchmark.transport.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            record = stream_rpc_request(
                coordinator, "http://192.168.10.11:18080", config, task
            )
        self.assertFalse(record["ok"])
        self.assertEqual(record["error_code"], ErrorCode.RPC_CONNECTION_FAILED.value)


class RunnerCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = [
            worker("w1", "192.168.10.11", "jetson"),
            worker("w2", "192.168.10.12", "jetson"),
        ]

    def run_case(self, stream, closer, cancel_event: threading.Event | None = None):
        config = rpc_config(self.nodes, rpc_coordinator_node="w1")

        class Backend:
            def start(inner, nodes, _config, _emit):
                return RpcSession(
                    nodes[0],
                    "http://192.168.10.11:18080",
                    {"coordinator": nodes[0].name, "participants": [item.name for item in nodes]},
                    [nodes[1]],
                    closer,
                )

        runner = BenchmarkRunner(
            lambda *args: {},
            lambda *args: [],
            lambda *args: None,
            ScenarioExecutor(lambda *args: {}, stream),
            Backend(),
        )
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return runner, config, Path(temporary.name), cancel_event or threading.Event()

    def test_cleanup_runs_after_request_failure(self) -> None:
        closes: list[str] = []

        def stream(node, url, config, task):
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "replica_index": task.replica_index,
                "node": node.name,
                "ok": False,
                "ttft_s": None,
                "e2e_s": 0.01,
                "generated_tokens": 0,
                "tokens_per_s": None,
                "error": "connection failed",
            }

        runner, config, root, event = self.run_case(stream, lambda: closes.append("closed") or [])
        summary = runner.run(config, self.nodes, root, cancel_event=event)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(closes, ["closed"])
        self.assertEqual(summary["topology"]["coordinator"], "w1")

    def test_cleanup_runs_after_success(self) -> None:
        closes: list[str] = []

        def stream(node, url, config, task):
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "replica_index": task.replica_index,
                "node": node.name,
                "ok": True,
                "ttft_s": 0.01,
                "e2e_s": 0.02,
                "generated_tokens": 1,
                "tokens_per_s": 50.0,
                "output_sha256": "digest",
            }

        runner, config, root, event = self.run_case(
            stream, lambda: closes.append("closed") or []
        )
        summary = runner.run(config, self.nodes, root, cancel_event=event)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["topology"]["coordinator"], "w1")
        self.assertEqual(closes, ["closed"])

    def test_cleanup_runs_after_cancellation(self) -> None:
        closes: list[str] = []
        cancelled = threading.Event()

        def stream(node, url, config, task):
            cancelled.set()
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "replica_index": task.replica_index,
                "node": node.name,
                "ok": True,
                "ttft_s": 0.01,
                "e2e_s": 0.02,
                "generated_tokens": 1,
                "tokens_per_s": 50.0,
                "output_sha256": "digest",
            }

        runner, config, root, _ = self.run_case(
            stream, lambda: closes.append("closed") or [], cancelled
        )
        summary = runner.run(config, self.nodes, root, cancel_event=cancelled)
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(closes, ["closed"])

    def test_cleanup_failure_cannot_produce_completed_summary(self) -> None:
        attempts: list[str] = []

        def stream(node, url, config, task):
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "replica_index": task.replica_index,
                "node": node.name,
                "ok": True,
                "ttft_s": 0.01,
                "e2e_s": 0.02,
                "generated_tokens": 1,
                "tokens_per_s": 50.0,
                "output_sha256": "digest",
            }

        def closer():
            attempts.append("failed")
            return ["stop failed"]

        runner, config, root, event = self.run_case(stream, closer)
        with self.assertRaises(RpcBackendError) as caught:
            runner.run(config, self.nodes, root, cancel_event=event)
        self.assertEqual(caught.exception.code, ErrorCode.RPC_CLEANUP_FAILED)
        summaries = list(root.glob("*/summary.json"))
        self.assertEqual(len(summaries), 1)
        saved = json.loads(summaries[0].read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "failed")
        self.assertNotEqual(saved["status"], "completed")
        self.assertGreaterEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
