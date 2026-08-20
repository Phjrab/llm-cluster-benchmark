from __future__ import annotations

import unittest
from unittest import mock

from cluster import clusterctl
from cluster.infrastructure.platform import HostPlatform, WorkerPlatform, controller_capabilities, worker_capabilities
from cluster.infrastructure.process import ProcessIdentity, can_signal
from cluster.infrastructure.remote import build_ssh_command
from cluster.infrastructure.sse import parse_sse_events
from cluster.infrastructure.worker_client import WorkerClient


class InfrastructureTests(unittest.TestCase):
    def test_macos_controller_never_selects_linux_worker_setup(self) -> None:
        capabilities = controller_capabilities("Darwin")
        self.assertIs(capabilities.host, HostPlatform.MACOS)
        self.assertFalse(capabilities.allows_linux_worker_setup)
        self.assertEqual(capabilities.allowed_package_managers, ())
        self.assertFalse(capabilities.supports_jtop)

    def test_linux_worker_capabilities_are_platform_scoped(self) -> None:
        jetson = worker_capabilities("Linux", "jetson")
        pi = worker_capabilities("Linux", "raspberry-pi")
        unknown = worker_capabilities("Linux", "generic")
        self.assertTrue(jetson.allows_linux_worker_setup)
        self.assertTrue(jetson.supports_jtop)
        self.assertTrue(pi.allows_linux_worker_setup)
        self.assertFalse(pi.supports_jtop)
        self.assertIs(unknown.worker, WorkerPlatform.UNSUPPORTED)
        self.assertFalse(unknown.allows_linux_worker_setup)

    def test_worker_token_header_is_opt_in(self) -> None:
        self.assertNotIn("X-Cluster-Worker-Token", WorkerClient("http://worker:8000").headers())
        self.assertEqual(
            WorkerClient("http://worker:8000/", "secret").headers()["X-Cluster-Worker-Token"],
            "secret",
        )

    def test_sse_parser_handles_tokens_done_errors_and_noise(self) -> None:
        events = list(parse_sse_events([
            b": keepalive\n", b"\n", b"event: ignored\n", b'data: {"type":"token","text":"A"}\n',
            b'data: {"type":"done","metrics":{"generated_tokens":1}}\n', b"data: {broken}\n",
            b'data: {"type":"error","message":"bad"}\n',
        ]))
        self.assertEqual([event["type"] for event in events], ["token", "done", "malformed", "error"])
        self.assertEqual(events[0]["text"], "A")

    def test_ssh_command_keeps_batch_mode_and_separate_quoted_arguments(self) -> None:
        class Target:
            host = "192.168.0.26"
            ssh_port = 22
            ssh_target = "edge@192.168.0.26"
            is_local = False
        command = build_ssh_command(Target())
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-1], "edge@192.168.0.26")

    def test_identity_mismatch_refuses_signal(self) -> None:
        expected = ProcessIdentity(42, "/usr/bin/python3", "/repo", ("python", "-m", "app"), 10.0, "user")
        self.assertTrue(can_signal(expected, expected))
        self.assertFalse(can_signal(expected, ProcessIdentity(42, "/usr/bin/python3", "/other", ("python", "-m", "app"), 10.0, "user")))

    def test_macos_refuses_local_linux_package_bootstrap(self) -> None:
        node = clusterctl.Node("legacy-head", "head", "127.0.0.1", "user", 22, 8000, "/home/user/project", True)
        with mock.patch.object(clusterctl.platform, "system", return_value="Darwin"):
            result = clusterctl.bootstrap_system_one(node)
        self.assertFalse(result["ok"])
        self.assertIn("macOS Controller", result["stderr"])


if __name__ == "__main__":
    unittest.main()
