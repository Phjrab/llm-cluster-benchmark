"""S09 result comparison/export tests use only synthetic local artifacts."""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path

from cluster.application.sweep_planner import compile_plan
from cluster.application.sweep_runner import build_sweep_manifest
from cluster.dashboard.service_layers.sweep_result_service import CSV_COLUMNS, SweepResultService
from cluster.domain.sweep import ResolutionContext, SweepSpec
from cluster.infrastructure.storage import FilesystemRunRepository
from cluster.tests.test_sweep_planner import context_data, rpc_spec


def simple_plan(*, execution_strategy="single_node", repeat_count=2):
    workers = ["j1", "j2"] if execution_strategy == "node_sweep" else ["j1"]
    raw = {
        "base": {
            "model_ref": "model-a", "prompt_ref": "same-text", "worker_ids": workers,
            "execution_strategy": execution_strategy, "n_ctx": 1024, "concurrency": 2,
            "max_tokens": 64, "n_gpu_layers": 4, "requests": 2, "warmup_requests": 1,
            "response_storage_mode": "hash_only",
        },
        "repeat_count": repeat_count,
        "execution": {
            "mode": "disjoint_parallel", "max_parallel_jobs": 2, "backfill_policy": "bounded",
        },
    }
    return compile_plan(SweepSpec.from_dict(raw), ResolutionContext.from_dict(context_data()))


class SweepResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = FilesystemRunRepository(Path(self.temporary.name))

    def write_run(self, run_id, value, start, finish, *, storage="hash_only"):
        self.repository.create(run_id, {"prompt": "private"})
        self.repository.write_summary(run_id, {
            "schema_version": 2, "run_id": run_id, "status": "completed",
            "started_at": start, "finished_at": finish, "cluster_tokens_per_s": value,
            "effective_user_tokens_per_s": value - 1, "requests_per_s": value / 10,
            "ttft_p50_s": 0.2, "e2e_p50_s": 1.2, "success_rate": 1.0,
            "response_storage_mode": storage,
            "actual_model_config": [{"effective_config": {"n_ctx": 768, "n_gpu_layers": 4}}],
            "measurement_instrumentation": {"overall": {
                "generated_tokens_per_j": None,
                "unavailable_node_reasons": [{"node": "j1", "reason": "sensor unavailable"}],
                "availability": {"energy_j": {"available": False, "reason": "one_or_more_nodes_unavailable"}},
            }},
        })
        self.repository.append_response(run_id, {
            "request_id": 1, "ok": True, "input_tokens": 64, "generated_tokens": 7,
            "input_tokens_exact": True, "output_tokens_exact": True,
            "input_token_source": "server_usage", "finish_reason": "stop",
            "response_storage_status": storage, "output_sha256": "a" * 64,
            "response": "must be hidden" if storage == "hash_only" else "visible",
        })

    def test_repeats_retry_adjustment_early_eos_energy_overlap_and_ci(self):
        plan = simple_plan()
        manifest = build_sweep_manifest("sweep_result", plan)
        first, second = manifest["trials"]
        first.update(status="completed", official_attempt_id="retry-ok")
        first["attempts"] = [
            {"attempt_id": "retry-failed", "status": "failed", "run_id": None, "failure_code": "=FAILED"},
            {"attempt_id": "retry-ok", "status": "completed", "run_id": "run_01", "failure_code": None},
        ]
        second.update(status="completed", official_attempt_id="attempt-02")
        second["attempts"] = [{"attempt_id": "attempt-02", "status": "completed", "run_id": "run_02"}]
        manifest.update(status="completed")
        self.write_run("run_01", 10.0, "2026-09-19T00:00:00Z", "2026-09-19T00:01:00Z")
        self.write_run("run_02", 14.0, "2026-09-19T00:00:30Z", "2026-09-19T00:02:00Z")

        result = SweepResultService(self.repository, bootstrap_resamples=100).assemble(manifest, plan)
        attempts = result["trials"][0]["attempts"]
        self.assertEqual([item["attempt_id"] for item in attempts], ["retry-failed", "retry-ok"])
        self.assertFalse(attempts[0]["representative"])
        self.assertTrue(attempts[1]["condition_mismatch"])
        self.assertEqual(attempts[1]["request_evidence"]["early_eos_count"], 1)
        self.assertNotIn("response", attempts[1]["responses"][0])
        self.assertFalse(attempts[1]["energy"]["available"])
        self.assertEqual(attempts[1]["measurements"], [])
        self.assertEqual(attempts[1]["parallel_context"]["label"], "parallel exploratory")
        self.assertEqual(attempts[1]["parallel_context"]["overlapping_run_ids"], ["run_02"])
        stats = result["aggregates"][0]["metrics"]["cluster_tokens_per_s"]
        self.assertEqual(stats["count"], 2)
        self.assertEqual(stats["mean"], 12.0)
        self.assertEqual(stats["ci_status"], "available_run_level")
        self.assertIn("no synthetic RPC baseline", result["baseline_policy"])
        self.assertEqual(result["cache_policy"], "reload_per_cell")

        one = simple_plan(repeat_count=1)
        one_manifest = build_sweep_manifest("single", one)
        one_manifest["trials"][0].update(status="completed", official_attempt_id="single-a")
        one_manifest["trials"][0]["attempts"] = [{"attempt_id": "single-a", "status": "completed", "run_id": "run_01"}]
        one_result = SweepResultService(self.repository, bootstrap_resamples=100).assemble(one_manifest, one)
        single_stats = one_result["aggregates"][0]["metrics"]["cluster_tokens_per_s"]
        self.assertEqual(single_stats["ci95"], [None, None])
        self.assertEqual(single_stats["ci_status"], "unavailable_single_repeat")

    def test_export_index_privacy_formula_protection_and_fixed_header(self):
        plan = simple_plan(repeat_count=1)
        manifest = build_sweep_manifest("sweep_export", plan)
        trial = manifest["trials"][0]
        trial.update(status="failed", failure_code="=HYPERLINK(\"bad\")")
        trial["attempts"] = [{"attempt_id": "bad", "status": "failed", "run_id": None, "failure_code": "=HYPERLINK(\"bad\")"}]
        service = SweepResultService(self.repository, bootstrap_resamples=100)
        result = service.assemble(manifest, plan)
        exported = service.export_json(result, plan.to_dict())
        self.assertFalse(exported["formal_approved"])
        self.assertEqual(exported["result_index"]["run_ids"], [])
        csv_text = service.export_csv(result)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(tuple(rows[0]), CSV_COLUMNS)
        self.assertTrue(rows[0]["failure_code"].startswith("'="))
        self.assertNotIn("private", csv_text)

    def test_tokenizer_rpc_node_sweep_and_historical_separation_contracts(self):
        two_model = compile_plan(
            SweepSpec.from_dict({
                "base": {"model_ref": "model-a", "prompt_ref": "same-text", "worker_ids": ["j1"]},
                "axes": [{"name": "model_ref", "values": ["model-a", "model-b"]}],
            }),
            ResolutionContext.from_dict(context_data()),
        )
        result = SweepResultService(self.repository, bootstrap_resamples=100).assemble(
            build_sweep_manifest("models", two_model), two_model
        )
        self.assertTrue(any("tokenizer" in warning.lower() for warning in result["comparison_warnings"]))

        rpc = compile_plan(SweepSpec.from_dict(rpc_spec()), ResolutionContext.from_dict(context_data()))
        rpc_result = SweepResultService(self.repository, bootstrap_resamples=100).assemble(
            build_sweep_manifest("rpc", rpc), rpc
        )
        self.assertEqual(rpc_result["trials"][0]["rpc_profile"]["coordinator_id"], "j1")
        self.assertIn(rpc_result["trials"][0]["rpc_profile"]["split_policy"], {"auto", "equal", "custom"})
        self.assertTrue(any("RPC" in warning for warning in rpc_result["comparison_warnings"]))

        node_plan = simple_plan(execution_strategy="node_sweep", repeat_count=1)
        self.assertEqual(node_plan.cells[0].workload.scenarios, 2)
        self.assertEqual(node_plan.cells[0].workload.logical_requests, 4)
        # A historical ordinary run remains an ordinary run; no sweep scanner
        # fabricates linkage merely because it exists in the same repository.
        self.repository.create("historical_01", {"model_id": "legacy.gguf"})
        self.repository.write_summary("historical_01", {"run_id": "historical_01", "status": "completed"})
        self.assertEqual(self.repository.read_summary("historical_01")["run_id"], "historical_01")


if __name__ == "__main__":
    unittest.main()
