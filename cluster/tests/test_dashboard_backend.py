"""Phase 12 Dashboard backend wiring and contract regression tests."""

from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
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
            self.assertEqual(controller.status_code, 200)
            self.assertEqual(controller.json()["role"], "controller")
            self.assertFalse(controller.json()["inference_enabled"])
            self.assertEqual(health.status_code, 200)
            self.assertFalse(health.json()["inference_enabled"])
            self.assertEqual(models.status_code, 200)
            self.assertEqual(set(models.json()), {"models", "inventories", "catalog", "recommendations"})

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

    def test_startup_recovery_is_service_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dashboard = self.load_dashboard(Path(directory))
            facade = dashboard.app.state.dashboard_services
            with mock.patch.object(facade, "startup", wraps=facade.startup) as startup:
                from fastapi.testclient import TestClient

                with TestClient(dashboard.app):
                    pass
            startup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
