"""Transport recovery must not replay successful stages or retry hard failures."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cluster import clusterctl


class EnvironmentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.node = SimpleNamespace(name="worker")

    @patch.object(clusterctl.time, "sleep")
    def test_transient_failure_recovers_with_backoff(self, sleep):
        operation = Mock(side_effect=[
            {"ok": False, "stderr": "server not responding"}, {"ok": True}])
        self.assertTrue(clusterctl._environment_stage(self.node, "sync", operation)["ok"])
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(5)

    @patch.object(clusterctl.time, "sleep")
    def test_timeout_is_bounded_and_preserves_stage(self, sleep):
        operation = Mock(side_effect=subprocess.TimeoutExpired("ssh", 20))
        result = clusterctl._environment_stage(self.node, "RPC", operation)
        self.assertFalse(result["ok"])
        self.assertIn("RPC (attempt 3/3)", result["stderr"])
        self.assertEqual(operation.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10])

    @patch.object(clusterctl.time, "sleep")
    def test_hard_failures_do_not_retry(self, sleep):
        for detail in ("Permission denied", "Host key verification failed",
                       "No space left on device", "nvcc compilation failed",
                       "Connection closed: Permission denied"):
            with self.subTest(detail=detail):
                operation = Mock(return_value={"ok": False, "stderr": detail})
                self.assertFalse(clusterctl._environment_stage(self.node, "setup", operation)["ok"])
                operation.assert_called_once()
        sleep.assert_not_called()

    @patch.object(clusterctl.time, "sleep")
    def test_success_never_repeats(self, sleep):
        operation = Mock(return_value={"ok": True})
        self.assertTrue(clusterctl._environment_stage(self.node, "setup", operation)["ok"])
        operation.assert_called_once()
        sleep.assert_not_called()

    @patch.object(clusterctl.time, "sleep")
    def test_install_retries_setup_without_replaying_sync(self, sleep):
        node = SimpleNamespace(name="worker", role="worker")
        ready = {"status": "ready"}
        with patch.object(clusterctl, "discover_node", return_value={"ssh": True}), \
             patch.object(clusterctl, "bootstrap_system_one", return_value={"ok": True}) as bootstrap, \
             patch.object(clusterctl, "sync_code_one", return_value={"ok": True}) as sync, \
             patch.object(clusterctl, "_setup_one", side_effect=[
                 {"ok": False, "stderr": "Connection reset by peer"}, {"ok": True}]) as setup, \
             patch.object(clusterctl, "_ensure_rpc_runtime_one", return_value={"ok": True}) as rpc, \
             patch.object(clusterctl, "_lifecycle_one", return_value={"ok": True}), \
             patch.object(clusterctl, "check_environment_one", return_value=ready) as check:
            self.assertEqual(clusterctl.install_environment_one(node), ready)
        bootstrap.assert_called_once()
        sync.assert_called_once()
        self.assertEqual(setup.call_count, 2)
        rpc.assert_called_once()
        check.assert_called_once()
