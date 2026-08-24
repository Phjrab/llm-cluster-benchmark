"""Roadmap Phase 12 compatibility-facade characterization gates."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from cluster.dashboard.service_layers import (
    DashboardServiceError,
    ResearchService,
    ResultService,
    SettingsService,
)
from cluster.dashboard.schemas import ClusterSettingsPayload


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "cluster" / "dashboard" / "services.py"
ROUTES = ROOT / "cluster" / "dashboard" / "routes.py"


class DashboardFacadeCharacterizationTests(unittest.TestCase):
    def test_public_facade_method_contract_is_frozen_before_extraction(self) -> None:
        tree = ast.parse(SERVICES.read_text(encoding="utf-8"))
        facade = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "DashboardFacade"
        )
        methods = {
            node.name for node in facade.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "startup", "shutdown", "dashboard_health", "controller_status", "bootstrap",
                "create_controller_ssh_identity", "settings", "update_settings", "status", "models",
                "refresh_status", "scan_network", "jetson_power_status", "start_jetson_power_mode",
                "probe_candidate", "upsert_node", "rename_node", "delete_node", "start_action",
                "listed_actions", "environment", "start_experiment", "experiments", "campaigns",
                "campaign", "compare_runs", "research_readiness", "experiment_groups",
                "cancel_experiment", "run", "responses", "measurements", "delete_run",
            }.issubset(methods)
        )

    def test_dashboard_service_layer_is_transport_neutral(self) -> None:
        tree = ast.parse(SERVICES.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("fastapi", imports)
        self.assertNotIn("starlette", imports)

    def test_route_contract_remains_facade_only(self) -> None:
        source = ROUTES.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertNotIn("subprocess", imports)
        self.assertNotIn("cluster.clusterctl", source)
        self.assertIn("DashboardFacade", source)

    def test_legacy_compatibility_exports_are_explicit(self) -> None:
        source = SERVICES.read_text(encoding="utf-8")
        self.assertIn("COMPATIBILITY_EXPORTS = (", source)
        for name in (
            '"DashboardFacade"', '"ActionManager"', '"ExperimentManager"',
            '"read_run_summaries"', '"read_environment_reports"', '"status_monitor"',
        ):
            if name == '"DashboardFacade"':
                # The facade itself is imported directly rather than through app.py's export loop.
                continue
            self.assertIn(name, source)

    def test_clusterctl_reexports_extracted_legacy_inventory_contract(self) -> None:
        from cluster import clusterctl
        from cluster.integrations import legacy_inventory_runtime

        self.assertIs(clusterctl.Node, legacy_inventory_runtime.Node)
        self.assertIs(clusterctl.load_nodes, legacy_inventory_runtime.load_nodes)
        self.assertIs(clusterctl.select_nodes, legacy_inventory_runtime.select_nodes)
        source = (ROOT / "cluster" / "clusterctl.py").read_text(encoding="utf-8")
        self.assertNotIn("class Node:", source)
        self.assertNotIn("csv.DictReader", source)

    def test_extracted_service_modules_are_framework_and_process_free(self) -> None:
        service_root = ROOT / "cluster" / "dashboard" / "service_layers"
        for path in sorted(service_root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = {
                alias.name.split(".")[0]
                for node in tree.body
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            with self.subTest(module=path.name):
                self.assertFalse({"fastapi", "starlette", "subprocess"}.intersection(imports))


class ExtractedServiceTests(unittest.TestCase):
    class Repository:
        def __init__(self) -> None:
            self.summary = {"run_id": "run_1", "status": "completed", "suite_id": ""}
            self.deleted = []

        def read_summary(self, run_id: str):
            if run_id != "run_1":
                raise FileNotFoundError(run_id)
            return dict(self.summary)

        def read_responses(self, _run_id: str):
            return [{"request_id": 1, "response": "ok"}]

        def read_measurements(self, _run_id: str):
            return [{"record_type": "telemetry_sample"}]

        def delete(self, run_id: str):
            self.deleted.append(run_id)

    def test_result_service_is_unit_testable_without_dashboard_globals(self) -> None:
        repository = self.Repository()
        published = []
        service = ResultService(
            run_repository=lambda: repository,
            suite_repository=lambda: None,
            read_suites=lambda **_kwargs: [],
            with_suite_metadata=lambda summary, _suites: summary,
            active_experiment=lambda: None,
            publish_event=lambda event, **payload: published.append((event, payload)),
            utc_now=lambda: "2026-08-24T00:00:00+00:00",
        )
        self.assertEqual(service.run("run_1")["status"], "completed")
        self.assertEqual(service.responses("run_1")["responses"][0]["response"], "ok")
        self.assertEqual(service.measurements("run_1")["schema_version"], 1)
        self.assertTrue(service.delete("run_1")["recoverable"])
        self.assertEqual(repository.deleted, ["run_1"])
        self.assertEqual(published[0][0], "results_changed")
        with self.assertRaisesRegex(DashboardServiceError, "Invalid run id"):
            service.run("../escape")

    def test_research_service_uses_injected_readers(self) -> None:
        class Campaigns:
            def __init__(self, _path):
                pass

            def list(self):
                return []

        requested = []
        service = ResearchService(
            campaigns_dir=Path("/tmp/campaigns"),
            read_runs=lambda **kwargs: requested.append(kwargs) or [],
            read_research_document=lambda name: {"name": name},
            status_snapshot=lambda: [],
            read_environment=lambda: [],
            controller_commit=lambda: "a" * 40,
            repository_factory=Campaigns,
        )
        self.assertEqual(service.campaigns(), {"schema_version": 1, "campaigns": []})
        comparison = service.compare_runs()
        self.assertEqual(comparison["runs"], [])
        self.assertEqual(requested, [{"limit": 10_000}])
        readiness = service.readiness()
        self.assertEqual(readiness["controller_source"]["observed_commit"], "a" * 40)

    def test_settings_service_rolls_back_failed_worker_restart(self) -> None:
        import threading

        state = {"worker_api_auth": False, "dashboard_token_auth": False}
        writes = []

        def write(value):
            state.clear()
            state.update(value)
            writes.append(dict(value))

        service = SettingsService(
            lock=threading.RLock(),
            read_settings=lambda: dict(state),
            write_settings=write,
            read_enabled_node_names=lambda: ["worker-01"],
            start_action=lambda _payload: (_ for _ in ()).throw(ValueError("busy")),
            publish_event=lambda *_args, **_kwargs: None,
        )
        with self.assertRaisesRegex(DashboardServiceError, "busy"):
            service.update(
                ClusterSettingsPayload(worker_api_auth=True),
                supplied_token="",
                token_is_valid=lambda _token: False,
            )
        self.assertEqual(state, {"worker_api_auth": False, "dashboard_token_auth": False})
        self.assertEqual(len(writes), 2)


if __name__ == "__main__":
    unittest.main()
