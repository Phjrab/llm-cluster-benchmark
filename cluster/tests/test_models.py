from __future__ import annotations

import ast
import hashlib
import json
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
from cluster.domain.model import ModelCatalogEntry, ModelInventoryEntry, recommend_models


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


class ModelControllerBoundaryTests(unittest.TestCase):
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
        self.assertEqual([item["id"] for item in models], [MODEL_ID, "qwen2.5-1.5b/Qwen2.5-1.5B-Instruct-Q4_K_M.gguf"])
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


if __name__ == "__main__":
    unittest.main()
