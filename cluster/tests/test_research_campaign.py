"""Roadmap Phase 06 durable campaign scheduling and recovery gates."""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cluster.integrations.campaign_jobs import CampaignJobDocumentFactory, DurableJobRunBackend
from cluster.benchmark.core import research_identity
from cluster.benchmark.persistence import RunPersistence
from cluster.domain.experiment import ExperimentConfig
from cluster.research.campaign import (
    CampaignRepository,
    CampaignRunner,
    CampaignStateError,
    CampaignValidationError,
    build_campaign_manifest,
)
from cluster.research.eligibility import assess_campaign_cell
from cluster.research.matrix import expand_formal_matrix
from cluster.research.scheduler import (
    coverage_from_cells,
    seeded_randomized_block_order,
)


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "config" / "research"
FIXED = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
FORMAL_MODEL_IDS = {
    "qwen2.5-1.5b-instruct-q4-k-m-official": (
        "qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    ),
    "granite-3.3-2b-instruct-q4-k-m-official": (
        "granite-3.3-2b/granite-3.3-2b-instruct-Q4_K_M.gguf"
    ),
}


def read_json(name: str) -> dict:
    return json.loads((RESEARCH / name).read_text(encoding="utf-8"))


def base_cell(cell_id: str, *, block: str = "a", cohort: str = "pi") -> dict:
    return {
        "cell_id": cell_id,
        "experiment_type": "formal",
        "platform_cohort": cohort,
        "strategy": "single_node",
        "node_set_id": "pi-02",
        "node_set": ["pi-worker-02"],
        "node_count": 1,
        "model_lock_key": "model-a",
        "prompt_set_version": 1,
        "prompt_id": "prompt-a",
        "parameter_profile": "profile-a",
        "order_block": block,
        "measurement_quality_policy": "quality-a",
    }


def runner_manifest(cell_count: int = 2, cooldown_s: float = 0) -> dict:
    cells = []
    for index in range(cell_count):
        cells.append(
            {
                **base_cell(f"cell-{index + 1}"),
                "campaign_cell_id": f"cell-{index + 1}--repeat-01",
                "repeat_index": 1,
                "order_index": index + 1,
                "status": "pending",
                "attempt_id": None,
                "run_id": None,
                "failure_code": None,
                "measurement_quality": None,
                "attempts": [],
                "warnings": [],
                "drift": [],
            }
        )
    return {
        "schema_version": 1,
        "campaign_version": 1,
        "artifact_type": "formal_campaign",
        "campaign_id": "campaign-test",
        "matrix_id": "fixture",
        "matrix_version": 1,
        "lock_ref": {"lock_sha256": "fixture"},
        "experiment_type": "formal",
        "status": "ready",
        "phase": "ready",
        "created_at": FIXED.isoformat(),
        "updated_at": FIXED.isoformat(),
        "order_seed": 7,
        "repeat_count": 1,
        "repeat_count_decision_evidence": "fixture",
        "controller_participant_policy": "forbidden",
        "model_ids": {"model-a": "models/a.gguf"},
        "retry_policy": {
            "automatic": False,
            "manual_retry_requires_reason": True,
            "preserve_every_attempt": True,
        },
        "cooldown_policy": {
            "minimum_cooldown_s": cooldown_s,
            "stabilization_rule_source": "fixture",
        },
        "estimates": {
            "runs": cell_count,
            "nominal_runtime_seconds": 1,
            "timeout_envelope_seconds": 1,
            "storage_bytes": 1,
        },
        "coverage": coverage_from_cells(cells),
        "current_cell_id": None,
        "pause_requested": False,
        "cancel_requested": False,
        "cooldown_not_before": None,
        "last_drift": None,
        "cells": cells,
    }


def passing_gate(_cell):
    return {"eligible": True, "blocking_issues": [], "warnings": []}


class FakeBackend:
    def __init__(self, starts=None) -> None:
        self.starts = list(starts or [])
        self.start_calls: list[str] = []
        self.inspect_results: dict[str, dict] = {}
        self.cancel_calls: list[str] = []
        self.lock = threading.Lock()

    def start(self, cell, attempt):
        with self.lock:
            self.start_calls.append(cell["campaign_cell_id"])
            result = self.starts.pop(0) if self.starts else {"status": "running"}
            result = dict(result)
            result.setdefault("backend_job_id", f"job-{len(self.start_calls)}")
            return result

    def inspect(self, attempt):
        return self.inspect_results.get(
            attempt["backend_job_id"], {"status": attempt.get("status", "running")}
        )

    def cancel(self, attempt):
        self.cancel_calls.append(str(attempt.get("backend_job_id")))
        return {"status": "cancelling", "backend_job_id": attempt.get("backend_job_id")}


class FrozenClock:
    def __init__(self) -> None:
        self.value = FIXED

    def __call__(self):
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class SchedulerTests(unittest.TestCase):
    def test_seeded_randomized_blocks_are_deterministic_and_complete(self) -> None:
        cells = [
            base_cell("a1", block="a"),
            base_cell("a2", block="a"),
            base_cell("b1", block="b"),
            base_cell("j1", block="j", cohort="jetson"),
        ]
        first = seeded_randomized_block_order(cells, repeat_count=3, seed=20260823)
        second = seeded_randomized_block_order(cells, repeat_count=3, seed=20260823)
        changed = seeded_randomized_block_order(cells, repeat_count=3, seed=20260824)
        self.assertEqual(first, second)
        self.assertNotEqual(
            [item["campaign_cell_id"] for item in first],
            [item["campaign_cell_id"] for item in changed],
        )
        self.assertEqual(len(first), 12)
        self.assertEqual([item["order_index"] for item in first], list(range(1, 13)))
        self.assertEqual({item["repeat_index"] for item in first}, {1, 2, 3})
        for repeat in (1, 2, 3):
            selected = [item for item in first if item["repeat_index"] == repeat]
            positions = [index for index, item in enumerate(selected) if item["order_block"] == "a"]
            self.assertEqual(positions, list(range(min(positions), max(positions) + 1)))

    def test_coverage_reports_pending_and_terminal_states(self) -> None:
        self.assertEqual(
            coverage_from_cells(
                [{"status": "pending"}, {"status": "completed"}, {"status": "failed"}]
            ),
            {
                "planned": 3,
                "pending": 1,
                "running": 0,
                "completed": 1,
                "failed": 1,
                "cancelled": 0,
                "excluded": 0,
            },
        )


class FormalManifestTests(unittest.TestCase):
    def inputs(self):
        return {
            "matrix": read_json("formal_experiment_matrix.json"),
            "protocol": read_json("experiment_protocol.json"),
            "model_lock": read_json("model_lock.json"),
            "prompt_lock": read_json("prompt_set.json"),
            "runtime_lock": read_json("runtime_lock.json"),
            "experiment_conditions": read_json("experiment_conditions.json"),
        }

    def test_shipped_formal_campaign_is_blocked_until_phase_09_pilot(self) -> None:
        values = self.inputs()
        self.assertEqual(values["matrix"]["execution_gate"]["blocking_phases"], [9])
        with self.assertRaisesRegex(CampaignValidationError, r"phase\(s\) 9"):
            build_campaign_manifest(
                campaign_id="formal-v1-test",
                repeat_count=10,
                repeat_count_decision_evidence="not-yet-frozen",
                model_ids=FORMAL_MODEL_IDS,
                **values,
            )

    def test_opened_fixture_gate_builds_complete_traceable_manifest(self) -> None:
        values = self.inputs()
        values["matrix"] = copy.deepcopy(values["matrix"])
        values["protocol"] = copy.deepcopy(values["protocol"])
        values["matrix"]["execution_gate"] = {
            "formal_execution_allowed": True,
            "blocking_phases": [],
            "reason": "pilot fixture",
        }
        values["protocol"]["thermal_policy"].update(
            {
                "formal_start_blocked_until_frozen": False,
                "final_stabilization_rule_source": "phase-09-pilot-fixture",
            }
        )
        manifest = build_campaign_manifest(
            campaign_id="formal-v1-test",
            repeat_count=10,
            repeat_count_decision_evidence="phase-09-pilot-report:fixture",
            model_ids=FORMAL_MODEL_IDS,
            created_at=FIXED.isoformat(),
            **values,
        )
        self.assertEqual(len(manifest["cells"]), 720)
        self.assertEqual(manifest["coverage"]["pending"], 720)
        self.assertEqual(manifest["coverage"]["completed"], 0)
        self.assertEqual(manifest["estimates"]["runs"], 720)
        self.assertFalse(manifest["retry_policy"]["automatic"])
        self.assertEqual(len({item["campaign_cell_id"] for item in manifest["cells"]}), 720)


class RepositoryTests(unittest.TestCase):
    def test_manifest_and_events_are_atomic_private_and_listable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest())
            repository.update(
                "campaign-test",
                lambda value: value.update({"phase": "checked"}),
                event={"type": "checked", "at": FIXED.isoformat()},
            )
            target = Path(directory) / "campaigns" / "campaign-test"
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual((target / "manifest.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((target / "events.jsonl").stat().st_mode & 0o777, 0o600)
            self.assertEqual(repository.list()[0]["phase"], "checked")
            self.assertEqual(repository.read_events("campaign-test")[-1]["type"], "checked")
            with self.assertRaises(CampaignStateError):
                repository.create(runner_manifest())

    def test_symlinked_campaign_path_is_rejected_without_writing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            campaigns = root / "campaigns"
            campaigns.mkdir()
            (campaigns / "campaign-test").symlink_to(outside, target_is_directory=True)
            repository = CampaignRepository(campaigns)
            with self.assertRaisesRegex(CampaignStateError, "symbolic link"):
                repository.create(runner_manifest())
            self.assertEqual(list(outside.iterdir()), [])


class RunnerLifecycleTests(unittest.TestCase):
    def make(self, repository, backend, *, clock=None, preflight=passing_gate, thermal=passing_gate):
        return CampaignRunner(
            repository,
            backend,
            preflight,
            thermal,
            start_recovery_grace_s=0,
            now=clock,
        )

    def test_restart_resumes_job_and_completed_cells_never_run_twice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest())
            backend = FakeBackend()
            first = self.make(repository, backend, clock=lambda: FIXED)
            running = first.tick("campaign-test")
            self.assertEqual(running["coverage"]["running"], 1)
            self.assertEqual(backend.start_calls, ["cell-1--repeat-01"])

            backend.inspect_results["job-1"] = {
                "status": "completed",
                "run_id": "run-1",
                "measurement_quality": "clean",
            }
            restarted = self.make(repository, backend, clock=lambda: FIXED)
            resumed = restarted.tick("campaign-test")
            self.assertEqual(resumed["coverage"]["completed"], 1)
            self.assertEqual(backend.start_calls, ["cell-1--repeat-01"])

            restarted.tick("campaign-test")
            self.assertEqual(backend.start_calls[-1], "cell-2--repeat-01")
            backend.inspect_results["job-2"] = {
                "status": "completed",
                "run_id": "run-2",
            }
            restarted.tick("campaign-test")
            done = restarted.tick("campaign-test")
            self.assertEqual(done["status"], "completed")
            for _ in range(3):
                restarted.tick("campaign-test")
            self.assertEqual(backend.start_calls.count("cell-1--repeat-01"), 1)
            self.assertEqual(backend.start_calls.count("cell-2--repeat-01"), 1)
            self.assertEqual(
                [item["run_id"] for item in done["cells"]], ["run-1", "run-2"]
            )

    def test_two_runner_instances_cannot_double_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest(cell_count=1))
            backend = FakeBackend()
            runners = [self.make(repository, backend, clock=lambda: FIXED) for _ in range(2)]
            barrier = threading.Barrier(3)
            errors = []

            def run(candidate):
                try:
                    barrier.wait()
                    candidate.tick("campaign-test")
                except Exception as exc:  # pragma: no cover - assertion evidence
                    errors.append(exc)

            threads = [threading.Thread(target=run, args=(candidate,)) for candidate in runners]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(backend.start_calls, ["cell-1--repeat-01"])

    def test_drift_pauses_without_start_and_pi_history_warning_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest(cell_count=1))
            backend = FakeBackend()

            def blocked(_cell):
                return {
                    "eligible": False,
                    "blocking_issues": [{"code": "MODEL_SHA_MISMATCH"}],
                    "warnings": [{"code": "PI_POWER_HISTORY", "blocking": False}],
                }

            state = self.make(repository, backend, clock=lambda: FIXED, preflight=blocked).tick(
                "campaign-test"
            )
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["phase"], "drift_blocked")
            self.assertEqual(backend.start_calls, [])
            self.assertEqual(state["cells"][0]["status"], "pending")
            self.assertEqual(state["cells"][0]["attempts"][0]["status"], "blocked")

            self.make(repository, backend, clock=lambda: FIXED).resume("campaign-test")
            state = self.make(repository, backend, clock=lambda: FIXED).tick("campaign-test")
            self.assertEqual(state["coverage"]["running"], 1)

    def test_failure_requires_manual_reason_and_preserves_attempt_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest(cell_count=1))
            backend = FakeBackend(starts=[{"status": "failed", "failure_code": "RUN_FAILED"}])
            runner = self.make(repository, backend, clock=lambda: FIXED)
            failed = runner.tick("campaign-test")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(len(failed["cells"][0]["attempts"]), 1)
            runner.tick("campaign-test")
            self.assertEqual(len(backend.start_calls), 1)
            with self.assertRaisesRegex(CampaignStateError, "requires a reason"):
                runner.retry_cell("campaign-test", "cell-1--repeat-01", reason="")
            runner.retry_cell("campaign-test", "cell-1--repeat-01", reason="operator review")
            backend.starts.append({"status": "completed", "run_id": "retry-run"})
            done = runner.tick("campaign-test")
            self.assertEqual(done["status"], "completed")
            self.assertEqual(len(done["cells"][0]["attempts"]), 2)
            self.assertEqual(done["cells"][0]["run_id"], "retry-run")

    def test_pause_cancel_cleanup_failure_and_cooldown_are_durable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest(cooldown_s=10))
            backend = FakeBackend()
            clock = FrozenClock()
            runner = self.make(repository, backend, clock=clock)
            runner.tick("campaign-test")
            pausing = runner.pause("campaign-test")
            self.assertTrue(pausing["pause_requested"])
            backend.inspect_results["job-1"] = {"status": "completed", "run_id": "run-1"}
            paused = runner.tick("campaign-test")
            self.assertEqual(paused["status"], "paused")
            runner.resume("campaign-test")
            clock.advance(9)
            runner.tick("campaign-test")
            self.assertEqual(len(backend.start_calls), 1)
            clock.advance(1)
            runner.tick("campaign-test")
            self.assertEqual(len(backend.start_calls), 2)
            cancelling = runner.cancel("campaign-test")
            self.assertTrue(cancelling["cancel_requested"])
            self.assertEqual(backend.cancel_calls, ["job-2"])
            backend.inspect_results["job-2"] = {
                "status": "completed",
                "run_id": "run-2",
                "cleanup_status": "failed",
            }
            cancelled = runner.tick("campaign-test")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["cells"][1]["failure_code"], "CLEANUP_FAILED")

    def test_uncertain_inspection_never_launches_replacement(self) -> None:
        class Uncertain(FakeBackend):
            def inspect(self, _attempt):
                raise TimeoutError("unknown remote state")

        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory) / "campaigns")
            repository.create(runner_manifest(cell_count=1))
            backend = Uncertain()
            runner = self.make(repository, backend, clock=lambda: FIXED)
            runner.tick("campaign-test")
            for _ in range(3):
                state = runner.tick("campaign-test")
            self.assertEqual(state["coverage"]["running"], 1)
            self.assertEqual(len(backend.start_calls), 1)
            self.assertEqual(
                repository.read_events("campaign-test")[-1]["type"],
                "campaign_attempt_inspection_uncertain",
            )


class EligibilityTests(unittest.TestCase):
    def test_pi_history_is_warning_but_missing_or_active_evidence_blocks(self) -> None:
        matrix = read_json("formal_experiment_matrix.json")
        cell = next(
            item
            for item in expand_formal_matrix(matrix)
            if item["node_set"] == ["pi-worker-02"]
        )
        locks = {
            "experiment_conditions": read_json("experiment_conditions.json"),
            "model_lock": read_json("model_lock.json"),
            "prompt_lock": read_json("prompt_set.json"),
            "runtime_lock": read_json("runtime_lock.json"),
        }
        model = next(
            item for item in locks["model_lock"]["models"]
            if item["model_key"] == cell["model_lock_key"]
        )
        worker = next(
            item for item in locks["runtime_lock"]["workers"]
            if item["node"] == "pi-worker-02"
        )
        deployment = worker["deployment"]
        snapshot = {
            "pi-worker-02": {
                "model_sha256": model["binary"]["sha256"],
                "backend_verified": True,
                "ntp_synchronized": True,
                "storage_sufficient": True,
                "deployment": {
                    "verified": True,
                    "source_commit": deployment["git_commit"],
                    "source_tree_sha256": "a" * 64,
                    "deployment_manifest_sha256": "b" * 64,
                    "runtime_fingerprint": worker["runtime"]["runtime_fingerprint"],
                    "llama_cpp_python_version": worker["runtime"]["llama_cpp_python"],
                    "rpc_commit": "f49e9178767d557a522618b16ce8694f9ddac628",
                },
                "power_integrity": {"status": "history_warning", "history_bits": ["undervoltage"]},
            }
        }
        result = assess_campaign_cell(
            cell=cell,
            manifest={"lock_ref": matrix["lock_ref"]},
            live_preflight_snapshot=snapshot,
            **locks,
        )
        self.assertTrue(result["eligible"])
        self.assertIn("PI_POWER_HISTORY", {item["code"] for item in result["warnings"]})
        snapshot["pi-worker-02"]["power_integrity"] = {
            "status": "active_degraded",
            "active_fault_bits": ["undervoltage"],
        }
        active = assess_campaign_cell(
            cell=cell,
            manifest={"lock_ref": matrix["lock_ref"]},
            live_preflight_snapshot=snapshot,
            **locks,
        )
        self.assertFalse(active["eligible"])
        self.assertIn("PI_POWER_ACTIVE", {item["code"] for item in active["blocking_issues"]})


class DurableJobAdapterTests(unittest.TestCase):
    def test_document_factory_maps_frozen_cell_to_one_traceable_job(self) -> None:
        matrix = read_json("formal_experiment_matrix.json")
        model_lock = read_json("model_lock.json")
        prompt_lock = read_json("prompt_set.json")
        runtime_lock = read_json("runtime_lock.json")
        conditions = read_json("experiment_conditions.json")
        cell = {
            **next(iter(expand_formal_matrix(matrix))),
            "campaign_cell_id": "formal-cell--repeat-01",
            "repeat_index": 1,
            "order_index": 1,
            "measurement_quality_policy": "jetson-primary-clean-v1",
        }
        factory = CampaignJobDocumentFactory(
            campaign_id="formal-v1-test",
            matrix=matrix,
            model_lock=model_lock,
            prompt_lock=prompt_lock,
            runtime_lock=runtime_lock,
            experiment_conditions=conditions,
            model_ids={
                "qwen2.5-1.5b-instruct-q4-k-m-official": (
                    "qwen2.5-1.5b/qwen2.5-1.5b-instruct-q4_k_m.gguf"
                )
            },
        )
        job = factory(cell, {"attempt_id": "attempt_aabbcc"})
        self.assertEqual(job["model_count"], 1)
        self.assertFalse(job["continue_on_model_error"])
        self.assertEqual(job["campaign_cell_id"], "formal-cell--repeat-01")
        self.assertEqual(job["config"]["experiment_type"], "formal")
        self.assertEqual(job["config"]["campaign_attempt_id"], "attempt_aabbcc")
        self.assertEqual(job["config"]["prompt"], prompt_lock["prompts"][0]["text"])
        self.assertEqual(job["config"]["n_gpu_layers"], 30)

        config = ExperimentConfig.from_dict(job["config"])
        config.validate()
        identity = research_identity(config)
        self.assertEqual(identity["campaign_id"], "formal-v1-test")
        self.assertEqual(identity["campaign_attempt_id"], "attempt_aabbcc")
        with tempfile.TemporaryDirectory() as directory:
            persistence = RunPersistence(Path(directory), "run_campaign_trace", config)
            event = persistence.emit("phase", phase="fixture")
            self.assertEqual(event["campaign_cell_id"], "formal-cell--repeat-01")
            self.assertEqual(event["campaign_attempt_id"], "attempt_aabbcc")

        legacy = ExperimentConfig(node_names=["pi-worker-02"])
        legacy.validate()
        self.assertIsNone(research_identity(legacy))

        wrong = CampaignJobDocumentFactory(
            campaign_id="formal-v1-test",
            matrix=matrix,
            model_lock=model_lock,
            prompt_lock=prompt_lock,
            runtime_lock=runtime_lock,
            experiment_conditions=conditions,
            model_ids={
                "qwen2.5-1.5b-instruct-q4-k-m-official": "models/wrong.gguf"
            },
        )
        with self.assertRaisesRegex(ValueError, "does not match locked GGUF"):
            wrong(cell, {"attempt_id": "attempt_aabbcc"})

    def test_adapter_maps_durable_job_recovery_cleanup_and_cancel(self) -> None:
        class Repository:
            def __init__(self):
                self.job = {}

            def read(self, _job_id):
                return self.job

        class Service:
            def __init__(self):
                self.repository = Repository()
                self.cancelled = False

            def start(self, document):
                self.repository.job = {**document, "status": "queued"}
                return self.repository.job

            def recover(self):
                return []

            def active(self):
                return self.repository.job

            def cancel(self):
                self.cancelled = True
                return {**self.repository.job, "status": "cancelled"}

        service = Service()
        adapter = DurableJobRunBackend(
            service, lambda _cell, _attempt: {"job_id": "job-1", "status": "queued"}
        )
        started = adapter.start({}, {})
        self.assertEqual(started["backend_job_id"], "job-1")
        service.repository.job.update(
            {
                "status": "completed",
                "summary": {
                    "summaries": [{"run_id": "run-1"}],
                    "models": [{"cleanup_status": "failed"}],
                },
            }
        )
        inspected = adapter.inspect({"backend_job_id": "job-1"})
        self.assertEqual(inspected["run_id"], "run-1")
        self.assertEqual(inspected["cleanup_status"], "failed")
        adapter.cancel({"backend_job_id": "job-1"})
        self.assertTrue(service.cancelled)


if __name__ == "__main__":
    unittest.main()
