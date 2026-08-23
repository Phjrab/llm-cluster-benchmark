"""Roadmap Phase 07 campaign, comparison, and readiness projections."""

from __future__ import annotations

import json
import importlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from cluster.dashboard.research_views import (
    campaign_detail,
    campaign_overview,
    compare_payload,
    normalize_compare_run,
    research_readiness,
)
from cluster.research.campaign import CampaignRepository


def campaign_manifest() -> dict:
    cells = [
        {
            "campaign_cell_id": "cell-1--repeat-01",
            "order_index": 1,
            "repeat_index": 1,
            "status": "completed",
            "platform_cohort": "pi5-cohort",
            "strategy": "single_node",
            "node_set": ["pi-worker-02"],
            "node_count": 1,
            "model_lock_key": "model-a",
            "prompt_id": "prompt-a",
            "measurement_quality": "clean",
            "failure_code": None,
            "run_id": "run-1",
            "attempt_id": "attempt-1",
            "attempts": [{"attempt_id": "attempt-1", "run_id": "run-1"}],
            "warnings": [],
            "drift": [],
        },
        {
            "campaign_cell_id": "cell-2--repeat-01",
            "order_index": 2,
            "repeat_index": 1,
            "status": "pending",
            "platform_cohort": "pi5-cohort",
            "strategy": "replicated_round_robin",
            "node_set": ["pi-worker-02", "pi-worker-03"],
            "node_count": 2,
            "model_lock_key": "model-a",
            "prompt_id": "prompt-a",
            "measurement_quality": None,
            "failure_code": None,
            "run_id": None,
            "attempt_id": None,
            "attempts": [],
            "warnings": [],
            "drift": [],
        },
    ]
    return {
        "schema_version": 1,
        "campaign_version": 1,
        "artifact_type": "formal_campaign",
        "campaign_id": "campaign-ui-test",
        "matrix_id": "matrix-v1",
        "matrix_version": 1,
        "lock_ref": {"lock_sha256": "fixture"},
        "experiment_type": "formal",
        "status": "running",
        "phase": "measurement",
        "created_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T01:00:00+00:00",
        "order_seed": 42,
        "repeat_count": 1,
        "repeat_count_decision_evidence": "fixture",
        "controller_participant_policy": "forbidden",
        "model_ids": {"model-a": "models/a.gguf"},
        "retry_policy": {
            "automatic": False,
            "manual_retry_requires_reason": True,
            "preserve_every_attempt": True,
        },
        "cooldown_policy": {"minimum_cooldown_s": 3, "stabilization_rule_source": "fixture"},
        "estimates": {
            "runs": 2,
            "nominal_runtime_seconds": 200,
            "timeout_envelope_seconds": 400,
            "storage_bytes": 1_000,
        },
        "coverage": {
            "planned": 2,
            "pending": 1,
            "running": 0,
            "completed": 1,
            "failed": 0,
            "cancelled": 0,
            "excluded": 0,
        },
        "current_cell_id": None,
        "pause_requested": False,
        "cancel_requested": False,
        "cooldown_not_before": None,
        "last_drift": None,
        "cells": cells,
    }


class CampaignProjectionTests(unittest.TestCase):
    def test_progress_repeat_result_coverage_and_estimate_are_projected(self) -> None:
        value = campaign_overview(campaign_manifest())
        self.assertEqual(value["progress_pct"], 50.0)
        self.assertEqual(value["result_coverage"], {"linked_runs": 1, "planned_runs": 2})
        self.assertEqual(value["repeat_progress"], {"completed": 0, "total": 1})
        self.assertEqual(value["estimated_remaining"]["cells"], 1)
        self.assertEqual(value["estimated_remaining"]["runtime_seconds"], 100.0)
        self.assertTrue(value["formal_eligible"])

    def test_blocking_drift_marks_campaign_ineligible(self) -> None:
        manifest = campaign_manifest()
        manifest["last_drift"] = {
            "blocking_issues": [{"code": "SOURCE_FINGERPRINT_MISMATCH", "blocking": True}]
        }
        value = campaign_overview(manifest)
        self.assertFalse(value["formal_eligible"])
        self.assertEqual(value["blocking_drift_count"], 1)

    def test_detail_keeps_bounded_events_and_cell_quality(self) -> None:
        detail = campaign_detail(
            campaign_manifest(),
            [{"type": "tick", "index": index} for index in range(220)],
        )
        self.assertEqual(len(detail["events"]), 200)
        self.assertEqual(detail["cells"][0]["measurement_quality"], "clean")
        self.assertEqual(detail["cells"][0]["run_id"], "run-1")

    def test_repository_manifest_can_feed_projection_without_write_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = CampaignRepository(Path(directory))
            repository.create(campaign_manifest())
            values = [campaign_overview(item) for item in repository.list()]
            self.assertEqual([item["campaign_id"] for item in values], ["campaign-ui-test"])


class CompareProjectionTests(unittest.TestCase):
    def test_formal_run_exposes_every_filter_dimension_and_metrics(self) -> None:
        run = {
            "run_id": "run-formal",
            "status": "completed",
            "finished_at": "2026-08-23T01:00:00Z",
            "model_id": "models/a.gguf",
            "execution_strategy": "replicated_round_robin",
            "nodes": ["pi-worker-02", "pi-worker-03"],
            "research_identity": {
                "campaign_id": "campaign-a",
                "campaign_cell_id": "cell-a",
                "experiment_type": "formal",
                "model_lock_entry": "model-a",
                "platform_cohort": "pi5-ubuntu",
            },
            "participant_nodes": [
                {
                    "name": "pi-worker-02",
                    "detected_platform": "raspberry-pi",
                    "runtime_backend": {"runtime_fingerprint": "pi-runtime"},
                },
                {
                    "name": "pi-worker-03",
                    "detected_platform": "raspberry-pi",
                    "runtime_backend": {"runtime_fingerprint": "pi-runtime"},
                },
            ],
            "measurement_quality": "warning",
            "cluster_tokens_per_s": 8.5,
            "ttft_p50_s": 0.7,
            "e2e_p95_s": 4.2,
            "success_rate": 1.0,
        }
        value = normalize_compare_run(run)
        self.assertEqual(value["campaign_id"], "campaign-a")
        self.assertEqual(value["model_lock"], "model-a")
        self.assertEqual(value["platform"], "raspberry-pi")
        self.assertEqual(value["node_count"], 2)
        self.assertEqual(value["runtime_fingerprint"], "pi-runtime")
        self.assertEqual(value["measurement_quality"], "warning")
        self.assertEqual(value["metrics"]["throughput_tokens_s"], 8.5)
        self.assertFalse(value["legacy_fallback"])

    def test_legacy_result_is_visible_with_explicit_unknown_fallbacks(self) -> None:
        value = normalize_compare_run(
            {
                "run_id": "run-old",
                "model_id": "old.gguf",
                "status": "completed",
                "execution_strategy": "single_node",
                "nodes": ["old-worker"],
                "cluster_tokens_per_s": 2,
            }
        )
        self.assertTrue(value["legacy_fallback"])
        self.assertEqual(value["campaign_id"], "uncampaigned")
        self.assertEqual(value["model_lock"], "unlocked")
        self.assertEqual(value["platform"], "unknown")
        self.assertEqual(value["measurement_quality"], "unknown")

    def test_compare_payload_builds_filter_catalog_and_newest_first(self) -> None:
        payload = compare_payload(
            [
                {"run_id": "old", "finished_at": "2026-08-22T00:00:00Z", "status": "failed"},
                {
                    "run_id": "new",
                    "finished_at": "2026-08-23T00:00:00Z",
                    "status": "completed",
                    "research_identity": {"campaign_id": "campaign-a", "model_lock_entry": "model-a"},
                    "participant_nodes": [{"detected_platform": "jetson", "power_mode": "MAXN"}],
                    "nodes": ["jetson-worker-01"],
                    "execution_strategy": "single_node",
                    "measurement_quality": "clean",
                },
            ]
        )
        self.assertEqual(payload["runs"][0]["run_id"], "new")
        self.assertIn("campaign-a", payload["filters"]["campaigns"])
        self.assertIn("MAXN", payload["filters"]["power_modes"])
        self.assertEqual(payload["legacy_run_count"], 1)


class ResearchReadinessTests(unittest.TestCase):
    def inputs(self) -> dict:
        return {
            "model_lock": {
                "models": [
                    {
                        "model_key": "approved",
                        "display_name": "Approved",
                        "binary": {"sha256": "a" * 64},
                        "license": {"acceptance_required": False},
                        "verification": {"status": "approved", "verified_workers": ["worker-1"]},
                    },
                    {
                        "model_key": "gated",
                        "display_name": "Gated",
                        "license": {
                            "spdx_or_name": "Research License",
                            "acceptance_required": True,
                            "accepted_for_this_project": False,
                        },
                        "verification": {"status": "source_locked"},
                    },
                ]
            },
            "runtime_lock": {
                "controller": {"git_commit": "c" * 40},
                "workers": [
                    {
                        "node": "worker-1",
                        "platform": "jetson",
                        "deployment": {"git_commit": "d" * 40},
                        "runtime": {"runtime_fingerprint": "runtime-a"},
                        "power": {"mode": "MAXN"},
                    }
                ],
            },
            "matrix": {"execution_gate": {"formal_execution_allowed": True, "blocking_phases": []}},
            "live_status": [
                {
                    "name": "worker-1",
                    "api": True,
                    "profile": {
                        "deployment": {"git_commit": "d" * 40},
                        "runtime_backend": {"runtime_fingerprint": "runtime-a", "verified": True},
                    },
                    "power": {"current": {"name": "MAXN"}},
                }
            ],
            "environment": [{"node": "worker-1", "status": "ready", "backend": {"verified": True}}],
            "controller_commit": "c" * 40,
        }

    def test_readiness_separates_model_license_source_runtime_power_and_instrumentation(self) -> None:
        value = research_readiness(**self.inputs())
        self.assertFalse(value["eligible"])
        self.assertEqual(value["model_counts"], {"approved": 1, "total": 2})
        self.assertEqual(value["license_blockers"][0]["model_key"], "gated")
        worker = value["workers"][0]
        self.assertEqual(worker["source_identity"], "match")
        self.assertEqual(worker["runtime_identity"], "match")
        self.assertEqual(worker["power_identity"], "match")
        self.assertTrue(worker["instrumentation_ready"])

    def test_runtime_source_power_and_phase_drift_are_explicit_blockers(self) -> None:
        values = self.inputs()
        values["model_lock"]["models"] = [values["model_lock"]["models"][0]]
        values["matrix"]["execution_gate"] = {
            "formal_execution_allowed": False,
            "blocking_phases": [9],
        }
        values["live_status"][0]["profile"]["deployment"]["git_commit"] = "e" * 40
        values["live_status"][0]["profile"]["runtime_backend"]["runtime_fingerprint"] = "runtime-b"
        values["live_status"][0]["power"]["current"]["name"] = "15W"
        value = research_readiness(**values)
        worker = value["workers"][0]
        self.assertEqual(worker["source_identity"], "drift")
        self.assertEqual(worker["runtime_identity"], "drift")
        self.assertEqual(worker["power_identity"], "drift")
        self.assertIn(9, value["execution_gate"]["blocking_phases"])
        self.assertFalse(value["eligible"])


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("pydantic"),
    "dashboard runtime dependencies are not installed",
)
class ResearchRouteTests(unittest.TestCase):
    @staticmethod
    def load_dashboard(root: Path):
        inventory = root / "nodes.csv"
        inventory.write_text(
            "name,role,host,user,ssh_port,api_port,project_dir,enabled,identity_file,platform\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ,
            {
                "CLUSTER_INVENTORY": str(inventory),
                "CLUSTER_RESULTS_DIR": str(root / "results"),
                "CLUSTER_RUNTIME_DIR": str(root / "runtime"),
            },
        ), mock.patch.object(threading.Thread, "start", return_value=None):
            if "cluster.dashboard.app" in sys.modules:
                return importlib.reload(sys.modules["cluster.dashboard.app"])
            return importlib.import_module("cluster.dashboard.app")

    def test_additive_research_routes_and_legacy_result_fallback(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "results" / "legacy_run"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "legacy_run",
                        "status": "completed",
                        "model_id": "legacy.gguf",
                        "execution_strategy": "single_node",
                        "nodes": ["old-worker"],
                        "cluster_tokens_per_s": 1.5,
                    }
                ),
                encoding="utf-8",
            )
            dashboard = self.load_dashboard(root)
            with TestClient(dashboard.app) as client:
                campaigns = client.get("/api/campaigns")
                compared = client.get("/api/research/compare")
                readiness = client.get("/api/research/readiness")
            self.assertEqual(campaigns.status_code, 200)
            self.assertEqual(campaigns.json()["campaigns"], [])
            self.assertEqual(compared.status_code, 200)
            self.assertEqual(compared.json()["runs"][0]["run_id"], "legacy_run")
            self.assertTrue(compared.json()["runs"][0]["legacy_fallback"])
            self.assertEqual(readiness.status_code, 200)
            self.assertIn("approved_models", readiness.json())

    def test_unknown_campaign_is_404_without_creating_artifacts(self) -> None:
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dashboard = self.load_dashboard(root)
            with TestClient(dashboard.app) as client:
                response = client.get("/api/campaigns/missing-campaign")
            self.assertEqual(response.status_code, 404)
            self.assertFalse((root / "runtime" / "controller" / "campaigns").exists())


if __name__ == "__main__":
    unittest.main()
