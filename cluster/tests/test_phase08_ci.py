from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cluster.clusterctl import Node
from scripts.ci.hardware_smoke import UNAVAILABLE_EXIT, build_plan, main as hardware_main
from scripts.ci.validate_repository import (
    validate_json_documents,
    validate_research_contracts,
    validate_shell_safety,
    validate_workflows,
)


ROOT = Path(__file__).resolve().parents[2]


def worker(name: str, platform: str, host: str) -> Node:
    return Node(
        name=name,
        role="worker",
        host=host,
        user="tester",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/tester/{name}",
        enabled=True,
        platform=platform,
    )


class HostedWorkflowTests(unittest.TestCase):
    def test_repository_validator_covers_json_research_workflows_and_shells(self) -> None:
        self.assertGreater(validate_json_documents(), 0)
        self.assertGreater(validate_research_contracts(), 0)
        self.assertGreaterEqual(validate_workflows(), 6)
        self.assertGreater(validate_shell_safety(), 0)

    def test_required_check_names_and_failure_artifacts_are_stable(self) -> None:
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for name in ("Linux quality gate", "macOS controller gate", "Browser E2E"):
            self.assertIn(f"name: {name}", text)
        self.assertIn("pull_request:", text)
        self.assertIn('"codex/**"', text)
        self.assertGreaterEqual(text.count("if: always()"), 3)
        self.assertIn("shellcheck", text)
        self.assertIn("test_packaging", text)
        self.assertIn("macos_controller_gate.py", text)
        self.assertIn("npm test", text)
        self.assertIn('"workstream/**"', text)
        self.assertIn(".artifacts/playwright", text)
        self.assertNotIn("\n            playwright-report", text)
        for filename in (
            "wheel-build.log",
            "wheel-install.log",
            "shell-validation.log",
            "macos-compatibility.log",
        ):
            self.assertIn(filename, text)

    def test_node_scripts_pin_playwright_and_run_all_browser_layers(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["devDependencies"]["@playwright/test"], "1.62.1")
        self.assertIn("test:syntax", package["scripts"])
        self.assertIn("test:fixtures", package["scripts"])
        self.assertIn("test:e2e", package["scripts"])
        self.assertIn("playwright test", package["scripts"]["test:e2e"])


class HardwareWorkflowTests(unittest.TestCase):
    def test_hardware_plan_is_homogeneous_and_covers_all_required_smokes(self) -> None:
        nodes = [
            worker("jetson-1", "jetson", "192.168.0.21"),
            worker("jetson-2", "jetson", "192.168.0.22"),
            worker("pi-1", "raspberry-pi", "192.168.0.31"),
        ]
        plan = build_plan(nodes, "models/smoke.gguf")
        self.assertEqual([item.case_id for item in plan], ["jetson-single", "pi-single", "replicated-two-node", "rpc-cleanup"])
        replicated = plan[2].config
        rpc = plan[3].config
        self.assertEqual(replicated.node_names, ["jetson-1", "jetson-2"])
        self.assertEqual(rpc.node_names, ["jetson-1", "jetson-2"])
        self.assertEqual(rpc.rpc_coordinator_node, "jetson-1")
        self.assertTrue(rpc.acknowledge_experimental_rpc)
        self.assertEqual(plan[1].config.n_gpu_layers, 0)

    def test_hardware_unavailable_is_explicit_skip_with_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            code = hardware_main([
                "--inventory", str(root / "missing.csv"),
                "--model-id", "models/smoke.gguf",
                "--results-dir", str(root / "results"),
                "--report", str(report),
                "--mode", "nightly",
            ])
            self.assertEqual(code, UNAVAILABLE_EXIT)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["mode"], "nightly")

    def test_hardware_workflow_is_opt_in_and_release_candidate_blocking(self) -> None:
        text = (ROOT / ".github" / "workflows" / "hardware.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("schedule:", text)
        self.assertIn('"v*-rc*"', text)
        self.assertIn("runs-on: [self-hosted, llm-cluster-hardware]", text)
        self.assertIn("Release candidate hardware gate", text)
        self.assertIn('MODE" != "release-candidate', text)
        self.assertIn("OUTCOME\" != \"passed", text)
        self.assertIn("if: always()", text)


if __name__ == "__main__":
    unittest.main()
