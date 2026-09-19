from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cluster.application.jobs import JobService
from cluster.application.resources import (
    FilesystemResourceCoordinator,
    ResourceConflictError,
    ResourceRegistryCorruptionError,
    WorkerResource,
)
from cluster.worker.ownership import WorkerOwnershipError, WorkerOwnershipRegistry
from cluster.infrastructure.storage import FilesystemJobRepository


def _process_acquire(
    directory: str,
    owner: str,
    workers: list[tuple[str, str, int]],
    barrier: multiprocessing.Barrier,
    output: multiprocessing.Queue,
    *,
    exclusive: bool = False,
) -> None:
    coordinator = FilesystemResourceCoordinator(Path(directory))
    barrier.wait(timeout=10)
    try:
        lease = coordinator.acquire(
            owner_job_id=owner,
            owner_attempt_id=owner + "-attempt",
            workers=[WorkerResource(*item) for item in workers],
            hold_reason="process-test",
            admission_limit=2,
            exclusive=exclusive,
        )
    except Exception as exc:
        output.put((owner, "error", type(exc).__name__, str(exc)))
    else:
        output.put((owner, "ok", lease["lease_id"], lease["fencing_epoch"]))


class ProcessReservationTests(unittest.TestCase):
    def run_race(self, left, right, *, left_exclusive=False):
        with tempfile.TemporaryDirectory() as directory:
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            output = context.Queue()
            processes = [
                context.Process(
                    target=_process_acquire,
                    args=(directory, "job-a", left, barrier, output),
                    kwargs={"exclusive": left_exclusive},
                ),
                context.Process(
                    target=_process_acquire,
                    args=(directory, "job-b", right, barrier, output),
                ),
            ]
            for process in processes:
                process.start()
            results = [output.get(timeout=15), output.get(timeout=15)]
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)
            return results

    def test_overlapping_worker_sets_are_atomic_across_processes(self) -> None:
        results = self.run_race(
            [("a", "10.0.0.1", 8000), ("b", "10.0.0.2", 8000)],
            [("b", "10.0.0.2", 8000), ("c", "10.0.0.3", 8000)],
        )
        self.assertEqual([item[1] for item in results].count("ok"), 1)
        self.assertEqual([item[1] for item in results].count("error"), 1)

    def test_disjoint_worker_sets_both_fit_opt_in_cap(self) -> None:
        results = self.run_race(
            [("a", "10.0.0.1", 8000), ("b", "10.0.0.2", 8000)],
            [("c", "10.0.0.3", 8000), ("d", "10.0.0.4", 8000)],
        )
        self.assertEqual([item[1] for item in results].count("ok"), 2)

    def test_formal_and_ordinary_start_race_has_one_winner(self) -> None:
        results = self.run_race(
            [("formal-a", "10.0.0.1", 8000)],
            [("ordinary-b", "10.0.0.2", 8000)],
            left_exclusive=True,
        )
        self.assertEqual([item[1] for item in results].count("ok"), 1)


class ReservationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.coordinator = FilesystemResourceCoordinator(self.root / "resources")
        self.a = WorkerResource("a", "worker.local", 8000)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def acquire(self, owner: str = "job-a", **kwargs):
        return self.coordinator.acquire(
            owner_job_id=owner,
            owner_attempt_id=owner + "-attempt",
            workers=kwargs.pop("workers", [self.a]),
            hold_reason="test",
            admission_limit=kwargs.pop("admission_limit", 2),
            **kwargs,
        )

    def test_aliases_with_same_physical_endpoint_are_rejected(self) -> None:
        with self.assertRaisesRegex(ResourceConflictError, "DUPLICATE_PHYSICAL_ENDPOINT"):
            self.acquire(
                workers=[
                    WorkerResource("alias-a", "same-host", 8000),
                    WorkerResource("alias-b", "same-host", 8000),
                ]
            )

    def test_rpc_ports_are_host_scoped(self) -> None:
        first = self.coordinator.acquire(
            owner_job_id="rpc-a", owner_attempt_id="attempt-a",
            workers=[WorkerResource("a", "host-a", 8000)], hold_reason="rpc",
            admission_limit=2, rpc_coordinator_id="a",
        )
        second = self.coordinator.acquire(
            owner_job_id="rpc-b", owner_attempt_id="attempt-b",
            workers=[WorkerResource("b", "host-b", 8000)], hold_reason="rpc",
            admission_limit=2, rpc_coordinator_id="b",
        )
        self.assertNotEqual(first["resources"], second["resources"])

    def test_model_deletion_action_conflicts_with_running_job(self) -> None:
        self.acquire()
        with self.assertRaisesRegex(ResourceConflictError, "RESOURCE_BUSY"):
            self.coordinator.acquire(
                owner_job_id="action-delete", owner_attempt_id="action-delete",
                workers=[self.a], hold_reason="worker_mutation:delete-models",
                admission_limit=2, kind="action",
            )

    def test_cleanup_failure_and_heartbeat_loss_quarantine_without_ttl_release(self) -> None:
        lease = self.acquire()
        quarantined = self.coordinator.finish(
            lease_id=lease["lease_id"], owner_job_id="job-a",
            fencing_epoch=lease["fencing_epoch"], cleanup_verified=False,
            evidence={"rpc_cleanup": "failed"},
        )
        self.assertEqual(quarantined["status"], "quarantined")
        with self.assertRaisesRegex(ResourceConflictError, "RESOURCE_BUSY"):
            self.acquire("job-b")
        reconciled = self.coordinator.reconcile(
            lease_id=lease["lease_id"], fencing_epoch=lease["fencing_epoch"],
            evidence={"operator_check": "process_and_ports_absent"},
        )
        self.assertEqual(reconciled["status"], "released")
        newer = self.acquire("job-b")
        self.assertEqual(newer["status"], "held")

    def test_cooldown_holds_resource_and_old_owner_cannot_release_new_owner(self) -> None:
        old = self.acquire()
        with self.assertRaises(ResourceConflictError):
            self.acquire("job-b")
        self.coordinator.finish(
            lease_id=old["lease_id"], owner_job_id="job-a",
            fencing_epoch=old["fencing_epoch"], cleanup_verified=True,
            evidence={"cooldown": "completed"},
        )
        new = self.acquire("job-b")
        replay = self.coordinator.finish(
            lease_id=old["lease_id"], owner_job_id="job-a",
            fencing_epoch=old["fencing_epoch"], cleanup_verified=True,
            evidence={"late_release": True},
        )
        self.assertEqual(replay["status"], "released")
        active_ids = {item["lease_id"] for item in self.coordinator.active()}
        self.assertIn(new["lease_id"], active_ids)

    def test_stale_heartbeat_is_quarantined_and_registry_corruption_fails_closed(self) -> None:
        lease = self.acquire()
        changed = self.coordinator.quarantine_stale(heartbeat_before="9999-01-01T00:00:00+00:00")
        self.assertEqual([item["lease_id"] for item in changed], [lease["lease_id"]])
        self.assertEqual(self.coordinator.active()[0]["status"], "quarantined")
        corrupt_root = self.root / "corrupt"
        corrupt = FilesystemResourceCoordinator(corrupt_root)
        corrupt.registry_path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(ResourceRegistryCorruptionError):
            corrupt.active()

    def test_duplicate_acquire_is_idempotent(self) -> None:
        first = self.acquire()
        second = self.acquire()
        self.assertEqual(first["lease_id"], second["lease_id"])
        self.assertFalse(second["_acquired_new"])

    def test_cli_mutation_observes_dashboard_reservation_without_remote_access(self) -> None:
        runtime = self.root / "runtime"
        jobs = runtime / "controller" / "jobs"
        coordinator = FilesystemResourceCoordinator(jobs / "_resources")
        coordinator.acquire(
            owner_job_id="dashboard-job", owner_attempt_id="dashboard-attempt",
            workers=[WorkerResource("worker-a", "192.168.10.10", 8000)],
            hold_reason="dashboard", admission_limit=1,
        )
        inventory = self.root / "nodes.csv"
        inventory.write_text(
            "name,role,host,user,ssh_port,api_port,project_dir,enabled,platform\n"
            "worker-a,worker,192.168.10.10,tester,22,8000,/home/tester/worker,true,jetson\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable, "-m", "cluster.clusterctl", "--inventory", str(inventory),
                "--node", "worker-a", "doctor",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "CLUSTER_RUNTIME_DIR": str(runtime)},
            text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("RESOURCE_BUSY", result.stdout)


class WorkerOwnershipTests(unittest.TestCase):
    def test_stale_owner_cannot_mutate_or_release_new_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = WorkerOwnershipRegistry(Path(directory) / "owner.json")
            old = {"owner_id": "job-a", "lease_id": "lease_" + "a" * 32, "fencing_epoch": 1}
            new = {"owner_id": "job-b", "lease_id": "lease_" + "b" * 32, "fencing_epoch": 2}
            registry.acquire(old)
            registry.release(old, cleanup_verified=True)
            registry.acquire(new)
            with self.assertRaisesRegex(WorkerOwnershipError, "STALE_OR_MISSING"):
                registry.require(old)
            with self.assertRaisesRegex(WorkerOwnershipError, "STALE_RESOURCE_OWNER"):
                registry.release(old, cleanup_verified=True)
            registry.require(new)


class JobStartIdempotencyTests(unittest.TestCase):
    class Child:
        pid = 424242

        def poll(self):
            return None

    def test_duplicate_job_start_spawns_one_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = JobService(
                root / "jobs", root / "missing.csv", root / "results",
                Path(__file__).resolve().parents[2], start_watcher=False,
            )
            document = {
                "job_id": "job-idempotent", "suite_id": "suite-idempotent",
                "status": "queued", "phase": "queued", "nodes": ["worker-a"],
            }
            with mock.patch("cluster.application.jobs.subprocess.Popen", return_value=self.Child()) as popen:
                first = service.start(document)
                second = service.start(document)
            self.assertEqual(first["job_id"], second["job_id"])
            self.assertEqual(popen.call_count, 1)

    def test_spawn_failure_releases_pre_spawn_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = JobService(
                root / "jobs", root / "missing.csv", root / "results",
                Path(__file__).resolve().parents[2], start_watcher=False,
            )
            document = {
                "job_id": "job-spawn-fail", "suite_id": "suite-spawn-fail",
                "status": "queued", "phase": "queued", "nodes": ["worker-a"],
            }
            with mock.patch("cluster.application.jobs.subprocess.Popen", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    service.start(document)
            self.assertEqual(service.resources.active(), [])

    def test_targeted_cancel_and_pause_leave_disjoint_job_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = JobService(
                root / "jobs", root / "missing.csv", root / "results",
                Path(__file__).resolve().parents[2], start_watcher=False,
            )
            repository = FilesystemJobRepository(root / "jobs")
            for job_id, node in (("job-a", "worker-a"), ("job-b", "worker-b")):
                repository.write(
                    job_id,
                    {
                        "job_id": job_id, "suite_id": "suite-" + job_id,
                        "status": "running", "phase": "suite", "nodes": [node],
                    },
                )
            with mock.patch.object(service, "recover", return_value=[]), mock.patch.object(
                service, "_schedule_cancel_fallback"
            ):
                with self.assertRaisesRegex(ValueError, "AMBIGUOUS_ACTIVE_JOB"):
                    service.cancel()
                paused = service.pause("job-a")
                self.assertTrue(paused["pause_requested"])
                service.resume("job-a")
                cancelled = service.cancel("job-a")
            self.assertTrue(cancelled["cancel_requested"])
            self.assertFalse(repository.read("job-b").get("cancel_requested", False))
            self.assertEqual(repository.read("job-b")["status"], "running")

    def test_crash_after_spawn_quarantines_instead_of_freeing_by_pid_or_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            coordinator = FilesystemResourceCoordinator(root / "jobs" / "_resources")
            lease = coordinator.acquire(
                owner_job_id="job-crash", owner_attempt_id="job-crash",
                workers=[WorkerResource("worker-a", "worker-a", 8000)],
                hold_reason="test", admission_limit=1,
            )
            lease.pop("_acquired_new", None)
            FilesystemJobRepository(root / "jobs").write(
                "job-crash",
                {
                    "job_id": "job-crash", "suite_id": "suite-crash",
                    "status": "running", "phase": "suite", "nodes": ["worker-a"],
                    "spawned_pid": 999999,
                    "created_at": "2020-01-01T00:00:00+00:00",
                    "resource_reservation": lease,
                },
            )
            JobService(
                root / "jobs", root / "missing.csv", root / "results",
                Path(__file__).resolve().parents[2], start_watcher=False,
                identity_retry_s=0,
            )
            saved = FilesystemJobRepository(root / "jobs").read("job-crash")
            self.assertEqual(saved["status"], "orphaned")
            self.assertEqual(coordinator.active()[0]["status"], "quarantined")


if __name__ == "__main__":
    unittest.main()
