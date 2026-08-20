"""Direct Worker setup CLI path-safety regression tests."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "cluster" / "worker_setup.sh"


class WorkerSetupPathSafetyTests(unittest.TestCase):
    def run_plan(self, project_dir: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(SCRIPT),
                "--plan-only",
                "--platform",
                "jetson",
                "--project-dir",
                project_dir,
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_dedicated_project_paths_remain_valid(self) -> None:
        for project_dir in (
            "/home/edge/llm-cluster-benchmark",
            "/opt/llm-cluster-benchmark",
            "/srv/edge/llm-cluster-benchmark",
        ):
            with self.subTest(project_dir=project_dir):
                result = self.run_plan(project_dir)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_broad_ambiguous_or_unsupported_paths_fail_before_install(self) -> None:
        invalid = (
            "/",
            "/home",
            "/home/edge",
            "/opt",
            "/srv",
            "/tmp/llm-cluster-benchmark",
            "/home/edge/../root/project",
            "/home/edge//project",
            "/home/edge/./project",
            "relative/project",
        )
        for project_dir in invalid:
            with self.subTest(project_dir=project_dir):
                result = self.run_plan(project_dir)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("project directory", result.stderr)

    def test_jetson_telemetry_client_tracks_system_service_version(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('system_jtop_version=', source)
        self.assertIn('version("jetson-stats")', source)
        self.assertIn('pip install "jetson-stats==$system_jtop_version"', source)
        self.assertIn('^[0-9]+([.][0-9]+){1,3}$', source)
        self.assertNotIn("jetson-stats==4.3.2", source)


if __name__ == "__main__":
    unittest.main()
