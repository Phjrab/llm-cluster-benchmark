"""Roadmap Phase 12 compatibility-facade characterization gates."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
