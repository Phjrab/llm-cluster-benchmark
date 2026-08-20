from __future__ import annotations

import unittest

from cluster.infrastructure.platform import HostPlatform, WorkerPlatform, controller_capabilities, worker_capabilities
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


if __name__ == "__main__":
    unittest.main()
