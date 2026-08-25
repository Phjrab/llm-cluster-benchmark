from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster import clusterctl
from cluster.infrastructure import huggingface_access
from cluster.integrations.legacy_inventory_runtime import Node


class HuggingFaceAccessTests(unittest.TestCase):
    def test_status_reports_account_without_returning_token(self) -> None:
        token = "hf_secret_value_that_must_not_escape"

        def whoami(*, token: str) -> dict[str, str]:
            self.assertEqual(token, "hf_secret_value_that_must_not_escape")
            return {"name": "researcher"}

        with mock.patch.object(
            huggingface_access,
            "_hub_api",
            return_value=(lambda: token, mock.Mock(), whoami),
        ):
            status = huggingface_access.huggingface_access_status(verify=True)
        self.assertEqual(status["account"], "researcher")
        self.assertTrue(status["verified"])
        self.assertNotIn(token, json.dumps(status))
        self.assertNotIn("token", status)

    def test_verified_download_uses_exact_identity_and_private_target(self) -> None:
        payload = b"mock gguf bytes only"
        digest = hashlib.sha256(payload).hexdigest()
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downloaded = root / "hub-cache-file.gguf"
            downloaded.write_bytes(payload)

            def hf_hub_download(**kwargs: object) -> str:
                calls.append(dict(kwargs))
                return str(downloaded)

            with mock.patch.object(
                huggingface_access,
                "_hub_api",
                return_value=(lambda: "hf_private", hf_hub_download, lambda **_: {"name": "tester"}),
            ):
                result = huggingface_access.download_verified_model_to_controller_cache(
                    project_root=root,
                    runtime_dir=root / ".run" / "cluster",
                    model_id="family/model-Q4_K_M.gguf",
                    repo_id="owner/model-GGUF",
                    revision="a" * 40,
                    filename="model-Q4_K_M.gguf",
                    expected_sha256=digest,
                    expected_size_bytes=len(payload),
                )
            target = Path(result["path"])
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((root / ".run" / "cluster" / "huggingface-cache").stat().st_mode), 0o700)
            self.assertEqual(calls[0]["repo_id"], "owner/model-GGUF")
            self.assertEqual(calls[0]["revision"], "a" * 40)
            self.assertEqual(calls[0]["filename"], "model-Q4_K_M.gguf")
            self.assertEqual(calls[0]["token"], "hf_private")
            self.assertNotIn("token", result)

    def test_checksum_mismatch_never_publishes_controller_model(self) -> None:
        payload = b"wrong bytes"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            downloaded = root / "download.gguf"
            downloaded.write_bytes(payload)
            with mock.patch.object(
                huggingface_access,
                "_hub_api",
                return_value=(lambda: "hf_private", lambda **_: str(downloaded), lambda **_: {"name": "tester"}),
            ):
                with self.assertRaises(huggingface_access.HuggingFaceAccessError):
                    huggingface_access.download_verified_model_to_controller_cache(
                        project_root=root,
                        runtime_dir=root / ".run" / "cluster",
                        model_id="family/model.gguf",
                        repo_id="owner/model-GGUF",
                        revision="b" * 40,
                        filename="model.gguf",
                        expected_sha256="0" * 64,
                        expected_size_bytes=len(payload),
                    )
            self.assertFalse((root / "models" / "family" / "model.gguf").exists())


class AuthenticatedCacheCommandTests(unittest.TestCase):
    def test_controller_cache_download_syncs_workers_without_token_argv_or_output(self) -> None:
        node = Node(
            "pi-worker-2", "worker", "192.168.0.16", "pi1", 22, 8000,
            "/home/pi1/llm-cluster-benchmark", True, platform="raspberry-pi",
        )
        args = argparse.Namespace(
            confirmed=True,
            license_accepted=True,
            model_id="family/model.gguf",
            source_repo="owner/model-GGUF",
            source_revision="a" * 40,
            source_filename="model-Q4_K_M.gguf",
            expected_sha256="b" * 64,
            expected_size_bytes=123,
            provenance_status="official",
            architecture="llama",
            metadata_contract="gguf-metadata-v1",
        )
        output = io.StringIO()
        with mock.patch.object(
            clusterctl,
            "download_verified_model_to_controller_cache",
            return_value={"path": "/private/cache/model.gguf", "size_bytes": 123, "sha256": "b" * 64},
        ) as download, mock.patch.object(
            clusterctl,
            "sync_models_one",
            return_value={"name": node.name, "ok": True, "stdout": "", "stderr": ""},
        ) as sync, contextlib.redirect_stdout(output):
            result = clusterctl.command_install_model_cache([node], args)
        self.assertEqual(result, 0)
        self.assertEqual(download.call_args.kwargs["repo_id"], "owner/model-GGUF")
        self.assertEqual(sync.call_args.kwargs["model_metadata"][args.model_id]["source_revision"], "a" * 40)
        self.assertNotIn("hf_", output.getvalue())
        self.assertNotIn("token", output.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
