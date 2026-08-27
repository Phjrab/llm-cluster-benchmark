"""WS-06 private-LAN download, bind, and RPC cleanup boundaries."""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from cluster import clusterctl
from cluster.infrastructure.remote import CommandResult
from cluster.worker.inference import LlamaCppInferenceBackend


class Response:
    def __init__(self, content: bytes, *, final_url: str, declared: int | None = None) -> None:
        self.content = content
        self.offset = 0
        self.final_url = final_url
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.final_url

    def read(self, count: int) -> bytes:
        value = self.content[self.offset:self.offset + count]
        self.offset += len(value)
        return value


def node() -> clusterctl.Node:
    return clusterctl.Node(
        "worker-01",
        "worker",
        "192.168.10.11",
        "edge",
        22,
        8000,
        "/home/edge/llm-cluster",
        True,
        platform="jetson",
    )


class WorkerDownloadPolicyTests(unittest.TestCase):
    def test_provider_redirect_query_is_allowed_but_caller_query_is_not(self) -> None:
        with self.assertRaisesRegex(ValueError, "without query"):
            LlamaCppInferenceBackend._validate_download_url(
                "https://models.example/model.gguf?token=caller"
            )
        self.assertEqual(
            LlamaCppInferenceBackend._validate_download_url(
                "https://cdn.models.example/model.gguf?signature=provider",
                allow_query=True,
            ),
            "cdn.models.example",
        )

    def test_redirect_target_must_remain_in_optional_allowlist(self) -> None:
        content = b"gguf"
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"CLUSTER_MODEL_DOWNLOAD_ALLOWED_DOMAINS": "models.example"},
            clear=False,
        ), mock.patch(
            "urllib.request.urlopen",
            return_value=Response(content, final_url="https://evil.example/model.gguf"),
        ):
            root = Path(directory)
            backend = LlamaCppInferenceBackend(root)
            with self.assertRaisesRegex(ValueError, "allowlist"):
                backend.install_model(
                    "safe/model.gguf",
                    "https://models.example/model.gguf",
                    hashlib.sha256(content).hexdigest(),
                    expected_size_bytes=len(content),
                )
            self.assertFalse((root / "safe/model.gguf").exists())
            self.assertFalse((root / "safe/model.gguf.part").exists())

    def test_size_overrun_removes_partial_file(self) -> None:
        content = b"five!"
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            os.environ,
            {"CLUSTER_MODEL_DOWNLOAD_ALLOWED_DOMAINS": ""},
            clear=False,
        ), mock.patch(
            "urllib.request.urlopen",
            return_value=Response(
                content,
                final_url="https://models.example/model.gguf",
            ),
        ):
            root = Path(directory)
            backend = LlamaCppInferenceBackend(root)
            with self.assertRaisesRegex(ValueError, "byte limit"):
                backend.install_model(
                    "safe/model.gguf",
                    "https://models.example/model.gguf",
                    hashlib.sha256(content).hexdigest(),
                    expected_size_bytes=4,
                )
            self.assertFalse((root / "safe/model.gguf.part").exists())

    def test_insufficient_disk_fails_before_network_and_leaves_no_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch(
            "cluster.worker.inference.shutil.disk_usage",
            return_value=SimpleNamespace(free=1),
        ), mock.patch("urllib.request.urlopen") as download:
            root = Path(directory)
            backend = LlamaCppInferenceBackend(root)
            with self.assertRaisesRegex(OSError, "storage is insufficient"):
                backend.install_model(
                    "safe/model.gguf",
                    "https://models.example/model.gguf",
                    "a" * 64,
                    expected_size_bytes=4,
                )
            download.assert_not_called()
            self.assertFalse((root / "safe/model.gguf.part").exists())


class LanBindAndCleanupCommandTests(unittest.TestCase):
    def test_catalog_size_is_sent_to_worker_install_boundary(self) -> None:
        with mock.patch.object(
            clusterctl,
            "request_json",
            return_value={
                "ok": True,
                "model": {
                    "size_bytes": 4,
                    "checksum_valid": True,
                    "downloaded_bytes": 4,
                },
            },
        ) as request:
            result = clusterctl.install_model_url_one(
                node(),
                "safe/model.gguf",
                "https://models.example/model.gguf",
                "a" * 64,
                expected_size_bytes=4,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(request.call_args.kwargs["payload"]["expected_size_bytes"], 4)

    def test_worker_lifecycle_binds_inventory_lan_address(self) -> None:
        with mock.patch.object(
            clusterctl, "run_on_node", return_value=CommandResult(0, "ok", "")
        ) as run, mock.patch.object(clusterctl, "worker_auth_enabled", return_value=False):
            result = clusterctl._lifecycle_one(node(), "start")
        self.assertTrue(result["ok"])
        self.assertIn("HOST=192.168.10.11", run.call_args.args[1])
        self.assertNotIn("HOST=0.0.0.0", run.call_args.args[1])

    def test_rpc_cleanup_check_uses_only_fixed_actions_and_ports(self) -> None:
        calls: list[tuple[str, tuple[str, ...]]] = []

        def runtime(_node, action, _timeout, *arguments):
            calls.append((action, arguments))
            return {"name": "worker-01", "ok": True, "stdout": "stopped", "stderr": ""}

        with mock.patch.object(clusterctl, "_rpc_runtime_command", side_effect=runtime):
            self.assertEqual(
                clusterctl.command_rpc_cleanup_check(
                    [node()], SimpleNamespace()
                ),
                0,
            )
        self.assertEqual(
            calls,
            [
                ("assert-stopped-worker", ("50052",)),
                ("assert-stopped-coordinator", ("18080",)),
            ],
        )


if __name__ == "__main__":
    unittest.main()
