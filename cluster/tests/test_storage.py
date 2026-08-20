from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cluster.infrastructure.storage import (
    FilesystemEnvironmentReportRepository,
    FilesystemExperimentRepository,
    FilesystemInventoryRepository,
    FilesystemJobRepository,
    FilesystemRunRepository,
    FilesystemSettingsRepository,
    FilesystemSuiteRepository,
    StorageCorruptionError,
)
from cluster.integrations.runtime_layout import resolve_runtime_paths


class FilesystemStorageTests(unittest.TestCase):
    def test_inventory_preserves_legacy_columns_and_atomic_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nodes.local.csv"
            repository = FilesystemInventoryRepository(path)
            repository.write_rows(
                [
                    {
                        "name": "legacy-head",
                        "role": "head",
                        "host": "127.0.0.1",
                        "user": "jetson",
                        "ssh_port": 22,
                        "api_port": 8000,
                        "project_dir": "/home/jetson/project/llm/local_llm_bench",
                        "enabled": True,
                        "identity_file": "",
                        "platform": "jetson",
                    }
                ]
            )
            self.assertEqual(repository.read_rows()[0]["enabled"], "true")
            self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0].split(",")[0], "name")
            self.assertFalse(list(path.parent.glob(".*.tmp-*")))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_settings_are_private_and_corruption_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            repository = FilesystemSettingsRepository(path)
            repository.write({"worker_api_auth": False, "dashboard_token_auth": True})
            self.assertEqual(repository.read()["dashboard_token_auth"], True)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(StorageCorruptionError):
                repository.read()

    def test_environment_reports_are_private_and_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemEnvironmentReportRepository(Path(directory) / "environment")
            repository.write("jetson-01", {"node": "jetson-01", "status": "ready"})
            target = Path(directory) / "environment" / "jetson-01.json"
            self.assertEqual(repository.read("jetson-01")["status"], "ready")
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            repository.delete("jetson-01")
            self.assertFalse(target.exists())

    def test_experiment_suite_and_job_repositories_skip_corrupted_listing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            experiments = FilesystemExperimentRepository(root / "experiments")
            suites = FilesystemSuiteRepository(root / "results" / "_suites")
            jobs = FilesystemJobRepository(root / "jobs")
            experiments.write("edge-test", {"experiment_id": "edge-test"})
            suites.write("suite_01", {"suite_id": "suite_01"})
            jobs.write("job_01", {"job_id": "job_01", "status": "queued"})
            (root / "experiments" / "broken.json").write_text("[]", encoding="utf-8")
            self.assertEqual(experiments.list(), [{"experiment_id": "edge-test"}])
            self.assertEqual(suites.read("suite_01")["suite_id"], "suite_01")
            self.assertEqual(jobs.list()[0]["status"], "queued")

    def test_run_repository_preserves_existing_run_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory) / "results")
            run_dir = repository.create("20260820_123456_ab12", {"node_names": ["jetson-01"]})
            repository.append_event("20260820_123456_ab12", {"type": "run_started"})
            repository.write_requests(
                "20260820_123456_ab12",
                [{"request_id": 1, "node": "jetson-01", "ok": True}],
            )
            repository.write_summary("20260820_123456_ab12", {"run_id": "20260820_123456_ab12"})
            self.assertEqual(json.loads((run_dir / "config.json").read_text())["node_names"], ["jetson-01"])
            self.assertEqual((run_dir / "events.jsonl").read_text().count("run_started"), 1)
            self.assertIn("request_id", (run_dir / "requests.csv").read_text())
            self.assertEqual(repository.read_summary("20260820_123456_ab12")["run_id"], "20260820_123456_ab12")
            self.assertEqual(repository.list_summaries()[0]["run_id"], "20260820_123456_ab12")

    def test_runtime_layout_is_rooted_once_and_overrides_remain_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / "runtime"
            paths = resolve_runtime_paths(
                {
                    "CLUSTER_RUNTIME_DIR": str(runtime),
                    "CLUSTER_INVENTORY": str(runtime / "inventory.csv"),
                    "CLUSTER_RESULTS_DIR": str(runtime / "results-override"),
                }
            )
            self.assertTrue(paths.layout.root.is_absolute())
            self.assertEqual(paths.runtime_dir, runtime)
            self.assertEqual(paths.inventory_path, runtime / "inventory.csv")
            self.assertEqual(paths.results_dir, runtime / "results-override")
            self.assertEqual(paths.settings_path, runtime / "settings.json")


if __name__ == "__main__":
    unittest.main()
