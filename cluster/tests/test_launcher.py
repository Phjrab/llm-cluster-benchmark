from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import unittest
from pathlib import Path

from cluster.cli import controller


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "llm-cluster"
SETUP_CONTROLLER = ROOT / "scripts" / "setup-controller"
SETUP_WORKER = ROOT / "scripts" / "setup-worker"
LEGACY_START = Path(__file__).resolve().parents[1] / "dashboard" / "start.sh"
LEGACY_STOP = Path(__file__).resolve().parents[1] / "dashboard" / "stop.sh"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class ControllerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.port = free_port()
        self.spec = controller.ProcessSpec(
            module="cluster.dashboard.app",
            host="127.0.0.1",
            port=self.port,
            health_path="/dashboard/health",
            pid_file=self.root / "controller" / "dashboard.pid",
            identity_file=self.root / "controller" / "dashboard.identity.json",
            log_file=self.root / "controller" / "dashboard.log",
            python_bin=Path(sys.executable),
            project_root=ROOT,
        )
        self.previous_env = {
            key: os.environ.get(key)
            for key in ("CLUSTER_RUNTIME_DIR", "CLUSTER_RESULTS_DIR", "CLUSTER_INVENTORY")
        }
        os.environ["CLUSTER_RUNTIME_DIR"] = str(self.root / "cluster-runtime")
        os.environ["CLUSTER_RESULTS_DIR"] = str(self.root / "results")
        os.environ["CLUSTER_INVENTORY"] = str(self.root / "cluster-runtime" / "nodes.local.csv")

    def tearDown(self) -> None:
        try:
            controller._stop(self.spec)
        except controller.LauncherError:
            pass
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def test_start_is_idempotent_status_logs_restart_and_stop(self) -> None:
        first, created = controller._start(self.spec)
        self.assertTrue(created)
        second, created_again = controller._start(self.spec)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        self.assertEqual(controller._show_status(self.spec), 0)
        self.assertIsInstance(tuple(controller._tail(self.spec.log_file, 200)), tuple)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/controller/status", timeout=2.0
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["role"], "controller")
        self.assertFalse(payload["inference_enabled"])

        stopped = controller._stop(self.spec)
        self.assertEqual(stopped, first)
        third, created_after_restart = controller._start(self.spec)
        self.assertTrue(created_after_restart)
        self.assertNotEqual((first.pid, first.started_at), (third.pid, third.started_at))
        controller._stop(self.spec)
        self.assertEqual(controller._show_status(self.spec), 3)

    def test_tampered_pid_record_never_signals_an_unrelated_process(self) -> None:
        foreign = subprocess.Popen(["sleep", "30"])
        try:
            self.spec.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.spec.pid_file.write_text(f"{foreign.pid}\n", encoding="utf-8")
            self.spec.identity_file.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "pid": foreign.pid,
                        "executable": "/not/this/python",
                        "cwd": "/not/this/project",
                        "argv": ["not-dashboard"],
                        "started_at": time.time(),
                        "user": "nobody",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(controller.LauncherError):
                controller._stop(self.spec)
            self.assertIsNone(foreign.poll())
        finally:
            foreign.terminate()
            foreign.wait(timeout=5)


class ControllerSetupAndCompatibilityTests(unittest.TestCase):
    def test_action_allowlist_rejects_missing_extra_and_injected_arguments(self) -> None:
        for arguments in ([], ["start", "extra"], ["start;id"], ["--help"]):
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), *arguments],
                cwd="/tmp",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 64, arguments)
            self.assertIn("start|stop|restart|status|logs", completed.stderr)

    def test_setup_is_controller_only_and_worker_setup_is_explicitly_linux_only(self) -> None:
        controller_setup = SETUP_CONTROLLER.read_text(encoding="utf-8")
        worker_setup = SETUP_WORKER.read_text(encoding="utf-8")
        self.assertIn("requirements-controller.txt", controller_setup)
        self.assertNotIn("apt-get", controller_setup)
        self.assertNotIn("worker_setup.sh", controller_setup)
        self.assertIn("worker_setup.sh", worker_setup)
        self.assertIn("Linux workers only", worker_setup)

    def test_legacy_dashboard_scripts_delegate_to_the_single_manager(self) -> None:
        start_text = LEGACY_START.read_text(encoding="utf-8")
        stop_text = LEGACY_STOP.read_text(encoding="utf-8")
        self.assertIn('exec "$PROJECT_ROOT/scripts/llm-cluster" start', start_text)
        self.assertIn('exec "$PROJECT_ROOT/scripts/llm-cluster" stop', stop_text)
        for text in (start_text, stop_text):
            self.assertNotIn("kill ", text)
            self.assertNotIn("nohup ", text)

    def test_old_clusterctl_surface_is_unchanged(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "cluster.clusterctl", "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("environment-install", completed.stdout)


if __name__ == "__main__":
    unittest.main()
