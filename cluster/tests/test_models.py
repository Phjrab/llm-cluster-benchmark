from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.application.model_service import (
    ModelPreflightError,
    WorkerModelInventory,
    aggregate_catalog,
    parse_worker_inventory,
    validate_model_preflight,
)
from cluster.domain.errors import ErrorCode
from cluster.domain.model import (
    ModelCatalogEntry,
    ModelInventoryEntry,
    ModelVerificationStatus,
    estimate_memory_fit,
    parse_catalog_entries,
    recommend_model_candidates,
    recommend_models,
)
from cluster.infrastructure.remote import CommandResult


MODEL_ID = "qwen/tiny-Q4_K_M.gguf"
MODEL_HASH = hashlib.sha256(b"gguf").hexdigest()


class ModelDomainTests(unittest.TestCase):
    def test_worker_inventory_requires_checksum_and_exposes_quantization(self) -> None:
        inventory = parse_worker_inventory(
            "jetson-01",
            [{"id": MODEL_ID, "filename": "tiny-Q4_K_M.gguf", "size_bytes": 4, "sha256": MODEL_HASH, "quantization": "Q4_K_M", "checksum_valid": True}],
        )
        self.assertEqual(inventory.models[0].sha256, MODEL_HASH)
        self.assertEqual(inventory.models[0].quantization, "Q4_K_M")
        malformed = parse_worker_inventory("jetson-01", [{"id": MODEL_ID, "size_bytes": 4}])
        self.assertEqual(malformed.models, ())

    def test_catalog_metadata_is_separate_from_worker_files_and_recommendation_is_deterministic(self) -> None:
        pi_fit = ModelCatalogEntry(id="pi-fit-Q4_K_M.gguf", estimated_memory_mb=1200, recommended_platforms=("raspberry-pi",), quantization="Q4_K_M")
        jetson_only = ModelCatalogEntry(id="jetson-Q4_K_M.gguf", estimated_memory_mb=900, recommended_platforms=("jetson",))
        recommended = recommend_models([jetson_only, pi_fit], platform="raspberry-pi", memory_total_mb=4096)
        self.assertEqual([item.id for item in recommended], ["pi-fit-Q4_K_M.gguf", "jetson-Q4_K_M.gguf"])
        inventory = WorkerModelInventory("pi-01", (ModelInventoryEntry(MODEL_ID, "tiny-Q4_K_M.gguf", 4, MODEL_HASH, "Q4_K_M"),))
        aggregate = aggregate_catalog([inventory], [pi_fit])
        observed = next(item for item in aggregate if item["id"] == MODEL_ID)
        self.assertEqual(observed["installed_nodes"], ["pi-01"])
        self.assertIsNone(observed["catalog"])
        catalog_only = next(item for item in aggregate if item["id"] == pi_fit.id)
        self.assertFalse(catalog_only["available"])
        pi_q8 = ModelCatalogEntry(id="pi-q8.gguf", estimated_memory_mb=1200, recommended_platforms=("raspberry-pi",), quantization="Q8_0")
        self.assertEqual(recommend_models([pi_q8, pi_fit], platform="raspberry-pi", memory_total_mb=4096)[0].id, pi_fit.id)

    def test_catalog_rejects_duplicate_invalid_repo_and_quantization(self) -> None:
        valid = {"id": "catalog/valid-Q4_K_M.gguf", "hf_repo": "owner/repository", "quantization": "Q4_K_M"}
        with self.assertRaises(ValueError):
            parse_catalog_entries([valid, valid])
        with self.assertRaises(ValueError):
            ModelCatalogEntry(id="catalog/bad-Q4_K_M.gguf", hf_repo="not a repo", quantization="Q4_K_M")
        with self.assertRaises(ValueError):
            ModelCatalogEntry(id="catalog/bad-Q4_K_M.gguf", quantization="Q4_BROKEN")

    def test_memory_fit_uses_gguf_bytes_kv_and_reserve_not_parameter_count(self) -> None:
        entry = ModelCatalogEntry(
            id="catalog/fit-Q4_K_M.gguf", size_bytes=1024 * 1024 * 1024,
            kv_cache_bytes_per_token=64 * 1024, compute_buffers_mb=256, backend_overhead_mb=256,
        )
        estimate = estimate_memory_fit(entry, memory_total_mb=4096, memory_available_mb=3600, context_length=4096)
        self.assertEqual(estimate.gguf_mapped_mb, 1024)
        self.assertEqual(estimate.kv_cache_mb, 256)
        self.assertEqual(estimate.system_reserve_mb, 1024)
        self.assertEqual(estimate.required_mb, 1920)
        self.assertTrue(estimate.fits)
        unknown = estimate_memory_fit(ModelCatalogEntry(id="catalog/unknown-Q4_K_M.gguf"), memory_total_mb=4096, memory_available_mb=3600)
        self.assertIsNone(unknown.fits)

    def test_recommendations_are_worker_only_and_need_smoke_for_recommended(self) -> None:
        entry = ModelCatalogEntry(
            id="catalog/locked-Q4_K_M.gguf", size_bytes=256 * 1024 * 1024,
            kv_cache_bytes_per_token=16 * 1024, hf_repo="owner/repo", hf_revision="f" * 40,
            gguf_filename="locked-Q4_K_M.gguf", sha256=MODEL_HASH, quantization="Q4_K_M",
            license="Apache-2.0",
            recommended_platforms=("jetson", "raspberry-pi"), verification_status="verified",
            verified_platforms=("jetson",), verified_llama_cpp_commits=("runtime-1",),
        )
        controller = recommend_model_candidates([entry], platform="controller", memory_total_mb=8192, memory_available_mb=7000, backend_verified=True, runtime_commit="runtime-1")[0]
        self.assertEqual(controller.status, ModelVerificationStatus.UNSUPPORTED)
        pi = recommend_model_candidates([entry], platform="raspberry-pi", memory_total_mb=8192, memory_available_mb=7000, backend_verified=True, runtime_commit="runtime-1")[0]
        self.assertEqual(pi.status, ModelVerificationStatus.COMPATIBLE)
        jetson = recommend_model_candidates([entry], platform="jetson", memory_total_mb=8192, memory_available_mb=7000, backend_verified=True, runtime_commit="runtime-1")[0]
        self.assertEqual(jetson.status, ModelVerificationStatus.RECOMMENDED)
        large = ModelCatalogEntry(id="catalog/large-Q4_K_M.gguf", size_bytes=7 * 1024 ** 3, kv_cache_bytes_per_token=256 * 1024, recommended_platforms=("jetson",))
        pi_large = recommend_model_candidates([large], platform="raspberry-pi", memory_total_mb=8192, memory_available_mb=4000, backend_verified=True)[0]
        self.assertEqual(pi_large.status, ModelVerificationStatus.UNSUPPORTED)


class ModelPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.good = WorkerModelInventory(
            "jetson-01",
            (ModelInventoryEntry(MODEL_ID, "tiny-Q4_K_M.gguf", 4, MODEL_HASH, "Q4_K_M", True),),
        )
        self.catalog = {MODEL_ID: ModelCatalogEntry(id=MODEL_ID, sha256=MODEL_HASH, estimated_memory_mb=32)}

    def test_preflight_allows_degraded_telemetry_when_worker_model_is_ready(self) -> None:
        # Telemetry is deliberately absent from the model preflight contract.
        validate_model_preflight(
            node_names=["jetson-01"], inventories={"jetson-01": self.good}, model_ids=[MODEL_ID],
            execution_strategy="single_node", rpc_coordinator_node=None, catalog=self.catalog,
        )

    def test_preflight_blocks_missing_and_bad_checksum_before_job(self) -> None:
        with self.assertRaises(ModelPreflightError) as missing:
            validate_model_preflight(
                node_names=["jetson-01"], inventories={"jetson-01": WorkerModelInventory("jetson-01", ())},
                model_ids=[MODEL_ID], execution_strategy="single_node", rpc_coordinator_node=None, catalog=self.catalog,
            )
        self.assertEqual(missing.exception.code, ErrorCode.MODEL_MISSING)
        corrupted = WorkerModelInventory(
            "jetson-01", (ModelInventoryEntry(MODEL_ID, "tiny-Q4_K_M.gguf", 4, "0" * 64, "Q4_K_M", True),)
        )
        with self.assertRaises(ModelPreflightError) as bad_checksum:
            validate_model_preflight(
                node_names=["jetson-01"], inventories={"jetson-01": corrupted}, model_ids=[MODEL_ID],
                execution_strategy="single_node", rpc_coordinator_node=None, catalog=self.catalog,
            )
        self.assertEqual(bad_checksum.exception.code, ErrorCode.MODEL_CORRUPTED)

    def test_rpc_requires_model_only_on_resolved_worker_coordinator(self) -> None:
        pi = WorkerModelInventory("pi-01", ())
        validate_model_preflight(
            node_names=["jetson-01", "pi-01"], inventories={"jetson-01": self.good, "pi-01": pi},
            model_ids=[MODEL_ID], execution_strategy="model_parallel_rpc", rpc_coordinator_node="jetson-01", catalog=self.catalog,
        )

    def test_replicated_workers_require_matching_model_checksum(self) -> None:
        other = WorkerModelInventory(
            "jetson-02", (ModelInventoryEntry(MODEL_ID, "tiny-Q4_K_M.gguf", 4, "1" * 64, "Q4_K_M", True),)
        )
        with self.assertRaises(ModelPreflightError) as mismatch:
            validate_model_preflight(
                node_names=["jetson-01", "jetson-02"], inventories={"jetson-01": self.good, "jetson-02": other},
                model_ids=[MODEL_ID], execution_strategy="replicated_round_robin", rpc_coordinator_node=None, catalog={},
            )
        self.assertEqual(mismatch.exception.code, ErrorCode.MODEL_CORRUPTED)

    def test_catalog_preflight_blocks_context_and_memory_overcommit(self) -> None:
        from cluster.dashboard.services import validate_catalog_execution_preflight
        from cluster.clusterctl import Node

        node = Node("jetson-01", "worker", "192.168.0.27", "edge", 22, 8000, "/opt/edge", True, platform="jetson")
        entry = ModelCatalogEntry(
            id=MODEL_ID, sha256=MODEL_HASH, quantization="Q4_K_M", context_length_advertised=4096,
            size_bytes=3 * 1024 ** 3, kv_cache_bytes_per_token=256 * 1024,
        )
        with self.assertRaises(ModelPreflightError) as context_error:
            validate_catalog_execution_preflight(
                nodes=[node], live_status={"jetson-01": {"profile": {"memory_total_mb": 8192, "memory_available_mb": 7000}}},
                inventories={"jetson-01": self.good}, catalog={MODEL_ID: entry}, model_ids=[MODEL_ID], n_ctx=8192,
                execution_strategy="single_node", rpc_coordinator_node=None,
            )
        self.assertEqual(context_error.exception.code, ErrorCode.CONFIG_MISMATCH)
        large_inventory = WorkerModelInventory("jetson-01", (ModelInventoryEntry(MODEL_ID, "tiny-Q4_K_M.gguf", 3 * 1024 ** 3, MODEL_HASH, "Q4_K_M", True),))
        with self.assertRaises(ModelPreflightError) as oom_error:
            validate_catalog_execution_preflight(
                nodes=[node], live_status={"jetson-01": {"profile": {"memory_total_mb": 4096, "memory_available_mb": 3000}}},
                inventories={"jetson-01": large_inventory}, catalog={MODEL_ID: entry}, model_ids=[MODEL_ID], n_ctx=4096,
                execution_strategy="single_node", rpc_coordinator_node=None,
            )
        self.assertEqual(oom_error.exception.code, ErrorCode.MODEL_LOAD_OOM)


class ModelControllerBoundaryTests(unittest.TestCase):
    def test_static_catalog_contains_requested_cohorts_as_unverified_candidates(self) -> None:
        from cluster.dashboard import app as dashboard
        catalog = {item.id: item for item in dashboard.services.read_model_catalog()}
        expected = {
            "qwen2.5-0.5b/Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
            "qwen2.5-7b/Qwen2.5-7B-Instruct-Q4_K_M.gguf",
            "qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf",
            "smollm2-1.7b/smollm2-1.7b-instruct-q4_k_m.gguf",
            "granite-3.3-2b/granite-3.3-2b-instruct-Q4_K_M.gguf",
            "lfm2.5-1.2b/LFM2.5-1.2B-Instruct-Q4_K_M.gguf",
            "gemma-4-e2b/gemma-4-E2B-it-qat-q4_0.gguf",
            "phi-4-mini/Phi-4-mini-instruct-Q4_K_M.gguf",
            "llama3.2-1b/Llama-3.2-1B-Instruct-Q4_K_M.gguf",
        }
        self.assertTrue(expected.issubset(catalog))
        self.assertEqual(catalog["qwen3-1.7b/Qwen3-1.7B-Q8_0.gguf"].quantization, "Q8_0")
        self.assertEqual(catalog["gemma-4-e2b/gemma-4-E2B-it-qat-q4_0.gguf"].parameters_effective_b, 2.3)
        self.assertEqual(catalog["gemma-4-e2b/gemma-4-E2B-it-qat-q4_0.gguf"].parameters_total_b, 5.1)
        self.assertFalse(catalog["qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"].identity_locked)

    def test_local_catalog_cache_remains_usable_without_remote_metadata(self) -> None:
        from cluster.dashboard import app as dashboard
        service = dashboard.services

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "catalog.json"
            cache = root / "cache.json"
            static.write_text(json.dumps({"models": [{"id": MODEL_ID, "vendor": "static"}]}), encoding="utf-8")
            cache.write_text(json.dumps({"models": [{"id": MODEL_ID, "vendor": "cached", "estimated_memory_mb": 64}]}), encoding="utf-8")
            with mock.patch.object(service, "MODEL_CATALOG_PATH", static), mock.patch.object(service, "MODEL_CATALOG_CACHE_PATH", cache):
                catalog = service.read_model_catalog()
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0].vendor, "cached")
        self.assertEqual(catalog[0].estimated_memory_mb, 64)

    def test_controller_aggregation_never_scans_controller_models_directory(self) -> None:
        from cluster.dashboard import app as dashboard
        service = dashboard.services
        worker = service.Node("worker-01", "worker", "192.168.0.27", "edge", 22, 8000, "/opt/cluster", True)
        head = service.Node("legacy-head", "head", "192.168.0.26", "edge", 22, 8000, "/opt/cluster", True)
        inventory = WorkerModelInventory("worker-01", (ModelInventoryEntry(MODEL_ID, "tiny-Q4_K_M.gguf", 4, MODEL_HASH, "Q4_K_M"),))
        with mock.patch.object(service, "read_enabled_nodes", return_value=[head, worker]), mock.patch.object(
            service, "fetch_worker_model_inventory", return_value=inventory
        ) as fetch:
            models = service.list_models()
        self.assertIn(MODEL_ID, [item["id"] for item in models])
        self.assertIn("qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf", [item["id"] for item in models])
        self.assertEqual(fetch.call_count, 1)

    def test_controller_model_operations_are_node_events_and_experiment_never_syncs_models(self) -> None:
        dashboard_source = (Path(__file__).resolve().parents[1] / "dashboard" / "services.py").read_text(encoding="utf-8")
        tree = ast.parse(dashboard_source)
        model_progress_channels = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "events" and node.func.attr == "publish"):
                continue
            if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "model_progress":
                model_progress_channels.extend(ast.unparse(keyword.value) for keyword in node.keywords if keyword.arg == "channel")
        self.assertEqual(model_progress_channels, ["EventChannel.NODE_OPS"])
        start_source = ast.get_source_segment(
            dashboard_source,
            next(
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "start_experiment"
            ),
        ) or ""
        self.assertNotIn("sync_models", start_source)
        self.assertNotIn("sync-models", start_source)
        frontend_source = (Path(__file__).resolve().parents[1] / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('message.type === "model_progress" && channel === "node_ops"', frontend_source)


class ModelSyncCompatibilityTests(unittest.TestCase):
    def test_worker_inventory_preserves_private_install_metadata(self) -> None:
        from cluster.worker.inference import LlamaCppInferenceBackend

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = root / MODEL_ID
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"gguf")
            (root / ".cluster-model-metadata.json").write_text(
                json.dumps({"schema_version": 1, "models": {MODEL_ID: {
                    "source_revision": "a" * 40, "architecture": "qwen2",
                    "chat_template_hash": "b" * 64, "license_accepted": True,
                }}}), encoding="utf-8"
            )
            backend = LlamaCppInferenceBackend(root)
            record = backend.model_inventory()[0]
        self.assertEqual(record["source_revision"], "a" * 40)
        self.assertEqual(record["architecture"], "qwen2")
        self.assertTrue(record["license_accepted"])
        self.assertTrue(record["metadata_inspected"])

    def test_sync_uses_macos_rsync_options_and_still_verifies_remote_checksum(self) -> None:
        from cluster import clusterctl

        node = clusterctl.Node(
            "pi-01", "worker", "192.168.0.16", "pi", 22, 8000,
            "/home/pi/llm-cluster-benchmark", True, platform="raspberry-pi",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models" / MODEL_ID
            source.parent.mkdir(parents=True)
            source.write_bytes(b"gguf")
            remote_results = iter((
                CommandResult(0, "", ""),
                CommandResult(0, "", ""),
                CommandResult(0, f"{MODEL_HASH}  remote.gguf\n", ""),
            ))
            completed = subprocess.CompletedProcess(["rsync"], 0, "progress", "")
            with mock.patch.object(clusterctl, "PROJECT_ROOT", root), mock.patch.object(
                clusterctl, "run_on_node", side_effect=lambda *_args, **_kwargs: next(remote_results)
            ), mock.patch.object(clusterctl, "_identity_path", return_value=None), mock.patch.object(
                clusterctl.subprocess, "run", return_value=completed
            ) as run:
                result = clusterctl.sync_models_one(node, [MODEL_ID])

        command = run.call_args.args[0]
        self.assertTrue(result["ok"])
        self.assertTrue(result["models"][0]["verified"])
        self.assertIn("--partial", command)
        self.assertIn("--progress", command)
        self.assertNotIn("--append-verify", command)
        self.assertNotIn("--info=progress2", command)


if __name__ == "__main__":
    unittest.main()
