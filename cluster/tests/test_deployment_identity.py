"""Roadmap Phase 01 Worker deployment identity contracts."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from cluster import clusterctl
from cluster.domain.deployment import DeploymentManifest
from cluster.infrastructure.deployment import (
    DEPLOYMENT_MANIFEST_RELATIVE_PATH,
    PINNED_RPC_COMMIT,
    RSYNC_EXCLUDES,
    build_deployment_manifest,
    deployment_status,
    finalize_deployment_manifest,
    read_deployment_manifest,
    verify_source_tree,
    write_deployment_manifest,
)
from cluster.research.locks import deployment_identity_issues
from cluster.worker.app import create_app
from cluster.worker.telemetry import TelemetryService
from cluster.tests.test_worker_runtime import FakeInferenceBackend, FakeTelemetry


COMMIT = "a" * 40


def make_source(root: Path) -> None:
    (root / "cluster").mkdir(parents=True)
    (root / "cluster" / "worker.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("source\n", encoding="utf-8")
    (root / "requirements-controller.txt").write_text("fastapi==1\n", encoding="utf-8")
    (root / "requirements-worker.txt").write_text("llama-cpp-python==1\n", encoding="utf-8")
    (root / "cluster" / "requirements-runtime.txt").write_text("psutil==1\n", encoding="utf-8")
    (root / ".run" / "cluster").mkdir(parents=True)
    (root / ".run" / "cluster" / "worker.token").write_text("secret", encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "large.gguf").write_bytes(b"model")
    (root / "cluster" / "__pycache__").mkdir()
    (root / "cluster" / "__pycache__" / "worker.pyc").write_bytes(b"cache")


def build_fixture(root: Path, *, deployed_at: str = "2026-08-23T00:00:00+00:00"):
    return build_deployment_manifest(
        root,
        source_commit=COMMIT,
        working_tree_clean=True,
        deployed_at=deployed_at,
    )


class DeploymentManifestUnitTests(unittest.TestCase):
    def test_tree_identity_is_deterministic_and_excludes_runtime_models_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_source(root)
            first = build_fixture(root, deployed_at="one")
            for path in root.rglob("*"):
                if path.is_file():
                    os.utime(path, (1_800_000_000, 1_800_000_000))
            second = build_fixture(root, deployed_at="two")

        self.assertEqual(first.source_tree_sha256, second.source_tree_sha256)
        self.assertNotEqual(first.deployment_manifest_sha256, second.deployment_manifest_sha256)
        paths = {item.path for item in first.source_files}
        self.assertIn("cluster/worker.py", paths)
        self.assertNotIn(".run/cluster/worker.token", paths)
        self.assertNotIn("models/large.gguf", paths)
        self.assertNotIn("cluster/__pycache__/worker.pyc", paths)

    def test_content_or_file_set_drift_fails_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_source(root)
            manifest = build_fixture(root)
            (root / "cluster" / "worker.py").write_text("VALUE = 2\n", encoding="utf-8")
            verified, detail = verify_source_tree(root, manifest)
            self.assertFalse(verified)
            self.assertIn("checksum mismatch", detail)
            (root / "cluster" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
            verified, detail = verify_source_tree(root, manifest)
            self.assertFalse(verified)
            self.assertTrue("file set mismatch" in detail or "checksum mismatch" in detail)

    def test_manifest_round_trip_is_private_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_source(root)
            manifest = build_fixture(root)
            path = root / DEPLOYMENT_MANIFEST_RELATIVE_PATH
            write_deployment_manifest(path, manifest)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(read_deployment_manifest(path), manifest)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["deployed_at"] = "tampered"
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertFalse(deployment_status(root)["verified"])
            self.assertIn("deployment_manifest_sha256", deployment_status(root)["error"])

    def test_worker_finalization_records_runtime_and_verifies_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_source(root)
            path = root / DEPLOYMENT_MANIFEST_RELATIVE_PATH
            write_deployment_manifest(path, build_fixture(root))
            with mock.patch(
                "cluster.infrastructure.deployment.probe_runtime_identity",
                return_value=("runtime-123", "0.3.20"),
            ):
                manifest = finalize_deployment_manifest(root, path)
            status = deployment_status(root)

        self.assertTrue(manifest.source_tree_verified)
        self.assertEqual(manifest.runtime_fingerprint, "runtime-123")
        self.assertEqual(manifest.llama_cpp_python_version, "0.3.20")
        self.assertTrue(status["verified"])
        self.assertEqual(status["rpc_commit"], PINNED_RPC_COMMIT)


class WorkerDeploymentHealthTests(unittest.TestCase):
    def test_health_exposes_additive_verified_deployment_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_source(root)
            path = root / DEPLOYMENT_MANIFEST_RELATIVE_PATH
            write_deployment_manifest(path, build_fixture(root))
            with mock.patch(
                "cluster.infrastructure.deployment.probe_runtime_identity",
                return_value=("runtime-123", "0.3.20"),
            ):
                finalize_deployment_manifest(root, path)
            app = create_app(
                backend=FakeInferenceBackend(),
                telemetry=TelemetryService(FakeTelemetry()),
                project_root=root,
                environment={"CLUSTER_NODE_NAME": "worker-01", "CLUSTER_PLATFORM": "raspberry-pi"},
            )
            response = TestClient(app).get("/cluster/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["deployment"]["verified"])
        self.assertTrue(payload["capabilities"]["deployment_verified"])
        self.assertEqual(payload["node"]["git_commit"], COMMIT)

    def test_health_keeps_old_worker_compatible_when_manifest_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                backend=FakeInferenceBackend(),
                telemetry=TelemetryService(FakeTelemetry()),
                project_root=Path(directory),
                environment={"CLUSTER_NODE_NAME": "worker-01", "CLUSTER_PLATFORM": "raspberry-pi"},
            )
            payload = TestClient(app).get("/cluster/health").json()

        self.assertTrue(payload["ok"])
        self.assertFalse(payload["deployment"]["available"])
        self.assertFalse(payload["capabilities"]["deployment_verified"])


class DeploymentSyncContractTests(unittest.TestCase):
    def test_sync_invalidates_old_manifest_deletes_only_unprotected_source_and_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory)
            make_source(source_root)
            manifest = build_fixture(source_root)
            node = clusterctl.Node(
                "worker-01", "worker", "192.168.0.20", "bench", 22, 8000,
                "/home/bench/llm-cluster-benchmark", True, platform="raspberry-pi",
            )
            remote_results = [
                subprocess.CompletedProcess([], 0, "", ""),  # project mkdir
                subprocess.CompletedProcess([], 0, "", ""),  # runtime mkdir
                subprocess.CompletedProcess([], 0, "", ""),  # chmod
                subprocess.CompletedProcess([], 0, "", ""),  # invalidate
                subprocess.CompletedProcess([], 0, "", ""),  # venv python exists
                subprocess.CompletedProcess(
                    [], 0,
                    json.dumps({"source_tree_verified": True, "verified": True}) + "\n",
                    "",
                ),
            ]
            rsync_results = [
                subprocess.CompletedProcess([], 0, "source-sync", ""),
                subprocess.CompletedProcess([], 0, "manifest-sync", ""),
            ]
            with mock.patch.object(clusterctl, "PROJECT_ROOT", source_root), mock.patch.object(
                clusterctl, "build_deployment_manifest", return_value=manifest
            ) as build, mock.patch.object(
                clusterctl, "run_on_node", side_effect=remote_results
            ) as remote, mock.patch.object(
                clusterctl.subprocess, "run", side_effect=rsync_results
            ) as run:
                result = clusterctl.sync_code_one(node)

        self.assertTrue(result["ok"])
        self.assertEqual(build.call_count, 2)
        source_argv = run.call_args_list[0].args[0]
        self.assertIn("--delete-delay", source_argv)
        for excluded in RSYNC_EXCLUDES:
            self.assertIn(f"--exclude={excluded}", source_argv)
        self.assertEqual(remote.call_args_list[3].args[1][:2], ["rm", "-f"])
        finalize_argv = remote.call_args_list[-1].args[1]
        self.assertEqual(finalize_argv[1:3], ["-m", "cluster.infrastructure.deployment"])


class FormalDeploymentEligibilityTests(unittest.TestCase):
    def runtime_lock(self) -> dict:
        return {
            "workers": [
                {
                    "node": "worker-01",
                    "runtime": {
                        "runtime_fingerprint": "runtime-123",
                        "llama_cpp_python": "0.3.20",
                    },
                    "deployment": {"git_commit": COMMIT},
                },
                {
                    "node": "worker-02",
                    "runtime": {
                        "runtime_fingerprint": "runtime-123",
                        "llama_cpp_python": "0.3.20",
                    },
                    "deployment": {"git_commit": COMMIT},
                },
            ]
        }

    def live(self, *, tree: str = "b" * 64) -> dict:
        return {
            "deployment": {
                "verified": True,
                "source_commit": COMMIT,
                "source_tree_sha256": tree,
                "deployment_manifest_sha256": "c" * 64,
                "runtime_fingerprint": "runtime-123",
                "llama_cpp_python_version": "0.3.20",
                "rpc_commit": PINNED_RPC_COMMIT,
            }
        }

    def test_matching_live_manifests_are_formally_accepted(self) -> None:
        issues = deployment_identity_issues(
            runtime_lock=self.runtime_lock(),
            selected_workers=["worker-01", "worker-02"],
            live_preflight_snapshot={"worker-01": self.live(), "worker-02": self.live()},
        )
        self.assertEqual(issues, [])

    def test_missing_tampered_or_cross_worker_drift_fails_closed(self) -> None:
        missing = deployment_identity_issues(
            runtime_lock=self.runtime_lock(),
            selected_workers=["worker-01"],
            live_preflight_snapshot={},
        )
        drift = deployment_identity_issues(
            runtime_lock=self.runtime_lock(),
            selected_workers=["worker-01", "worker-02"],
            live_preflight_snapshot={
                "worker-01": self.live(tree="d" * 64),
                "worker-02": self.live(tree="e" * 64),
            },
        )
        unverified = deployment_identity_issues(
            runtime_lock=self.runtime_lock(),
            selected_workers=["worker-01"],
            live_preflight_snapshot={"worker-01": {"deployment": {"verified": False}}},
        )

        self.assertIn("SOURCE_FINGERPRINT_MISSING", {item["code"] for item in missing})
        self.assertIn("SOURCE_FINGERPRINT_MISMATCH", {item["code"] for item in drift})
        self.assertIn("SOURCE_FINGERPRINT_UNVERIFIED", {item["code"] for item in unverified})


if __name__ == "__main__":
    unittest.main()
