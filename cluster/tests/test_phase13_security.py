"""Roadmap Phase 13 SSH pinning and secure-mode regression gates."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cluster import clusterctl
from cluster.infrastructure.remote import build_ssh_command
from cluster.infrastructure.ssh_host_keys import (
    list_pinned_host_keys,
    pin_host_key,
    scan_host_keys,
)


KEY = "AAAAC3NzaC1lZDI1NTE5AAAAITestHostKeyMaterialOnly"
FINGERPRINT = "SHA256:AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/ab"


class FakeRunner:
    def __init__(self, *, host: str = "192.168.0.26", port: int = 22) -> None:
        self.endpoint = host if port == 22 else f"[{host}]:{port}"
        self.calls: list[list[str]] = []

    def __call__(self, command, **_kwargs):
        self.calls.append(list(command))
        if command[0] == "ssh-keyscan":
            return SimpleNamespace(
                returncode=0,
                stdout=f"# scan\n{self.endpoint} ssh-ed25519 {KEY}\n",
                stderr="",
            )
        if command[0] == "ssh-keygen":
            return SimpleNamespace(
                returncode=0,
                stdout=f"256 {FINGERPRINT} fixture (ED25519)\n",
                stderr="",
            )
        raise AssertionError(command)


class SshHostKeyPinningTests(unittest.TestCase):
    def test_scan_requires_exact_endpoint_and_sha256_fingerprint(self) -> None:
        runner = FakeRunner()
        values = scan_host_keys("192.168.0.26", 22, runner=runner)
        self.assertEqual(values[0]["fingerprint"], FINGERPRINT)
        self.assertEqual(values[0]["key_type"], "ssh-ed25519")
        self.assertEqual(values[0]["endpoint"], "192.168.0.26")

    def test_pin_is_private_replaces_endpoint_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ssh_known_hosts"
            runner = FakeRunner()
            pinned = pin_host_key(
                path,
                "192.168.0.26",
                22,
                FINGERPRINT,
                runner=runner,
            )
            self.assertEqual(pinned["fingerprint"], FINGERPRINT)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(list_pinned_host_keys(path, runner=runner)), 1)
            original = path.read_text(encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match"):
                pin_host_key(
                    path,
                    "192.168.0.26",
                    22,
                    "SHA256:0000000000000000000000000000000000000000",
                    runner=runner,
                )
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_pinned_ssh_command_ignores_global_known_hosts(self) -> None:
        class Target:
            host = "192.168.0.26"
            ssh_port = 22
            ssh_target = "edge@192.168.0.26"
            is_local = False

        managed = Path("/private/runtime/ssh_known_hosts")
        command = build_ssh_command(
            Target(),
            known_hosts_file=managed,
            require_pinned=True,
        )
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn(f"UserKnownHostsFile={managed}", command)
        self.assertIn("GlobalKnownHostsFile=/dev/null", command)
        self.assertNotIn("StrictHostKeyChecking=accept-new", command)

    def test_runtime_setting_selects_compatible_or_pinned_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            settings = runtime / "settings.json"
            with mock.patch.dict(os.environ, {"CLUSTER_RUNTIME_DIR": str(runtime)}, clear=False):
                self.assertEqual(clusterctl.ssh_host_key_policy(), "trusted_lan")
                settings.write_text(json.dumps({"ssh_host_key_policy": "pinned"}), encoding="utf-8")
                self.assertEqual(clusterctl.ssh_host_key_policy(), "pinned")
                settings.write_text("{broken", encoding="utf-8")
                self.assertEqual(clusterctl.ssh_host_key_policy(), "pinned")


if __name__ == "__main__":
    unittest.main()
