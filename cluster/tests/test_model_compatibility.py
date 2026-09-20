"""R06 model compatibility evidence uses only deterministic local fixtures."""

from __future__ import annotations

import unittest

from cluster.benchmark.core import model_compatibility_evidence
from cluster.application.model_service import WorkerModelInventory
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.model import (
    ModelCatalogEntry,
    ModelInventoryEntry,
    ModelVerificationStatus,
    recommend_model_candidates,
)


MODEL_ID = "fixture/model-Q4_K_M.gguf"
MODEL_SHA = "a" * 64
REVISION = "b" * 40


def catalog_entry() -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=MODEL_ID,
        architecture="qwen2",
        size_bytes=256 * 1024 * 1024,
        kv_cache_bytes_per_token=16 * 1024,
        hf_repo="fixture/model-GGUF",
        hf_revision=REVISION,
        gguf_filename="model-Q4_K_M.gguf",
        sha256=MODEL_SHA,
        quantization="Q4_K_M",
        license="Apache-2.0",
        recommended_platforms=("jetson",),
        verification_status="verified",
        verified_platforms=("jetson",),
        verified_llama_cpp_commits=("runtime-fixture",),
    )


def installed(*, architecture: str = "qwen2", sha256: str = MODEL_SHA) -> ModelInventoryEntry:
    return ModelInventoryEntry(
        MODEL_ID,
        "model-Q4_K_M.gguf",
        256 * 1024 * 1024,
        sha256,
        "Q4_K_M",
        True,
        source_revision=REVISION,
        architecture=architecture,
        metadata_contract="gguf-metadata-v1",
        metadata_inspected=True,
    )


class CompatibilityRecommendationTests(unittest.TestCase):
    def test_catalog_smoke_without_exact_installed_artifact_is_not_runtime_verified(self) -> None:
        recommendation = recommend_model_candidates(
            [catalog_entry()],
            platform="jetson",
            memory_total_mb=8192,
            memory_available_mb=7000,
            backend_verified=True,
            runtime_commit="runtime-fixture",
            installed_models={},
        )[0]
        self.assertEqual(recommendation.status, ModelVerificationStatus.COMPATIBLE)
        self.assertEqual(recommendation.compatibility.installation, "unknown")
        self.assertEqual(recommendation.compatibility.runtime_smoke, "unknown")
        self.assertEqual(recommendation.compatibility.formal_approval, "not_assessed")

    def test_exact_artifact_architecture_backend_and_runtime_smoke_are_verified(self) -> None:
        recommendation = recommend_model_candidates(
            [catalog_entry()],
            platform="jetson",
            memory_total_mb=8192,
            memory_available_mb=7000,
            backend_verified=True,
            runtime_commit="runtime-fixture",
            installed_models={MODEL_ID: installed()},
        )[0]
        self.assertEqual(recommendation.status, ModelVerificationStatus.RECOMMENDED)
        self.assertEqual(recommendation.compatibility.status, "verified")
        self.assertEqual(recommendation.compatibility.artifact_identity, "valid")
        self.assertEqual(recommendation.compatibility.architecture, "valid")

    def test_architecture_or_artifact_drift_is_blocked_not_compatible(self) -> None:
        for model in (installed(architecture="llama"), installed(sha256="c" * 64)):
            recommendation = recommend_model_candidates(
                [catalog_entry()],
                platform="jetson",
                memory_total_mb=8192,
                memory_available_mb=7000,
                backend_verified=True,
                runtime_commit="runtime-fixture",
                installed_models={MODEL_ID: model},
            )[0]
            self.assertEqual(recommendation.status, ModelVerificationStatus.UNSUPPORTED)
            self.assertEqual(recommendation.compatibility.status, "blocked")


class ResultCompatibilityEvidenceTests(unittest.TestCase):
    def test_completed_run_records_observed_runtime_without_formal_promotion(self) -> None:
        config = ExperimentConfig(
            node_names=["jetson-01"],
            model_id=MODEL_ID,
            sweep={
                "sweep_id": "sweep-1",
                "plan_sha256": "d" * 64,
                "cell_id": "cell-1",
                "trial_id": "trial-1",
                "attempt_id": "attempt-1",
                "sweep_repeat_index": 1,
                "model_sha256": MODEL_SHA,
                "template_sha256": "e" * 64,
                "prompt_sha256": "f" * 64,
                "prompt_mode": "same_text",
                "model_identity": {
                    "catalog_id": MODEL_ID,
                    "model_id": MODEL_ID,
                    "artifact_sha256": MODEL_SHA,
                    "source_revision": REVISION,
                    "quantization": "Q4_K_M",
                    "template_sha256": "e" * 64,
                    "size_bytes": 256 * 1024 * 1024,
                    "architecture": "qwen2",
                    "tokenizer_sha256": "1" * 64,
                    "artifact_kind": "single_gguf",
                },
            },
        )
        evidence = model_compatibility_evidence(
            config,
            [{
                "name": "jetson-01",
                "detected_platform": "jetson",
                "runtime_backend": {
                    "kind": "cuda",
                    "verified": True,
                    "runtime_fingerprint": "runtime-fixture",
                    "llama_cpp_python": "0.3.20",
                },
            }],
            [{
                "node": "jetson-01",
                "model_id": MODEL_ID,
                "model_sha256": MODEL_SHA,
                "source_revision": REVISION,
                "architecture": "qwen2",
            }],
            [{"node": "jetson-01", "ok": True}],
            status="completed",
        )[0]
        self.assertEqual(evidence["status"], "verified")
        self.assertEqual(evidence["runtime_smoke"], "valid")
        self.assertEqual(evidence["formal_approval"], "not_assessed")
        self.assertNotIn("model_path", evidence)

    def test_legacy_run_without_pinned_identity_stays_observed_unverified(self) -> None:
        config = ExperimentConfig(node_names=["worker-01"], model_id=MODEL_ID)
        evidence = model_compatibility_evidence(
            config,
            [{"name": "worker-01", "runtime_backend": {"kind": "cpu", "verified": True}}],
            [{"node": "worker-01", "model_id": MODEL_ID, "model_sha256": MODEL_SHA}],
            [{"node": "worker-01", "ok": True}],
            status="completed",
        )[0]
        self.assertEqual(evidence["status"], "observed_unverified")
        self.assertEqual(evidence["artifact_identity"], "unknown")


class CompatibilityPreflightTests(unittest.TestCase):
    def test_preflight_returns_separate_runtime_and_formal_evidence(self) -> None:
        from cluster.dashboard.services import validate_catalog_execution_preflight
        from cluster.integrations.legacy_inventory_runtime import Node

        entry = catalog_entry()
        node = Node(
            "jetson-01", "worker", "192.0.2.10", "edge", 22, 8000,
            "/opt/cluster", True, platform="jetson",
        )
        evidence = validate_catalog_execution_preflight(
            nodes=[node],
            live_status={
                node.name: {
                    "profile": {
                        "platform_kind": "jetson",
                        "memory_total_mb": 8192,
                        "memory_available_mb": 7000,
                        "runtime_backend": {
                            "verified": True,
                            "runtime_fingerprint": "runtime-fixture",
                        },
                    }
                }
            },
            inventories={node.name: WorkerModelInventory(node.name, (installed(),))},
            catalog={entry.id: entry},
            model_ids=[entry.id],
            n_ctx=4096,
            execution_strategy="single_node",
            rpc_coordinator_node=None,
        )
        self.assertEqual(evidence[0]["status"], "verified")
        self.assertEqual(evidence[0]["formal_approval"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
