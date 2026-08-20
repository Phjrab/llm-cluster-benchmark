from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.benchmark.persistence import RunPersistence
from cluster.benchmark.runner import run_experiment
from cluster.domain.errors import ErrorCode
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.failures import FAILURE_GUIDE, failure_from_exception, failure_from_message
from cluster.infrastructure.storage import FilesystemRunRepository


def config(*, persist_prompt: bool = True) -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="results-failure",
        node_names=["worker-01"],
        model_id="models/example.gguf",
        n_ctx=128,
        n_gpu_layers=0,
        requests=1,
        concurrency=1,
        max_tokens=4,
        warmup_requests=0,
        prompt="patient privacy prompt",
        persist_prompt=persist_prompt,
    )


def record(*, error: str = "", response: str = "answer") -> dict:
    return {
        "request_id": 1,
        "logical_request_id": 1,
        "scenario_id": "main",
        "replica_index": 0,
        "node": "worker-01",
        "assigned_node": "worker-01",
        "node_host": "192.168.0.27",
        "started_at": "2026-08-20T00:00:00+00:00",
        "ok": not error,
        "ttft_s": 0.1 if not error else None,
        "e2e_s": 0.4,
        "server_generation_s": 0.2,
        "generated_tokens": 2 if not error else 0,
        "tokens_per_s": 10.0 if not error else None,
        "output_sha256": "abc" if not error else "",
        "response": response,
        "error": error,
    }


class ResultDurabilityTests(unittest.TestCase):
    def test_completed_request_is_durable_before_final_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = RunPersistence(Path(directory), "20260820_123456_ab12", config())
            persistence.emit("request_completed", completed=1, total=1, result=record())
            run_dir = persistence.run_dir
            self.assertFalse((run_dir / "summary.json").exists())
            response_lines = (run_dir / "responses.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(response_lines), 1)
            saved = json.loads(response_lines[0])
            self.assertEqual(saved["prompt"], "patient privacy prompt")
            self.assertEqual(saved["response"], "answer")
            self.assertEqual(saved["generated_tokens"], 2)
            self.assertEqual(saved["ttft_s"], 0.1)
            self.assertEqual(persistence.recover_records(), [saved])
            self.assertIn("request_completed", (run_dir / "events.jsonl").read_text(encoding="utf-8"))

    def test_private_prompt_keeps_hash_not_raw_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = RunPersistence(Path(directory), "20260820_123456_ab12", config(persist_prompt=False))
            persistence.emit("request_completed", completed=1, total=1, result=record())
            saved = persistence.recover_records()[0]
            self.assertNotIn("prompt", saved)
            self.assertEqual(len(saved["prompt_sha256"]), 64)
            self.assertEqual(saved["response"], "answer")
            persisted_config = json.loads((persistence.run_dir / "config.json").read_text(encoding="utf-8"))
            self.assertNotIn("prompt", persisted_config)
            self.assertEqual((persistence.run_dir / "responses.jsonl").stat().st_mode & 0o777, 0o600)

    def test_legacy_result_without_response_journal_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory))
            repository.create("20260820_123456_ab12", {"model_id": "models/example.gguf"})
            self.assertEqual(repository.read_responses("20260820_123456_ab12"), [])
            repository.write_requests("20260820_123456_ab12", [record()])
            header = (Path(directory) / "20260820_123456_ab12" / "requests.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertNotIn("response", header)
            self.assertNotIn("failure", header)


class StructuredFailureTests(unittest.TestCase):
    def test_failure_record_keeps_legacy_string_and_serializes_evidence(self) -> None:
        failure = failure_from_message(
            "CUDA out of memory while loading model",
            stage="model_loading",
            node="jetson-01",
            model_id="models/example.gguf",
        )
        self.assertEqual(failure.code, ErrorCode.MODEL_LOAD_OOM)
        saved = failure.to_dict()
        self.assertEqual(saved["node"], "jetson-01")
        self.assertTrue(saved["solutions"])
        request = record(error="CUDA out of memory", response="")
        request["error_code"] = failure.code.value
        request["failure"] = saved
        self.assertEqual(request["error"], "CUDA out of memory")
        self.assertEqual(request["failure"]["code"], ErrorCode.MODEL_LOAD_OOM.value)

    def test_all_major_failure_codes_have_deterministic_guide(self) -> None:
        self.assertEqual(set(FAILURE_GUIDE), set(ErrorCode))
        self.assertTrue(all(FAILURE_GUIDE[code] for code in ErrorCode))

    def test_exception_mapping_is_deterministic(self) -> None:
        self.assertEqual(
            failure_from_exception(TimeoutError("slow"), stage="request").code,
            ErrorCode.REQUEST_TIMEOUT,
        )
        self.assertEqual(
            failure_from_message("Experiment cancelled", stage="run").code,
            ErrorCode.CANCELLED,
        )

    def test_failed_run_summary_adds_structured_failure_without_removing_error(self) -> None:
        inventory = (
            "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file\n"
            "legacy-head,head,127.0.0.1,test,22,8000,/opt/llm,true,\n"
            "worker-01,worker,192.168.0.27,test,22,8000,/opt/llm,true,\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory_path = root / "nodes.csv"
            inventory_path.write_text(inventory, encoding="utf-8")
            with mock.patch(
                "cluster.benchmark.runner._load_model",
                side_effect=RuntimeError("CUDA out of memory"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Failed to load model"):
                    run_experiment(config(), inventory_path=inventory_path, results_root=root / "results")
            summary = json.loads(next((root / "results").glob("*/summary.json")).read_text(encoding="utf-8"))
            self.assertIn("error", summary)
            self.assertEqual(summary["error_code"], ErrorCode.MODEL_LOAD_OOM.value)
            self.assertEqual(summary["failure"]["code"], ErrorCode.MODEL_LOAD_OOM.value)
