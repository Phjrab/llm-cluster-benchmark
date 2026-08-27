from __future__ import annotations

import csv
import json
import tempfile
import unittest
from urllib.error import HTTPError
from pathlib import Path
from unittest import mock

from cluster.benchmark.persistence import RunPersistence
from cluster.benchmark.runner import run_experiment
from cluster.domain.errors import ErrorCode
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.failures import FAILURE_GUIDE, failure_from_exception, failure_from_message
from cluster.infrastructure.storage import FilesystemRunRepository


def config(
    *, persist_prompt: bool = True, response_storage_mode: str = "full"
) -> ExperimentConfig:
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
        response_storage_mode=response_storage_mode,
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
            self.assertEqual(saved["response_storage_status"], "stored")
            self.assertEqual(saved["generated_tokens"], 2)
            self.assertEqual(saved["ttft_s"], 0.1)
            self.assertEqual(persistence.recover_records(), [saved])
            self.assertIn("request_completed", (run_dir / "events.jsonl").read_text(encoding="utf-8"))
            events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("answer", events)
            self.assertNotIn("patient privacy prompt", events)

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
            self.assertEqual(persisted_config["prompt_chars"], len("patient privacy prompt"))
            self.assertEqual((persistence.run_dir / "responses.jsonl").stat().st_mode & 0o777, 0o600)

    def test_hash_only_keeps_length_hash_and_metrics_without_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = RunPersistence(
                Path(directory), "20260820_123456_ab12",
                config(persist_prompt=False, response_storage_mode="hash_only"),
            )
            sensitive = record(
                error="failure patient privacy prompt answer", response="answer"
            )
            persistence.emit("request_completed", completed=1, total=1, result=sensitive)
            saved = persistence.recover_records()[0]
            self.assertEqual(saved["response_storage_status"], "hash_only")
            self.assertNotIn("response", saved)
            self.assertEqual(saved["output_chars"], 6)
            self.assertEqual(len(saved["output_sha256"]), 64)
            self.assertEqual(saved["error"], "failure [REDACTED] [REDACTED]")
            artifacts = (persistence.run_dir / "events.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("patient privacy prompt", artifacts)
            self.assertNotIn('"response": "answer"', artifacts)

    def test_none_keeps_request_metrics_but_omits_response_length_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            persistence = RunPersistence(
                Path(directory), "20260820_123456_ab12",
                config(response_storage_mode="none"),
            )
            result = record()
            result["output_chars"] = 6
            persistence.emit("request_completed", completed=1, total=1, result=result)
            saved = persistence.recover_records()[0]
            self.assertEqual(saved["response_storage_status"], "not_persisted")
            self.assertEqual(saved["generated_tokens"], 2)
            self.assertNotIn("response", saved)
            self.assertNotIn("output_chars", saved)
            self.assertNotIn("output_sha256", saved)
            persistence.complete([result], {"run_id": persistence.run_id})
            with (persistence.run_dir / "requests.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                csv_record = next(csv.DictReader(handle))
            self.assertEqual(csv_record["output_chars"], "")
            self.assertEqual(csv_record["output_sha256"], "")

    def test_legacy_result_without_response_journal_remains_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory))
            repository.create("20260820_123456_ab12", {"model_id": "models/example.gguf"})
            self.assertEqual(repository.read_responses("20260820_123456_ab12"), [])
            repository.write_requests("20260820_123456_ab12", [record()])
            header = (Path(directory) / "20260820_123456_ab12" / "requests.csv").read_text(encoding="utf-8").splitlines()[0]
            self.assertNotIn("response", header)
            self.assertNotIn("failure", header)

    def test_delete_moves_exact_run_to_private_recoverable_trash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory))
            run_dir = repository.create("20260820_123456_ab12", {"model_id": "models/example.gguf"})
            repository.write_summary("20260820_123456_ab12", {"run_id": "20260820_123456_ab12"})
            destination = repository.delete("20260820_123456_ab12")
            self.assertFalse(run_dir.exists())
            self.assertTrue((destination / "summary.json").is_file())
            self.assertEqual(destination.parent.name, "_trash")
            self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
            self.assertEqual(repository.list_summaries(), [])

    def test_trash_lists_checksum_restores_and_requires_checksum_for_purge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory))
            run_id = "20260824_120000_ab12"
            repository.create(run_id, {"model_id": "models/example.gguf"})
            repository.write_summary(run_id, {"run_id": run_id, "status": "completed"})
            destination = repository.delete(run_id)
            entries = repository.list_trash()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["trash_id"], destination.name)
            self.assertEqual(entries[0]["run_id"], run_id)
            self.assertEqual(len(entries[0]["archive_sha256"]), 64)
            with self.assertRaisesRegex(ValueError, "checksum"):
                repository.purge(destination.name, archive_sha256="0" * 64)
            restored = repository.restore(destination.name)
            self.assertEqual(restored.name, run_id)
            self.assertEqual(repository.read_summary(run_id)["status"], "completed")
            self.assertEqual(repository.list_trash(), [])

    def test_formal_campaign_trash_is_retention_protected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory))
            run_id = "20260824_120001_ab12"
            repository.create(run_id, {"model_id": "models/example.gguf"})
            repository.write_summary(run_id, {
                "run_id": run_id,
                "status": "completed",
                "experiment_type": "formal",
                "campaign_id": "formal-campaign-a",
            })
            destination = repository.delete(run_id)
            entry = repository.list_trash()[0]
            self.assertTrue(entry["protected"])
            with self.assertRaisesRegex(PermissionError, "cannot be permanently deleted"):
                repository.purge(destination.name, archive_sha256=entry["archive_sha256"])


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
        self.assertEqual(
            failure_from_exception(
                HTTPError("http://worker/api/select-model", 404, "Not Found", {}, None),
                stage="model_loading",
                model_id="missing.gguf",
            ).code,
            ErrorCode.MODEL_MISSING,
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
                side_effect=RuntimeError("CUDA out of memory patient privacy prompt"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Failed to load model") as raised:
                    run_experiment(config(), inventory_path=inventory_path, results_root=root / "results")
            self.assertNotIn("patient privacy prompt", str(raised.exception))
            run_dir = next((root / "results").glob("*"))
            summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("error", summary)
            self.assertEqual(summary["error_code"], ErrorCode.MODEL_LOAD_OOM.value)
            self.assertEqual(summary["failure"]["code"], ErrorCode.MODEL_LOAD_OOM.value)
            self.assertNotIn("patient privacy prompt", json.dumps(summary))
            self.assertNotIn(
                "patient privacy prompt",
                (run_dir / "events.jsonl").read_text(encoding="utf-8"),
            )
