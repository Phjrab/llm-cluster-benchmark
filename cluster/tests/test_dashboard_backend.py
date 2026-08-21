"""Phase 12 Dashboard backend wiring and contract regression tests."""

from __future__ import annotations

import ast
import importlib
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("pydantic"),
    "dashboard runtime dependencies are not installed",
)
class DashboardBackendTests(unittest.TestCase):
    @staticmethod
    def load_dashboard(root: Path):
        inventory = root / "nodes.csv"
        inventory.write_text(
            "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file,platform\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ,
            {
                "CLUSTER_INVENTORY": str(inventory),
                "CLUSTER_RESULTS_DIR": str(root / "results"),
                "CLUSTER_RUNTIME_DIR": str(root / "runtime"),
            },
        ), mock.patch.object(threading.Thread, "start", return_value=None):
            if "cluster.dashboard.app" in sys.modules:
                return importlib.reload(sys.modules["cluster.dashboard.app"])
            return importlib.import_module("cluster.dashboard.app")

    def test_app_is_wiring_and_routes_delegate_to_facade(self) -> None:
        dashboard_dir = Path(__file__).resolve().parents[1] / "dashboard"
        app_source = (dashboard_dir / "app.py").read_text(encoding="utf-8")
        routes_source = (dashboard_dir / "routes.py").read_text(encoding="utf-8")
        app_tree = ast.parse(app_source)
        self.assertLess(len(app_source.splitlines()), 100)
        self.assertIn("def create_app", app_source)
        self.assertIn("register_routers", app_source)
        self.assertNotIn("subprocess", app_source)
        self.assertFalse(
            any(isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) and getattr(node, "name", "") == "ActionManager" for node in app_tree.body)
        )
        self.assertIn("web_router = APIRouter", routes_source)
        self.assertIn("controller_router = APIRouter", routes_source)
        self.assertIn("nodes_router = APIRouter", routes_source)
        self.assertIn("models_router = APIRouter", routes_source)
        self.assertIn("events_router = APIRouter", routes_source)
        self.assertIn("experiments_router = APIRouter", routes_source)
        routes_tree = ast.parse(routes_source)
        imported = {
            alias.name
            for node in routes_tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("subprocess", imported)
        self.assertNotIn("concurrent.futures", imported)

    def test_controller_is_not_worker_and_model_api_is_additive(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            dashboard = self.load_dashboard(Path(directory))
            with TestClient(dashboard.app) as client:
                controller = client.get("/api/controller/status")
                models = client.get("/api/models")
                health = client.get("/dashboard/health")
                index = client.get("/")
            self.assertEqual(controller.status_code, 200)
            self.assertEqual(controller.json()["role"], "controller")
            self.assertFalse(controller.json()["inference_enabled"])
            self.assertEqual(health.status_code, 200)
            self.assertFalse(health.json()["inference_enabled"])
            self.assertEqual(models.status_code, 200)
            self.assertEqual(
                set(models.json()),
                {
                    "models",
                    "inventories",
                    "catalog",
                    "recommendations",
                    "starter_packs",
                    "catalog_policy",
                },
            )
            self.assertEqual(index.headers["cache-control"], "no-store")

    def test_structured_failure_and_raw_run_response_are_exposed(self) -> None:
        from fastapi.testclient import TestClient
        from cluster.dashboard.services import DashboardServiceError

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self.load_dashboard(root)
            run_dir = root / "results" / "run_backend_contract"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps({"run_id": "run_backend_contract", "status": "failed", "failures": [{"code": "MODEL_MISSING"}]}),
                encoding="utf-8",
            )
            (run_dir / "responses.jsonl").write_text(
                json.dumps({"request_id": 1, "prompt": "ping", "response": "pong"}) + "\n",
                encoding="utf-8",
            )
            service = dashboard.app.state.dashboard_services
            with mock.patch.object(
                service,
                "start_experiment",
                side_effect=DashboardServiceError(
                    409, {"code": "MODEL_MISSING", "stage": "model_preflight"}
                ),
            ), TestClient(dashboard.app) as client:
                failure = client.post(
                    "/api/experiments",
                    json={"node_names": ["worker-01"], "model_id": "models/a.gguf", "prompt": "ping"},
                )
                raw = client.get("/api/runs/run_backend_contract")
                responses = client.get("/api/runs/run_backend_contract/responses")
            self.assertEqual(failure.status_code, 409)
            self.assertEqual(failure.json()["detail"]["code"], "MODEL_MISSING")
            self.assertEqual(raw.status_code, 200)
            self.assertEqual(raw.json()["failures"][0]["code"], "MODEL_MISSING")
            self.assertEqual(responses.status_code, 200)
            self.assertEqual(responses.json()["responses"][0]["response"], "pong")

    def test_terminal_run_can_be_deleted_to_private_trash(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self.load_dashboard(root)
            run_dir = root / "results" / "run_delete_contract"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps({"run_id": "run_delete_contract", "status": "completed", "suite_id": ""}),
                encoding="utf-8",
            )
            with TestClient(dashboard.app) as client:
                deleted = client.delete("/api/runs/run_delete_contract")
                missing = client.get("/api/runs/run_delete_contract")
            self.assertEqual(deleted.status_code, 200)
            self.assertTrue(deleted.json()["recoverable"])
            self.assertEqual(missing.status_code, 404)
            trashed = list((root / "results" / "_trash").glob("run_delete_contract-*"))
            self.assertEqual(len(trashed), 1)
            self.assertTrue((trashed[0] / "summary.json").is_file())

    def test_deleting_one_suite_run_marks_remaining_suite_partial(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self.load_dashboard(root)
            results = root / "results"
            run_id = "run_suite_delete_one"
            suite_id = "suite_delete_contract"
            run_dir = results / run_id
            run_dir.mkdir(parents=True)
            summary = {"run_id": run_id, "status": "completed", "suite_id": suite_id, "model_index": 1}
            (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            suite_dir = results / "_suites"
            suite_dir.mkdir(parents=True, exist_ok=True)
            suite = {
                "suite_id": suite_id, "status": "completed", "completed_models": 2,
                "summaries": [summary, {"run_id": "run_suite_keep", "status": "completed", "model_index": 2}],
                "models": [{"model_index": 1, "status": "completed"}, {"model_index": 2, "status": "completed"}],
            }
            (suite_dir / f"{suite_id}.json").write_text(json.dumps(suite), encoding="utf-8")
            with TestClient(dashboard.app) as client:
                deleted = client.delete(f"/api/runs/{run_id}")
            self.assertEqual(deleted.status_code, 200)
            reconciled = json.loads((suite_dir / f"{suite_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(reconciled["status"], "partial")
            self.assertEqual(reconciled["completed_models"], 1)
            self.assertEqual(reconciled["models"][0]["status"], "deleted")
            self.assertEqual(reconciled["deleted_run_ids"], [run_id])

    def test_active_suite_run_deletion_is_rejected(self) -> None:
        from fastapi.testclient import TestClient
        from cluster.dashboard import services

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self.load_dashboard(root)
            run_dir = root / "results" / "run_active_contract"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps({"run_id": "run_active_contract", "status": "completed", "suite_id": "suite_active"}),
                encoding="utf-8",
            )
            with TestClient(dashboard.app) as client, mock.patch.object(
                services.experiments, "active", return_value={"status": "running", "suite_id": "suite_active"}
            ):
                response = client.delete("/api/runs/run_active_contract")
            self.assertEqual(response.status_code, 409)
            self.assertTrue(run_dir.is_dir())

    def test_startup_recovery_is_service_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dashboard = self.load_dashboard(Path(directory))
            facade = dashboard.app.state.dashboard_services
            with mock.patch.object(facade, "startup", wraps=facade.startup) as startup:
                from fastapi.testclient import TestClient

                with TestClient(dashboard.app):
                    pass
            startup.assert_called_once()

    def test_network_scan_uses_cross_platform_interface_inventory(self) -> None:
        """macOS Controllers do not provide Linux's ``ip`` command."""
        from cluster.dashboard import services

        interfaces = {
            "en0": [
                SimpleNamespace(
                    family=services.socket.AF_INET,
                    address="192.168.0.3",
                    netmask="255.255.255.0",
                )
            ],
            "lo0": [
                SimpleNamespace(
                    family=services.socket.AF_INET,
                    address="127.0.0.1",
                    netmask="255.0.0.0",
                )
            ],
            "docker0": [
                SimpleNamespace(
                    family=services.socket.AF_INET,
                    address="192.168.99.1",
                    netmask="255.255.255.0",
                )
            ],
        }
        stats = {
            "en0": SimpleNamespace(isup=True),
            "lo0": SimpleNamespace(isup=True),
            "docker0": SimpleNamespace(isup=True),
        }
        with mock.patch.object(services.psutil, "net_if_addrs", return_value=interfaces), mock.patch.object(
            services.psutil, "net_if_stats", return_value=stats
        ):
            self.assertEqual(
                services._private_scan_networks(),
                [{"interface": "en0", "local_ip": "192.168.0.3", "network": "192.168.0.0/24"}],
            )

    def test_reverse_hostname_is_best_effort_and_never_requires_ssh(self) -> None:
        from cluster.dashboard import services

        with mock.patch.object(
            services.socket, "gethostbyaddr", return_value=("jetson-orin.local.", [], ["192.168.0.26"])
        ):
            self.assertEqual(services._reverse_hostname("192.168.0.26"), "jetson-orin.local")
        with mock.patch.object(services.socket, "gethostbyaddr", side_effect=services.socket.herror):
            self.assertEqual(services._reverse_hostname("192.168.0.99"), "")

    def test_registered_worker_hostname_uses_existing_key_authentication(self) -> None:
        from cluster.clusterctl import Node
        from cluster.dashboard import services

        node = Node("jetson-worker", "worker", "192.168.0.26", "jetson", 22, 8000, "/opt/cluster", True)
        result = SimpleNamespace(ok=True, stdout="jetson-orin\n")
        with mock.patch.object(services, "run_on_node", return_value=result) as hostname:
            self.assertEqual(services._registered_worker_hostname(node), "jetson-orin")
        hostname.assert_called_once_with(node, ["hostname"], timeout=5)

    def test_dashboard_can_explicitly_create_dedicated_controller_ssh_key(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            identity = Path(directory) / ".ssh" / "id_ed25519_llm_cluster"
            dashboard = self.load_dashboard(Path(directory))
            with mock.patch.object(dashboard.services, "DEFAULT_IDENTITY", identity):
                with TestClient(dashboard.app) as client:
                    response = client.post("/api/onboarding/ssh-key")
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload["key_status"], "ready")
                self.assertTrue(payload["public_key"].startswith("ssh-ed25519 "))
            self.assertTrue(identity.is_file())
            self.assertTrue(identity.with_suffix(".pub").is_file())
            self.assertEqual(stat.S_IMODE(identity.stat().st_mode), 0o600)

    def test_registered_worker_can_be_renamed_and_restarted(self) -> None:
        from fastapi.testclient import TestClient
        from cluster.clusterctl import Node

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self.load_dashboard(root)
            old_name = "worker-wrong-name"
            new_name = "jetson-worker-02"
            dashboard.services.write_all_nodes(
                [
                    Node(
                        old_name,
                        "worker",
                        "192.168.0.26",
                        "jetson",
                        22,
                        8000,
                        "/opt/llm-cluster",
                        True,
                        platform="jetson",
                    )
                ]
            )
            dashboard.services._environment_repository().write(
                old_name,
                {"node": old_name, "status": "ready"},
            )
            queued_action = {"id": "restart-1", "action": "restart", "status": "queued"}
            with mock.patch.object(
                dashboard.services.actions,
                "start",
                return_value=queued_action,
            ) as start_action, mock.patch.object(
                dashboard.services.status_monitor,
                "refresh_now",
                return_value=None,
            ), TestClient(dashboard.app) as client:
                response = client.patch(
                    f"/api/nodes/{old_name}/name",
                    json={"new_name": new_name},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["old_name"], old_name)
            self.assertEqual(payload["node"]["name"], new_name)
            self.assertEqual(payload["node"]["host"], "192.168.0.26")
            self.assertEqual(payload["action"], queued_action)
            saved_nodes = dashboard.services.read_all_nodes()
            self.assertEqual([node.name for node in saved_nodes], [new_name])
            action_payload = start_action.call_args.args[0]
            self.assertEqual(action_payload.action, "restart")
            self.assertEqual(action_payload.node_names, [new_name])
            with self.assertRaises(FileNotFoundError):
                dashboard.services._environment_repository().read(old_name)

    def test_worker_rename_rejects_duplicate_and_invalid_names(self) -> None:
        from fastapi.testclient import TestClient
        from cluster.clusterctl import Node

        with tempfile.TemporaryDirectory() as directory:
            dashboard = self.load_dashboard(Path(directory))
            dashboard.services.write_all_nodes(
                [
                    Node("worker-one", "worker", "192.168.0.26", "jetson", 22, 8000, "/opt/one", True),
                    Node("worker-two", "worker", "192.168.0.27", "jetson", 22, 8000, "/opt/two", True),
                ]
            )
            with TestClient(dashboard.app) as client:
                duplicate = client.patch(
                    "/api/nodes/worker-one/name",
                    json={"new_name": "worker-two"},
                )
                invalid = client.patch(
                    "/api/nodes/worker-one/name",
                    json={"new_name": "worker name with spaces"},
                )

            self.assertEqual(duplicate.status_code, 409)
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(
                [node.name for node in dashboard.services.read_all_nodes()],
                ["worker-one", "worker-two"],
            )

    def test_inventory_accepts_more_than_four_workers(self) -> None:
        from cluster.clusterctl import Node
        from cluster.dashboard.schemas import ExperimentPayload

        with tempfile.TemporaryDirectory() as directory:
            dashboard = self.load_dashboard(Path(directory))
            workers = [
                Node(
                    f"worker-{index}",
                    "worker",
                    f"192.168.0.{30 + index}",
                    "edge",
                    22,
                    8000,
                    f"/home/edge/worker-{index}/llm-cluster",
                    True,
                    platform="jetson" if index % 2 else "raspberry-pi",
                )
                for index in range(1, 9)
            ]
            dashboard.services.write_all_nodes(workers)
            self.assertEqual(len(dashboard.services.read_enabled_nodes()), 8)
            payload = ExperimentPayload(
                node_names=[node.name for node in workers],
                model_id="models/example.gguf",
                rpc_tensor_split=[1.0] * len(workers),
                prompt="unbounded worker selection",
            )
            self.assertEqual(len(payload.node_names), 8)
            self.assertEqual(len(payload.rpc_tensor_split), 8)


if __name__ == "__main__":
    unittest.main()
