"""R05 ordered multipart GGUF identity, installation, and planner tests."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from cluster.application.model_service import (
    WorkerModelInventory,
    build_artifact_set_install_spec,
    validate_model_preflight,
)
from cluster.application.sweep_models import preview_catalog_sweep
from cluster.dashboard.schemas import ModelInstallPayload
from cluster.domain.errors import DomainValidationError
from cluster.domain.model import (
    ModelArtifact,
    ModelCatalogEntry,
    ModelInventoryEntry,
    artifact_set_manifest_sha256,
)
from cluster.integrations.legacy_inventory_runtime import Node
from cluster.tests.test_gguf_identity import write_gguf
from cluster.tests.test_sweep_models import fixtures
from cluster.worker.inference import LlamaCppInferenceBackend


def artifact_entry(first: bytes, second: bytes, **overrides: object) -> ModelCatalogEntry:
    artifacts = (
        ModelArtifact("model-Q4_K_M-00001-of-00002.gguf", len(first), hashlib.sha256(first).hexdigest()),
        ModelArtifact("model-Q4_K_M-00002-of-00002.gguf", len(second), hashlib.sha256(second).hexdigest()),
    )
    values: dict[str, object] = {
        "id": "family/model-Q4_K_M-00001-of-00002.gguf",
        "gguf_repo": "owner/model-GGUF",
        "gguf_revision": "a" * 40,
        "gguf_filename": artifacts[0].filename,
        "size_bytes": sum(item.size_bytes for item in artifacts),
        "artifact_set_sha256": artifact_set_manifest_sha256(artifacts),
        "artifacts": artifacts,
        "multipart": True,
        "architecture": "qwen2",
        "quantization": "Q4_K_M",
        "license": "Apache-2.0",
        "download_policy": "direct",
        "provenance_status": "official",
        "official_gguf": True,
    }
    values.update(overrides)
    return ModelCatalogEntry(**values)


class Response:
    def __init__(self, content: bytes, url: str) -> None:
        self.content = content
        self.offset = 0
        self.url = url
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, count: int) -> bytes:
        chunk = self.content[self.offset:self.offset + count]
        self.offset += len(chunk)
        return chunk

    def geturl(self) -> str:
        return self.url


class MultipartDomainTests(unittest.TestCase):
    def test_manifest_is_ordered_and_catalog_requires_exact_total(self) -> None:
        parts = (
            ModelArtifact("a-00001-of-00002.gguf", 1, "a" * 64),
            ModelArtifact("a-00002-of-00002.gguf", 2, "b" * 64),
        )
        self.assertNotEqual(
            artifact_set_manifest_sha256(parts), artifact_set_manifest_sha256(reversed(parts))
        )
        with self.assertRaisesRegex(DomainValidationError, "total"):
            artifact_entry(b"one", b"two", size_bytes=1)
        with self.assertRaisesRegex(DomainValidationError, "manifest"):
            artifact_entry(b"one", b"two", artifact_set_sha256="0" * 64)

    def test_install_spec_uses_manifest_identity_and_pinned_part_urls(self) -> None:
        entry = artifact_entry(b"one", b"two")
        spec = build_artifact_set_install_spec(entry)
        self.assertEqual(spec.artifact_set_sha256, entry.identity_sha256)
        self.assertEqual(spec.loader_filename, entry.artifacts[0].filename)
        self.assertEqual(len(spec.artifacts), 2)
        self.assertTrue(all("/resolve/" + "a" * 40 + "/" in part.source_url for part in spec.artifacts))


class MultipartWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        source = self.root / "first.gguf"
        write_gguf(source, {
            "general.architecture": "qwen2",
            "tokenizer.chat_template": "{{ messages }}",
            "tokenizer.ggml.model": "gpt2",
        })
        self.first = source.read_bytes()
        self.second = b"synthetic second shard"
        self.entry = artifact_entry(self.first, self.second)
        self.backend = LlamaCppInferenceBackend(self.root / "models", llama_factory=lambda **_: object())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request_artifacts(self) -> list[dict[str, object]]:
        return [
            {**part.to_dict(), "source_url": f"https://models.example/{part.filename}"}
            for part in self.entry.artifacts
        ]

    def install(self) -> dict[str, object]:
        responses = [
            Response(self.first, "https://models.example/first"),
            Response(self.second, "https://models.example/second"),
        ]
        with mock.patch("urllib.request.urlopen", side_effect=responses):
            return self.backend.install_model_set(
                self.entry.id,
                self.request_artifacts(),
                self.entry.artifact_set_sha256,
                {"architecture": "qwen2", "source_revision": "a" * 40},
            )

    def test_atomic_install_inventory_and_loader_use_one_logical_model(self) -> None:
        installed = self.install()
        self.assertEqual(installed["sha256"], self.entry.artifact_set_sha256)
        self.assertEqual(installed["artifact_count"], 2)
        self.assertEqual(len(self.backend.list_models()), 1)
        inventory = self.backend.model_inventory()
        self.assertEqual(len(inventory), 1)
        self.assertTrue(inventory[0]["checksum_valid"])
        self.assertEqual(inventory[0]["artifact_kind"], "artifact_set")
        loaded_paths: list[str] = []
        self.backend._llama_factory = lambda **kwargs: loaded_paths.append(kwargs["model_path"]) or object()
        loaded = self.backend.load_model(self.entry.id, 128, 0)
        self.assertTrue(loaded["loaded"])
        self.assertTrue(loaded_paths[0].endswith(self.entry.artifacts[0].filename))

    def test_partial_failure_never_promotes_a_shard(self) -> None:
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[Response(self.first, "https://models.example/first"), OSError("offline")],
        ):
            with self.assertRaisesRegex(OSError, "offline"):
                self.backend.install_model_set(
                    self.entry.id, self.request_artifacts(), self.entry.artifact_set_sha256
                )
        model_dir = self.root / "models" / "family"
        self.assertEqual(list(model_dir.glob("*.gguf")), [])
        self.assertFalse((self.root / "models" / ".cluster-model-metadata.json").exists())

    def test_tampered_member_blocks_inventory_and_load(self) -> None:
        self.install()
        second = self.root / "models" / "family" / self.entry.artifacts[1].filename
        second.write_bytes(b"tampered")
        self.assertFalse(self.backend.model_inventory()[0]["checksum_valid"])
        with self.assertRaisesRegex(ValueError, "artifact-set checksum"):
            self.backend.load_model(self.entry.id, 128, 0)


class MultipartIntegrationTests(unittest.TestCase):
    def test_preflight_and_sweep_accept_manifest_identity(self) -> None:
        data = fixtures()
        original = data["catalog"][1]
        model = data["inventories"][0].models[1]
        entry = artifact_entry(
            b"one",
            b"two",
            architecture="fake",
            quantization=original.quantization,
            verified_platforms=original.verified_platforms,
            verified_llama_cpp_commits=original.verified_llama_cpp_commits,
            verification_status="verified",
            kv_cache_bytes_per_token=1024,
        )
        installed = dataclasses.replace(
            model,
            id=entry.id,
            filename=entry.gguf_filename,
            size_bytes=entry.size_bytes,
            sha256=entry.artifact_set_sha256,
            source_revision=entry.download_revision,
            artifact_kind="artifact_set",
            artifact_count=2,
        )
        validate_model_preflight(
            node_names=["j1"],
            inventories={"j1": WorkerModelInventory("j1", (installed,))},
            model_ids=[entry.id],
            execution_strategy="replicated_round_robin",
            rpc_coordinator_node=None,
            catalog={entry.id: entry},
        )
        data["catalog"][1] = entry
        data["selections"]["b"] = entry.id
        data["inventories"] = [
            dataclasses.replace(item, models=(item.models[0], installed))
            for item in data["inventories"]
        ]
        preview = preview_catalog_sweep(**data)
        self.assertEqual([cell.status for cell in preview.plan.cells], ["valid", "valid"])
        resolved = next(item for item in preview.plan.context.models if item.ref == "b")
        self.assertEqual(resolved.artifact_kind, "artifact_set")
        self.assertEqual(resolved.artifact_sha256, entry.artifact_set_sha256)

    def test_dashboard_emits_catalog_derived_artifact_set_action(self) -> None:
        from cluster.dashboard import services

        entry = artifact_entry(b"one", b"two")
        worker = Node("jetson-01", "worker", "192.168.0.26", "jetson", 22, 8000, "/tmp/project", True, platform="jetson")
        payload = ModelInstallPayload(nodes=[worker.name], confirmed=True)
        action = {"id": "action-1", "status": "queued"}
        with mock.patch.object(services, "read_model_catalog", return_value=[entry]), mock.patch.object(
            services, "read_enabled_nodes", return_value=[worker]
        ), mock.patch.object(services, "read_environment_reports", return_value=[]), mock.patch.object(
            services.actions, "start", return_value=action
        ) as start:
            result = services.DashboardFacade().install_catalog_model(entry.id, payload)
        self.assertTrue(result["ok"])
        sent = start.call_args.args[0]
        self.assertEqual(sent.action, "install-model-set")
        self.assertEqual(sent.options["artifact_set_sha256"], entry.artifact_set_sha256)
        self.assertEqual(len(sent.options["artifacts"]), 2)

    def test_gated_artifact_set_uses_authenticated_controller_cache_action(self) -> None:
        from cluster.dashboard import services

        entry = artifact_entry(
            b"one", b"two", gated=True, license_review_required=True, license="Model Terms"
        )
        worker = Node("jetson-01", "worker", "192.168.0.26", "jetson", 22, 8000, "/tmp/project", True, platform="jetson")
        payload = ModelInstallPayload(nodes=[worker.name], confirmed=True)
        with mock.patch.object(services, "read_model_catalog", return_value=[entry]), mock.patch.object(
            services, "read_enabled_nodes", return_value=[worker]
        ), mock.patch.object(services, "read_environment_reports", return_value=[]), mock.patch.object(
            services, "model_license_status", return_value={"accepted": True}
        ), mock.patch.object(
            services, "huggingface_access_status", return_value={"configured": True}
        ), mock.patch.object(services.actions, "start", return_value={"id": "a"}) as start:
            result = services.DashboardFacade().install_catalog_model(entry.id, payload)
        self.assertEqual(result["download_mode"], "controller_authenticated")
        self.assertEqual(start.call_args.args[0].action, "install-model-set-cache")

    def test_gated_cache_command_downloads_each_fake_part_then_publishes_set(self) -> None:
        from cluster import clusterctl

        entry = artifact_entry(b"one", b"two")
        worker = Node("jetson-01", "worker", "192.168.0.26", "jetson", 22, 8000, "/tmp/project", True, platform="jetson")
        args = SimpleNamespace(
            confirmed=True,
            license_accepted=True,
            artifact_manifest=json.dumps([item.to_dict() for item in entry.artifacts]),
            model_id=entry.id,
            source_repo=entry.download_repo,
            source_revision=entry.download_revision,
            artifact_set_sha256=entry.artifact_set_sha256,
            provenance_status="official",
            architecture="qwen2",
            metadata_contract="gguf-metadata-v1",
        )
        verified = {"model": {"sha256": entry.artifact_set_sha256, "checksum_valid": True}}
        with mock.patch.object(
            clusterctl, "download_verified_model_to_controller_cache", return_value={"already_present": False}
        ) as download, mock.patch.object(
            clusterctl, "sync_models_one", return_value={"ok": True, "stdout": "", "stderr": ""}
        ) as sync, mock.patch.object(clusterctl, "request_json", return_value=verified) as request:
            result = clusterctl.command_install_model_set_cache([worker], args)
        self.assertEqual(result, 0)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(len(sync.call_args.args[1]), 2)
        self.assertTrue(request.call_args.args[0].endswith("/cluster/models/verify-set"))


if __name__ == "__main__":
    unittest.main()
