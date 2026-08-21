from __future__ import annotations

import ast
import json
import tempfile
import threading
import time
import unittest
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from cluster.application import job_process
from cluster.application.jobs import JobProcessSpec, JobService
from cluster.application.suite_runner import ExperimentRunner, SuiteRunner
from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.process import ProcessIdentity
from cluster.infrastructure.storage import FilesystemJobRepository, FilesystemSuiteRepository


class FakeInspector:
    def __init__(self) -> None:
        self.identities: dict[int, ProcessIdentity] = {}
        self.signals: list[tuple[int, int]] = []

    def inspect(self, pid: int):
        return self.identities.get(pid)

    def signal(self, expected: ProcessIdentity, signum: int) -> bool:
        observed = self.identities.get(expected.pid)
        if observed != expected:
            return False
        self.signals.append((expected.pid, signum))
        return True


def config() -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="experiment-1",
        name="durable suite",
        node_names=["worker-1"],
        model_id="models/a.gguf",
        n_ctx=128,
        n_gpu_layers=0,
        requests=1,
        concurrency=1,
        max_tokens=1,
        warmup_requests=0,
    )


class FakeExperimentRunner:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.models: list[str] = []

    def run(self, model_config, _cancel_event, progress):
        self.models.append(model_config.model_id)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        progress(
            {
                "type": "request_completed",
                "at": "2026-08-20T00:00:00+00:00",
                "run_id": outcome["run_id"],
                "completed": 1,
                "result": {"ok": True},
            }
        )
        return outcome


def run_summary(index: int, status: str = "completed") -> dict:
    return {
        "run_id": f"run_{index}",
        "suite_id": "suite_test",
        "experiment_id": "experiment-1",
        "model_id": f"models/{chr(96 + index)}.gguf",
        "model_index": index,
        "model_count": 2,
        "status": status,
    }


class SuiteRunnerTests(unittest.TestCase):
    def make_runner(self, root: Path, outcomes, unload, emit=None):
        fake = FakeExperimentRunner(outcomes)
        runner = SuiteRunner(
            fake,
            FilesystemSuiteRepository(root / "_suites"),
            unload,
            emit=emit,
        )
        return runner, fake

    def execute(
        self,
        runner: SuiteRunner,
        cancel: threading.Event,
        *,
        continue_on_error: bool = True,
        cooldown: float = 0,
    ):
        return runner.run(
            config(),
            ["models/a.gguf", "models/b.gguf"],
            "suite_test",
            continue_on_error,
            cooldown,
            cancel,
            total_work_units=2,
            per_model_work_units=1,
            started_at="2026-08-20T00:00:00+00:00",
        )

    def test_ordered_models_complete_and_cleanup_every_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unloads: list[tuple[str, ...]] = []
            runner, fake = self.make_runner(
                Path(directory),
                [run_summary(1), run_summary(2)],
                lambda names: unloads.append(tuple(names)) or [],
            )
            summary = self.execute(runner, threading.Event())
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(fake.models, ["models/a.gguf", "models/b.gguf"])
            self.assertEqual(len(unloads), 2)
            self.assertEqual(summary["completed_models"], 2)

    def test_model_error_continues_or_stops_by_policy(self) -> None:
        for continue_on_error, expected_attempts, expected_status in (
            (True, 2, "partial"),
            (False, 1, "failed"),
        ):
            with self.subTest(continue_on_error=continue_on_error), tempfile.TemporaryDirectory() as directory:
                outcomes = [RuntimeError("model failed"), run_summary(2)]
                runner, fake = self.make_runner(Path(directory), outcomes, lambda _names: [])
                summary = self.execute(
                    runner,
                    threading.Event(),
                    continue_on_error=continue_on_error,
                )
                self.assertEqual(summary["attempted_models"], expected_attempts)
                self.assertEqual(summary["status"], expected_status)
                self.assertEqual(len(fake.models), expected_attempts)

    def test_cooldown_is_cancelled_without_starting_next_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cancelled = threading.Event()

            def emit(event):
                if event["type"] == "model_cooldown":
                    cancelled.set()

            runner, fake = self.make_runner(
                Path(directory), [run_summary(1), run_summary(2)], lambda _names: [], emit
            )
            before = time.monotonic()
            summary = self.execute(runner, cancelled, cooldown=30)
            self.assertLess(time.monotonic() - before, 1.0)
            self.assertEqual(summary["status"], "cancelled")
            self.assertEqual(fake.models, ["models/a.gguf"])

    def test_cleanup_failure_is_persisted_and_stops_suite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            calls = 0

            def unload(_names):
                nonlocal calls
                calls += 1
                return ["worker-1: unload failed"] if calls == 1 else []

            runner, fake = self.make_runner(
                Path(directory), [run_summary(1), run_summary(2)], unload
            )
            summary = self.execute(runner, threading.Event())
            self.assertEqual(summary["status"], "partial")
            self.assertEqual(fake.models, ["models/a.gguf"])
            self.assertEqual(summary["models"][0]["cleanup_status"], "failed")
            self.assertEqual(summary["errors"][0]["stage"], "unload")


class JobRegistryRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.jobs = self.root / "controller" / "jobs"
        self.results = self.root / "results"
        self.inventory = self.root / "nodes.csv"
        self.project = Path(__file__).resolve().parents[2]
        self.python = Path(__import__("sys").executable)
        self.inspector = FakeInspector()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def service(self) -> JobService:
        return JobService(
            self.jobs,
            self.inventory,
            self.results,
            self.project,
            python_bin=self.python,
            inspector=self.inspector,
            start_watcher=False,
            cancel_grace_s=60,
        )

    def job(self, job_id="job_01", suite_id="suite_01", status="running"):
        spec = JobProcessSpec(
            job_id,
            self.jobs,
            self.inventory,
            self.results,
            self.project,
            self.python,
        )
        identity = ProcessIdentity(
            1234,
            str(self.python.resolve()),
            str(self.project),
            spec.argv,
            100.0,
            "tester",
        )
        return {
            "job_id": job_id,
            "suite_id": suite_id,
            "status": status,
            "phase": status,
            "process": identity.to_dict(),
            "created_at": "2026-08-20T00:00:00+00:00",
            "updated_at": "2026-08-20T00:00:00+00:00",
        }, identity

    def write_suite(self, suite_id: str, status: str) -> None:
        target = self.results / "_suites"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{suite_id}.json").write_text(
            json.dumps(
                {
                    "suite_id": suite_id,
                    "status": status,
                    "summaries": [],
                    "errors": [],
                    "completed_models": 1 if status == "completed" else 0,
                    "completed_work_units": 1,
                    "finished_at": "2026-08-20T00:01:00+00:00",
                }
            ),
            encoding="utf-8",
        )

    def test_dashboard_restart_recovers_live_running_job(self) -> None:
        job, identity = self.job()
        repository = FilesystemJobRepository(self.jobs)
        repository.write(job["job_id"], job)
        repository.append_event(
            job["job_id"],
            {"sequence": 1, "type": "request_completed", "at": "2026-08-20T00:00:01+00:00"},
        )
        self.inspector.identities[identity.pid] = identity
        first = self.service()
        second = self.service()
        self.assertEqual(first.active()["status"], "running")
        self.assertEqual(second.active()["process"]["pid"], identity.pid)
        self.assertEqual(second.active()["latest_event"]["type"], "request_completed")

    def test_restart_adopts_exact_spawned_process_before_child_claim(self) -> None:
        job, identity = self.job()
        job.pop("process")
        job["spawned_pid"] = identity.pid
        FilesystemJobRepository(self.jobs).write(job["job_id"], job)
        self.inspector.identities[identity.pid] = identity
        saved = self.service().active()
        self.assertEqual(saved["status"], "running")
        self.assertEqual(saved["process"]["pid"], identity.pid)

    def test_completed_failed_and_cancelled_are_recovered_from_suite(self) -> None:
        for suite_status, job_status in (
            ("completed", "completed"),
            ("failed", "failed"),
            ("partial", "failed"),
            ("cancelled", "cancelled"),
        ):
            with self.subTest(suite_status=suite_status):
                job_id = f"job_{suite_status}"
                suite_id = f"suite_{suite_status}"
                job, _identity = self.job(job_id, suite_id)
                FilesystemJobRepository(self.jobs).write(job_id, job)
                self.write_suite(suite_id, suite_status)
                self.service().recover()
                saved = FilesystemJobRepository(self.jobs).read(job_id)
                self.assertEqual(saved["status"], job_status)
                self.assertEqual(saved["suite_status"], suite_status)

    def test_stale_pid_becomes_orphaned(self) -> None:
        job, _identity = self.job()
        FilesystemJobRepository(self.jobs).write(job["job_id"], job)
        service = self.service()
        saved = service.active()
        self.assertEqual(saved["status"], "orphaned")
        self.assertEqual(saved["errors"][-1]["stage"], "job_recovery")

    def test_fresh_queued_spawn_gets_bounded_identity_claim_grace(self) -> None:
        job, _identity = self.job(status="queued")
        job.pop("process")
        job["spawned_pid"] = 4321
        job["created_at"] = datetime.now(timezone.utc).isoformat()
        FilesystemJobRepository(self.jobs).write(job["job_id"], job)
        saved = self.service().active()
        self.assertEqual(saved["status"], "queued")
        self.assertNotIn("orphaned_from_status", saved)

    def test_fresh_running_child_gets_same_transient_inspection_grace(self) -> None:
        job, _identity = self.job(status="running")
        job["spawned_pid"] = 4321
        job["created_at"] = datetime.now(timezone.utc).isoformat()
        FilesystemJobRepository(self.jobs).write(job["job_id"], job)
        saved = self.service().active()
        self.assertEqual(saved["status"], "running")
        self.assertNotIn("orphaned_from_status", saved)

    def test_expired_queued_spawn_without_identity_becomes_orphaned(self) -> None:
        job, _identity = self.job(status="queued")
        job.pop("process")
        job["spawned_pid"] = 4321
        FilesystemJobRepository(self.jobs).write(job["job_id"], job)
        saved = self.service().active()
        self.assertEqual(saved["status"], "orphaned")

    def test_cancel_sets_durable_request_before_any_signal_fallback(self) -> None:
        job, identity = self.job()
        FilesystemJobRepository(self.jobs).write(job["job_id"], job)
        self.inspector.identities[identity.pid] = identity
        updated = self.service().cancel()
        self.assertTrue(updated["cancel_requested"])
        self.assertEqual(updated["phase"], "cancelling")
        self.assertEqual(self.inspector.signals, [])


class JobProcessTests(unittest.TestCase):
    def test_dashboard_manager_is_only_a_durable_job_facade(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "dashboard" / "services.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        manager = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ExperimentManager"
        )
        rendered = ast.unparse(manager)
        self.assertNotIn("run_experiment", rendered)
        self.assertNotIn("threading.Thread", rendered)
        self.assertIn("JobService", rendered)

    def test_job_service_runs_outside_dashboard_and_persists_terminal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "controller" / "jobs"
            results = root / "results"
            inventory = root / "missing-nodes.csv"
            project = Path(__file__).resolve().parents[2]
            service = JobService(
                jobs,
                inventory,
                results,
                project,
                python_bin=Path(sys.executable),
                start_watcher=False,
            )
            base = config()
            job = {
                "job_id": "job_spawn",
                "suite_id": "suite_spawn",
                "status": "queued",
                "phase": "queued",
                "config": base.__dict__,
                "model_ids": ["models/a.gguf"],
                "continue_on_model_error": False,
                "model_cooldown_s": 0,
                "total": 1,
                "model_total": 1,
                "started_at": "2026-08-20T00:00:00+00:00",
            }
            started = service.start(job)
            self.assertIn("process", started)
            deadline = time.monotonic() + 10
            saved = started
            while time.monotonic() < deadline:
                saved = service.repository.read("job_spawn")
                if saved["status"] in {"completed", "failed", "cancelled", "orphaned"}:
                    break
                time.sleep(0.05)
            while time.monotonic() < deadline and service.inspector.inspect(saved["process"]["pid"]):
                time.sleep(0.02)
            service.list()
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(saved["suite_status"], "failed")
            self.assertTrue((jobs / "job_spawn.log").exists())
            self.assertEqual((jobs / "job_spawn.log").stat().st_mode & 0o777, 0o600)

    def test_child_job_process_persists_running_events_and_completed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "jobs"
            results = root / "results"
            inventory = root / "nodes.csv"
            repository = FilesystemJobRepository(jobs)
            base = config()
            repository.write(
                "job_child",
                {
                    "job_id": "job_child",
                    "suite_id": "suite_child",
                    "status": "queued",
                    "phase": "queued",
                    "config": base.__dict__,
                    "model_ids": ["models/a.gguf"],
                    "continue_on_model_error": True,
                    "model_cooldown_s": 0,
                    "total": 1,
                    "model_total": 1,
                    "started_at": "2026-08-20T00:00:00+00:00",
                },
            )

            observed_states = [repository.read("job_child")["status"]]

            def fake_benchmark(model_config, **kwargs):
                observed_states.append(repository.read("job_child")["status"])
                kwargs["progress"](
                    {
                        "type": "request_completed",
                        "at": "2026-08-20T00:00:01+00:00",
                        "run_id": "run_child",
                        "completed": 1,
                        "result": {"ok": True},
                    }
                )
                return {
                    "run_id": "run_child",
                    "suite_id": model_config.suite_id,
                    "experiment_id": model_config.experiment_id,
                    "model_id": model_config.model_id,
                    "model_index": 1,
                    "model_count": 1,
                    "status": "completed",
                }

            with mock.patch.object(job_process, "run_experiment", side_effect=fake_benchmark), mock.patch.object(
                job_process, "unload_models", return_value=[]
            ):
                exit_code = job_process.run_job(
                    "job_child", jobs, inventory, results
                )
            saved = repository.read("job_child")
            self.assertEqual(exit_code, 0)
            self.assertEqual(observed_states, ["queued", "running"])
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["suite_status"], "completed")
            self.assertTrue(repository.read_events("job_child"))
            self.assertEqual(saved["process"]["pid"], __import__("os").getpid())


if __name__ == "__main__":
    unittest.main()
