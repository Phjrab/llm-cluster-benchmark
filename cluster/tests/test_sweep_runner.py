"""S06 durable supervisor tests use only cached plans and injected fake jobs."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.application.sweep_models import preview_catalog_sweep
from cluster.application.sweep_runner import (
    SweepJobDocumentFactory,
    SweepRepository,
    SweepStateError,
    SweepSupervisor,
)
from cluster.domain.sweep import SweepSpec
from cluster.tests.test_sweep_models import TEXT, fixtures, preview
from cluster.tests.test_sweep_rpc import rpc_preview


def no_drift(_plan, _trial):
    return {"blocking_issues": [], "observed_at": "fake"}


class FakeBackend:
    def __init__(self) -> None:
        self.jobs = {}
        self.starts = []
        self.cancels = []
        self.lose_next_response = False
        self.inspect_error = None

    def start(self, _plan, trial, attempt):
        job_id = attempt["backend_job_id"]
        self.starts.append((trial.trial_id, attempt["attempt_id"], job_id))
        self.jobs.setdefault(job_id, {
            "status": "running", "backend_job_id": job_id, "run_id": None,
            "cleanup_status": "held", "finished_at": None,
        })
        if self.lose_next_response:
            self.lose_next_response = False
            raise ConnectionError("synthetic response loss")
        return dict(self.jobs[job_id])

    def inspect(self, attempt):
        if self.inspect_error is not None:
            raise self.inspect_error
        job_id = attempt["backend_job_id"]
        if job_id not in self.jobs:
            raise FileNotFoundError(job_id)
        return dict(self.jobs[job_id])

    def cancel(self, attempt):
        job_id = attempt["backend_job_id"]
        self.cancels.append(job_id)
        self.jobs[job_id].update(
            status="cancelled", cleanup_status="released", finished_at="fake-finished"
        )
        return dict(self.jobs[job_id])

    def finish(self, job_id, status="completed", cleanup="released"):
        self.jobs[job_id].update(
            status=status,
            cleanup_status=cleanup,
            run_id="run_" + job_id[-8:],
            finished_at="fake-finished",
            failure_code="SYNTHETIC_OOM" if status == "failed" else None,
        )


def parallel_plan(*, failure_policy="continue_ready"):
    data = fixtures()
    base = data["spec"].base.to_dict()
    data["spec"] = SweepSpec.from_dict({
        "base": base,
        "combination": "explicit",
        "explicit": [
            {**base, "model_ref": "a", "worker_ids": ["j1"], "n_ctx": 128, "concurrency": 1},
            {**base, "model_ref": "b", "worker_ids": ["j2"], "n_ctx": 256, "concurrency": 2},
        ],
        "execution": {
            "mode": "disjoint_parallel", "max_parallel_jobs": 2,
            "backfill_policy": "bounded", "backfill_window": 8,
            "failure_policy": failure_policy,
        },
    })
    return preview_catalog_sweep(**data).plan


class DurableSweepSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = SweepRepository(Path(self.temporary.name) / "sweeps")
        self.backend = FakeBackend()
        self.supervisor = SweepSupervisor(self.repository, self.backend, no_drift)

    def test_claim_response_loss_restart_and_completed_trial_never_relaunch(self):
        plan = preview().plan
        self.supervisor.create("sweep_restart", plan)
        self.backend.lose_next_response = True
        running = self.supervisor.tick("sweep_restart")
        self.assertEqual(running["coverage"]["running"], 1)
        self.assertEqual(len(self.backend.starts), 1)
        attempt_id = self.backend.starts[0][1]
        job_id = self.backend.starts[0][2]

        restarted = SweepSupervisor(self.repository, self.backend, no_drift)
        restarted.tick("sweep_restart")
        self.assertEqual(len(self.backend.starts), 1)
        self.backend.finish(job_id)
        completed_one = restarted.tick("sweep_restart")
        first = completed_one["trials"][0]
        self.assertEqual(first["official_attempt_id"], attempt_id)
        restarted.tick("sweep_restart")
        self.assertEqual(len(self.backend.starts), 2)
        self.assertEqual(len({item[2] for item in self.backend.starts}), 2)

    def test_crash_after_claim_reuses_same_attempt_and_job_id(self):
        plan = preview().plan
        self.supervisor.create("sweep_claim", plan)
        claimed = self.supervisor._claim("sweep_claim", plan.trials[0].trial_id)
        attempt = claimed["trials"][0]["attempts"][0]
        restarted = SweepSupervisor(self.repository, self.backend, no_drift)
        restarted.tick("sweep_claim")
        self.assertEqual(self.backend.starts[0][1], attempt["attempt_id"])
        self.assertEqual(self.backend.starts[0][2], attempt["backend_job_id"])

    def test_inspection_uncertainty_and_unreleased_cleanup_pause(self):
        plan = preview().plan
        self.supervisor.create("sweep_uncertain", plan)
        running = self.supervisor.tick("sweep_uncertain")
        self.backend.inspect_error = OSError("synthetic uncertain process state")
        uncertain = self.supervisor.tick("sweep_uncertain")
        self.assertEqual(uncertain["status"], "paused")
        self.assertEqual(uncertain["trials"][0]["status"], "needs_reconciliation")

        other = SweepSupervisor(self.repository, self.backend, no_drift)
        other.create("sweep_cleanup", plan)
        self.backend.inspect_error = None
        active = other.tick("sweep_cleanup")
        job_id = active["trials"][0]["attempts"][0]["backend_job_id"]
        self.backend.finish(job_id, cleanup="quarantined")
        held = other.tick("sweep_cleanup")
        self.assertEqual(held["status"], "paused")
        self.assertEqual(held["phase"], "needs_reconciliation")

    def test_pause_at_safe_boundary_cancel_scope_and_manual_retry_history(self):
        plan = preview().plan
        self.supervisor.create("sweep_pause", plan)
        active = self.supervisor.tick("sweep_pause")
        job_id = active["trials"][0]["attempts"][0]["backend_job_id"]
        pausing = self.supervisor.pause("sweep_pause", reason="operator thermal check")
        self.assertEqual(pausing["phase"], "pausing")
        self.backend.finish(job_id)
        paused = self.supervisor.tick("sweep_pause")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(len(self.backend.starts), 1)
        self.supervisor.resume("sweep_pause")
        second = self.supervisor.tick("sweep_pause")
        second_job = second["trials"][1]["attempts"][0]["backend_job_id"]
        cancelled = self.supervisor.cancel("sweep_pause", reason="operator stopped selected sweep")
        self.assertEqual(self.backend.cancels, [second_job])
        cancelled = self.supervisor.tick("sweep_pause")
        self.assertEqual(cancelled["status"], "cancelled")

        retried = self.supervisor.retry(
            "sweep_pause", plan.trials[1].trial_id, reason="manual inspection cleared"
        )
        self.assertEqual(retried["trials"][1]["status"], "pending")
        self.supervisor.tick("sweep_pause")
        attempts = self.repository.read("sweep_pause")["trials"][1]["attempts"]
        self.assertEqual(len(attempts), 2)
        self.assertNotEqual(attempts[0]["attempt_id"], attempts[1]["attempt_id"])
        self.assertEqual(attempts[1]["retry_of_attempt_id"], attempts[0]["attempt_id"])
        self.assertEqual(attempts[1]["retry_reason"], "manual inspection cleared")

    def test_disjoint_parallel_overlap_and_continue_ready(self):
        plan = parallel_plan()
        self.supervisor.create("sweep_parallel", plan)
        active = self.supervisor.tick("sweep_parallel")
        self.assertEqual(active["coverage"]["running"], 2)
        self.assertEqual(len(self.backend.starts), 2)
        first_job, second_job = [item[2] for item in self.backend.starts]
        self.backend.finish(first_job, status="failed")
        after_failure = self.supervisor.tick("sweep_parallel")
        self.assertEqual(after_failure["coverage"]["running"], 1)
        self.assertEqual(after_failure["trials"][0]["failure_code"], "SYNTHETIC_OOM")
        self.backend.finish(second_job)
        final = self.supervisor.tick("sweep_parallel")
        self.assertEqual(final["status"], "partial")
        self.assertEqual(len(self.backend.starts), 2)

        same_pool = preview().plan
        raw = same_pool.spec.to_dict()
        raw["execution"] = {
            "mode": "disjoint_parallel", "max_parallel_jobs": 2,
            "backfill_policy": "bounded", "backfill_window": 8,
        }
        data = fixtures()
        data["spec"] = SweepSpec.from_dict(raw)
        serial_plan = preview_catalog_sweep(**data).plan
        fresh = FakeBackend()
        serial = SweepSupervisor(self.repository, fresh, no_drift)
        serial.create("sweep_overlap", serial_plan)
        value = serial.tick("sweep_overlap")
        self.assertEqual(value["coverage"]["running"], 1)
        self.assertEqual(len(fresh.starts), 1)
        self.assertTrue(value["scheduling"]["overlap_decisions"])
        self.assertIn(
            "worker:j1",
            value["scheduling"]["overlap_decisions"][0]["overlap_resource_ids"],
        )

    def test_drift_blocks_before_claim_and_does_not_download_or_relock(self):
        plan = preview().plan
        calls = []
        def drift(_plan, trial):
            calls.append(trial.trial_id)
            return {"blocking_issues": [{"code": "MODEL_CHECKSUM_DRIFT"}]}
        supervisor = SweepSupervisor(self.repository, self.backend, drift)
        supervisor.create("sweep_drift", plan)
        blocked = supervisor.tick("sweep_drift")
        self.assertEqual(blocked["status"], "paused")
        self.assertEqual(self.backend.starts, [])
        self.assertEqual(calls, [plan.trials[0].trial_id])

    def test_rpc_context_model_concurrency_compound_plan_uses_one_model_children(self):
        plan, _templates = rpc_preview()
        factory = SweepJobDocumentFactory(lambda _plan, _trial: TEXT)
        first = plan.trials[0]
        attempt = {
            "sweep_id": "compound", "attempt_id": "attempt_compound",
            "suite_id": "suite_compound", "backend_job_id": "job_compound",
        }
        document = factory(plan, first, attempt)
        config = document["config"]
        self.assertEqual(len(document["model_ids"]), 1)
        self.assertEqual(config["model_count"], 1)
        self.assertEqual(config["execution_strategy"], "model_parallel_rpc")
        self.assertEqual(config["concurrency"], plan.cells[0].condition.concurrency)
        self.assertEqual(config["n_ctx"], plan.cells[0].condition.n_ctx)
        self.assertEqual(config["sweep"]["trial_id"], first.trial_id)
        self.assertEqual(config["sweep"]["rpc_profile"]["profile_id"], "two-auto-all")

        self.supervisor.create("sweep_compound", plan)
        current = self.supervisor.tick("sweep_compound")
        while current["status"] not in {"completed", "partial", "failed", "cancelled"}:
            for trial in current["trials"]:
                if trial["status"] == "running":
                    self.backend.finish(trial["attempts"][-1]["backend_job_id"])
            # Recreate on every boundary to model Dashboard/supervisor restart.
            current = SweepSupervisor(self.repository, self.backend, no_drift).tick("sweep_compound")
        self.assertEqual(current["status"], "completed")
        self.assertEqual(len(self.backend.starts), len(plan.trials))
        self.assertEqual(len({item[2] for item in self.backend.starts}), len(plan.trials))

    def test_repository_rejects_manifest_tamper(self):
        plan = preview().plan
        manifest = self.supervisor.create("sweep_tamper", plan)
        changed = copy.deepcopy(manifest)
        changed["plan_sha256"] = "0" * 64
        with self.assertRaises(SweepStateError):
            self.repository._write("sweep_tamper", changed)

    def test_manifest_failure_before_write_has_no_run_and_after_write_is_recoverable(self):
        plan = preview().plan
        with mock.patch(
            "cluster.application.sweep_runner.atomic_write_text",
            side_effect=OSError("synthetic manifest write failure"),
        ):
            with self.assertRaises(OSError):
                self.supervisor.create("sweep_before_write", plan)
        self.assertEqual(self.backend.starts, [])
        with self.assertRaises(SweepStateError):
            self.repository.read("sweep_before_write")

        original_append = self.repository._append
        with mock.patch.object(
            self.repository, "_append", side_effect=OSError("synthetic event write failure")
        ):
            with self.assertRaises(OSError):
                self.supervisor.create("sweep_after_write", plan)
        recovered = self.repository.read("sweep_after_write")
        self.assertEqual(recovered["status"], "ready")
        self.assertEqual(self.backend.starts, [])
        original_append("sweep_after_write", {"type": "synthetic_recovered"})


if __name__ == "__main__":
    unittest.main()
