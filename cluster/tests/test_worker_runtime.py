from __future__ import annotations

import ast
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from fastapi.testclient import TestClient

from cluster.worker.app import create_app
from cluster.worker.inference import LlamaCppInferenceBackend
from cluster.worker.telemetry import (
    GenericPsutilTelemetry,
    JetsonTelemetry,
    RaspberryPiTelemetry,
    TelemetryService,
)


class FakeInferenceBackend:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.models_dir = Path("/tmp/test-models")
        self.loaded: Optional[str] = None
        self.seed: Optional[int] = None
        self.load_calls: list[tuple[str, int, int]] = []

    def list_models(self) -> list[Dict[str, object]]:
        return [{"id": "tiny.gguf", "name": "tiny.gguf", "size_mb": 1.0, "is_loaded": self.loaded == "tiny.gguf"}]

    def current_model_info(self) -> Dict[str, object]:
        return {"loaded": self.loaded is not None, "model_id": self.loaded}

    def load_model(self, model_id: str, n_ctx: int, n_gpu_layers: int) -> Dict[str, object]:
        if not self.ready:
            raise RuntimeError("backend unavailable")
        if model_id != "tiny.gguf":
            raise FileNotFoundError(model_id)
        self.load_calls.append((model_id, n_ctx, n_gpu_layers))
        self.loaded = model_id
        return {"loaded": True, "model_id": model_id, "n_ctx": n_ctx, "n_gpu_layers": n_gpu_layers}

    def unload_model(self) -> None:
        self.loaded = None

    def stream_chat(self, **kwargs: Any) -> Iterable[str]:
        if self.loaded is None:
            raise RuntimeError("No model loaded. Select a model first.")
        if kwargs.get("seed") is not None:
            self.set_seed(int(kwargs["seed"]))
        yield "hello"
        yield " world"

    def tokenize(self, text: str) -> int:
        return 2 if text else 0

    def set_seed(self, seed: int) -> None:
        self.seed = seed

    def readiness(self) -> Dict[str, object]:
        return {"ready": self.ready, "error": None if self.ready else "backend unavailable"}


class FakeTelemetry:
    def __init__(self, *, ready: bool = True, degraded: bool = False) -> None:
        self.ready = ready
        self.degraded = degraded
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        return None

    def status(self) -> Dict[str, Any]:
        return {
            "provider": "psutil" if self.degraded else "fake",
            "ready": self.ready,
            "degraded": self.degraded,
            "error": "jtop client/service mismatch" if self.degraded else None,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {"gpu_pct": None, "power_w": None, "platform_kind": "raspberry-pi"}


class WorkerRouteContractTests(unittest.TestCase):
    def make_client(self, *, auth: bool = False, backend_ready: bool = True) -> tuple[TestClient, FakeInferenceBackend, FakeTelemetry]:
        backend = FakeInferenceBackend(ready=backend_ready)
        provider = FakeTelemetry(ready=False, degraded=True)
        telemetry = TelemetryService(provider)
        app = create_app(
            backend=backend,
            telemetry=telemetry,
            project_root=Path(tempfile.gettempdir()),
            environment={
                "CLUSTER_PLATFORM": "raspberry-pi",
                "CLUSTER_NODE_NAME": "pi-01",
                "CLUSTER_NODE_ROLE": "worker",
                "CLUSTER_WORKER_AUTH": "true" if auth else "false",
                "CLUSTER_API_TOKEN": "test-token" if auth else "",
            },
        )
        return TestClient(app), backend, provider

    def test_mock_backend_load_unload_and_stream_contract(self) -> None:
        client, backend, telemetry = self.make_client()
        self.assertTrue(telemetry.started)
        models = client.get("/api/models")
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["models"][0]["id"], "tiny.gguf")
        self.assertEqual(models.json()["models_dir"], "/tmp/test-models")
        loaded = client.post("/api/select-model", json={"model_id": "tiny.gguf", "n_ctx": 512, "n_gpu_layers": 0})
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(backend.load_calls, [("tiny.gguf", 512, 0)])
        response = client.post("/cluster/chat/stream", json={"message": "hi", "max_tokens": 4, "seed": 123})
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "token"', response.text)
        self.assertIn('"generated_tokens": 2', response.text)
        self.assertEqual(backend.seed, 123)
        unloaded = client.post("/api/unload-model")
        self.assertEqual(unloaded.status_code, 200)
        self.assertFalse(unloaded.json()["current"]["loaded"])

    def test_model_load_failure_keeps_message_and_exposes_stable_error_code(self) -> None:
        client, _, _ = self.make_client()
        response = client.post(
            "/api/select-model",
            json={"model_id": "missing.gguf", "n_ctx": 512, "n_gpu_layers": 0},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("missing.gguf", response.json()["detail"])
        self.assertEqual(response.headers["X-Cluster-Error-Code"], "MODEL_MISSING")

    def test_health_separates_inference_and_degraded_telemetry(self) -> None:
        client, _, _ = self.make_client(backend_ready=True)
        health = client.get("/cluster/health")
        self.assertEqual(health.status_code, 200)
        payload = health.json()
        self.assertEqual(payload["node"]["role"], "worker")
        self.assertTrue(payload["capabilities"]["inference_ready"])
        self.assertTrue(payload["capabilities"]["telemetry_degraded"])
        self.assertFalse(payload["capabilities"]["telemetry_ready"])
        self.assertIsNone(payload["metrics"]["gpu_pct"])
        self.assertIsNone(payload["metrics"]["power_w"])

    def test_worker_auth_protects_all_worker_routes(self) -> None:
        client, _, _ = self.make_client(auth=True)
        self.assertEqual(client.get("/cluster/health").status_code, 401)
        authorized = client.get("/cluster/health", headers={"X-Cluster-Worker-Token": "test-token"})
        self.assertEqual(authorized.status_code, 200)


class WorkerTelemetryTests(unittest.TestCase):
    def test_provider_selection_and_pi_unavailable_metrics_are_none(self) -> None:
        root = Path(tempfile.gettempdir())
        self.assertIsInstance(TelemetryService.for_platform("generic-linux", root).provider, GenericPsutilTelemetry)
        self.assertIsInstance(TelemetryService.for_platform("jetson", root).provider, JetsonTelemetry)
        pi = TelemetryService.for_platform("raspberry-pi", root).provider
        self.assertIsInstance(pi, RaspberryPiTelemetry)
        snapshot = pi.snapshot()
        self.assertIsNone(snapshot["gpu_pct"])
        self.assertIsNone(snapshot["power_w"])

    def test_jtop_failure_degrades_to_psutil_without_preventing_snapshot(self) -> None:
        class BrokenJtop:
            def __init__(self, **_: Any) -> None:
                pass

            def __enter__(self) -> "BrokenJtop":
                raise RuntimeError("jtop client/service mismatch")

            def __exit__(self, *_: Any) -> None:
                return None

        telemetry = JetsonTelemetry(Path(tempfile.gettempdir()), jtop_factory=BrokenJtop)
        telemetry.refresh()
        status = telemetry.status()
        self.assertTrue(status["degraded"])
        self.assertFalse(status["ready"])
        self.assertIn("mismatch", status["error"])
        snapshot = telemetry.snapshot()
        self.assertEqual(snapshot["accelerator"]["type"], "cuda")
        self.assertIsNone(snapshot["gpu_pct"])
        self.assertIsNotNone(snapshot["ram_pct"])


class WorkerStandaloneBoundaryTests(unittest.TestCase):
    def test_worker_runtime_does_not_import_web_app(self) -> None:
        worker_dir = Path(__file__).resolve().parents[1] / "worker"
        for source_path in worker_dir.glob("*.py"):
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            self.assertNotIn("web.app", imported, source_path.name)

    def test_backend_imports_and_reports_missing_native_runtime_without_parent_app(self) -> None:
        backend = LlamaCppInferenceBackend(Path(tempfile.gettempdir()) / "missing-models")
        result = backend.readiness()
        self.assertIn("ready", result)

    def test_worker_import_does_not_load_legacy_web_application(self) -> None:
        sys.modules.pop("web.app", None)
        importlib.reload(sys.modules["cluster.worker.app"])
        self.assertNotIn("web.app", sys.modules)


class LlamaBackendCompatibilityTests(unittest.TestCase):
    def test_model_load_retry_and_chat_template_fallback_match_legacy_behavior(self) -> None:
        class FakeLlama:
            attempts: list[tuple[int, int, int]] = []

            def __init__(self, **kwargs: Any) -> None:
                self.kwargs = kwargs
                FakeLlama.attempts.append((kwargs["n_ctx"], kwargs["n_gpu_layers"], kwargs["n_batch"]))
                if kwargs["n_gpu_layers"] > 4:
                    raise RuntimeError("simulated GPU memory pressure")

            def create_chat_completion(self, **_: Any) -> Iterable[Dict[str, object]]:
                raise RuntimeError("no chat template")

            def create_completion(self, **kwargs: Any) -> Iterable[Dict[str, object]]:
                self.prompt = kwargs["prompt"]
                return iter([{"choices": [{"text": "A"}]}, {"choices": [{"text": "B"}]}])

            def tokenize(self, _: bytes, add_bos: bool = False) -> list[int]:
                return [1, 2]

            def set_seed(self, seed: int) -> None:
                self.seed = seed

        with tempfile.TemporaryDirectory() as directory:
            models = Path(directory) / "models"
            models.mkdir()
            (models / "tiny.gguf").write_bytes(b"gguf")
            backend = LlamaCppInferenceBackend(models, llama_factory=FakeLlama, torch_module=None)
            loaded = backend.load_model("tiny.gguf", 1024, 8)
            self.assertTrue(loaded["auto_adjusted_n_gpu_layers"])
            self.assertEqual(loaded["n_gpu_layers"], 4)
            self.assertTrue(any(layers == 8 for _, layers, _ in FakeLlama.attempts))
            self.assertEqual(
                list(
                    backend.stream_chat(
                        message="hello",
                        history=[],
                        max_tokens=4,
                        temperature=0.2,
                        top_p=0.9,
                        seed=7,
                    )
                ),
                ["A", "B"],
            )
            self.assertEqual(backend.tokenize("AB"), 2)
            self.assertEqual(backend.llm.seed, 7)


if __name__ == "__main__":
    unittest.main()
