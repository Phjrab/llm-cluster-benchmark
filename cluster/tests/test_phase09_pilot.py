"""Roadmap Phase 09 pilot planning, separation, and precision gates."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from cluster.benchmark.core import research_identity
from cluster.domain.errors import DomainValidationError
from cluster.domain.experiment import ExperimentConfig
from cluster.research.pilot import (
    PilotValidationError,
    analyze_pilot,
    expand_pilot_plan,
    validate_pilot_plan,
)
from scripts.research.phase09_pilot import (
    append_retry_run,
    initial_manifest,
    manifest_execution_order,
    parser as pilot_parser,
    reconcile_pre_run_failures,
)


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "config" / "research"


def read_json(name: str) -> dict:
    return json.loads((RESEARCH / name).read_text(encoding="utf-8"))


def validate(plan: dict) -> None:
    validate_pilot_plan(
        plan,
        matrix=read_json("formal_experiment_matrix.json"),
        model_lock=read_json("model_lock.json"),
        prompt_lock=read_json("prompt_set.json"),
    )


def completed_summary(value: float = 10.0, start_temp: float = 50.0) -> dict:
    return {
        "status": "completed",
        "success_rate": 1.0,
        "wall_s": 100.0,
        "cluster_tokens_per_s": value,
        "requests_per_s": value / 10,
        "ttft_p50_s": 1 / value,
        "e2e_p50_s": 2 / value,
        "measurement_instrumentation": {
            "nodes": {
                "worker": {
                    "sample_count": 4,
                    "start_temperature_c": start_temp,
                    "peak_temperature_c": start_temp + 10,
                    "end_temperature_c": start_temp + 8,
                    "throttling_sample_count": 0,
                    "steady_state_start": 2.0,
                    "controller_collection_overhead_s": 1.0,
                    "worker_collection_overhead_samples_s": [
                        {"sampled_at": "one", "overhead_s": 0.01},
                        {"sampled_at": "two", "overhead_s": 0.01},
                    ],
                }
            }
        },
    }


def complete_observations(plan: dict, matrix: dict) -> list[dict]:
    observations = []
    for item in expand_pilot_plan(plan, matrix):
        offset = ((int(item["pilot_repeat_index"]) - 1) % 5) * 0.1
        observations.append(
            {
                "pilot_cell_id": item["pilot_cell_id"],
                "pilot_stage": item["pilot_stage"],
                "pilot_repeat_index": item["pilot_repeat_index"],
                "pilot_order_index": item["pilot_order_index"],
                "cooldown_before_s": item["cooldown_before_s"],
                "status": "completed",
                "run_id": f"run-{item['pilot_order_index']}",
                "total_elapsed_s": 125.0 + offset,
                "summary": completed_summary(10.0 + offset, 50.0 + min(offset, 1.0)),
            }
        )
    return observations


class PilotPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = read_json("pilot_plan.v3.json")
        self.matrix = read_json("formal_experiment_matrix.json")

    def test_shipped_plan_is_predeclared_separated_and_exact_matrix_subset(self) -> None:
        validate(self.plan)
        validate(read_json("pilot_plan.json"))
        validate(read_json("pilot_plan.v2.json"))
        revised = read_json("pilot_plan.v4.json")
        validate(revised)
        self.assertEqual(self.plan["status"], "predeclared")
        self.assertEqual(self.plan["pilot_version"], 3)
        self.assertEqual(
            self.plan["supersedes"]["reason_code"], "WORKER_LLAMA_CONTEXT_RACE"
        )
        self.assertFalse(self.plan["separation"]["formal_pooling_allowed"])
        self.assertFalse(self.plan["separation"]["selective_deletion_allowed"])
        self.assertEqual(revised["pilot_version"], 4)
        self.assertEqual(revised["supersedes"]["reason_code"], "TELEMETRY_INTRUSION")
        thermal_revision = read_json("pilot_plan.v5.json")
        validate(thermal_revision)
        self.assertEqual(thermal_revision["supersedes"]["reason_code"], "THERMAL_RECOVERY_RANGE")
        self.assertEqual(
            thermal_revision["thermal_policy"]["calibration_cooldown_candidates_s"],
            [60, 180, 300],
        )
        self.assertEqual(
            thermal_revision["thermal_policy"]["variance_stage_fallback_cooldown_s"],
            300,
        )
        self.assertEqual(
            revised["telemetry_policy"]["worker_collection_interval_s_by_platform"],
            {"jetson": 1.0, "raspberry-pi": 10.0},
        )

    def test_order_has_eight_calibration_and_twenty_variance_runs(self) -> None:
        first = expand_pilot_plan(self.plan, self.matrix)
        second = expand_pilot_plan(self.plan, self.matrix)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 28)
        self.assertEqual(sum(item["pilot_stage"] == "calibration" for item in first), 8)
        self.assertEqual(sum(item["pilot_stage"] == "variance" for item in first), 20)
        self.assertEqual([item["pilot_order_index"] for item in first], list(range(1, 29)))
        self.assertEqual(
            [item["cooldown_before_s"] for item in first[:4]],
            [0.0, 3.0, 15.0, 30.0],
        )

    def test_bounded_resume_keeps_stage_and_positive_attempt_limit_explicit(self) -> None:
        args = pilot_parser().parse_args(
            ["execute", "--stage", "calibration", "--confirmed", "--max-new-runs", "1"]
        )
        self.assertEqual(args.stage, "calibration")
        self.assertTrue(args.confirmed)
        self.assertEqual(args.max_new_runs, 1)
        with self.assertRaises(SystemExit):
            pilot_parser().parse_args(["execute", "--max-new-runs", "0"])

    def test_unapproved_or_non_matrix_cell_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["variance_cells"][0]["model_lock_key"] = "qwen2.5-3b-instruct-q4-k-m-official"
        with self.assertRaisesRegex(PilotValidationError, "exact formal matrix subset"):
            validate(plan)

    def test_formal_workload_drift_is_rejected(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["workload"]["requests"] = 8
        with self.assertRaisesRegex(PilotValidationError, "must equal the formal workload"):
            validate(plan)

    def test_request_level_pseudoreplication_cannot_replace_run_repeats(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["precision_policy"]["independent_unit"] = "request"
        # The independent-unit declaration is immutable, not merely descriptive.
        with self.assertRaises(PilotValidationError):
            validate_pilot_plan(
                plan,
                matrix=self.matrix,
                model_lock=read_json("model_lock.json"),
                prompt_lock=read_json("prompt_set.json"),
            )

    def test_failed_attempt_gets_one_distinct_durable_retry_slot(self) -> None:
        plan = read_json("pilot_plan.v5.json")
        declared = expand_pilot_plan(plan, self.matrix)
        manifest = initial_manifest(plan, self.matrix)
        failed_item = next(
            item for item in declared if item["pilot_order_index"] == 21
        )

        retry = append_retry_run(manifest, failed_item)
        same_retry = append_retry_run(manifest, failed_item)
        order = manifest_execution_order(declared, manifest)

        self.assertIs(retry, same_retry)
        self.assertEqual(len(manifest["runs"]), 29)
        self.assertEqual(retry["pilot_repeat_index"], 6)
        self.assertEqual(retry["pilot_order_index"], 29)
        self.assertEqual(retry["retry_of_order_index"], 21)
        self.assertEqual(order[-1]["node_set"], failed_item["node_set"])
        self.assertEqual(order[-1]["pilot_repeat_index"], 6)
        self.assertEqual(order[-1]["pilot_order_index"], 29)

    def test_existing_pre_run_failure_is_reconciled_without_deletion(self) -> None:
        plan = read_json("pilot_plan.v5.json")
        manifest = initial_manifest(plan, self.matrix)
        failed = next(
            item for item in manifest["runs"] if item["pilot_order_index"] == 21
        )
        failed.update({
            "status": "failed",
            "error_code": "PRE_RUN_CLEANUP_FAILED",
            "cleanup_errors": ["pi-worker-04: timed out"],
            "finished_at": "2026-08-25T05:44:21+00:00",
            "total_elapsed_s": 30.0,
        })

        self.assertTrue(reconcile_pre_run_failures(manifest))
        self.assertFalse(reconcile_pre_run_failures(manifest))

        self.assertEqual(len(manifest["observations"]), 1)
        self.assertEqual(
            manifest["observations"][0]["error_code"],
            "PRE_RUN_CLEANUP_FAILED",
        )
        self.assertEqual(len(manifest["runs"]), 29)
        self.assertEqual(manifest["runs"][-1]["retry_of_order_index"], 21)


class PilotIdentityTests(unittest.TestCase):
    def test_pilot_identity_is_additive_and_separate_from_formal_campaign(self) -> None:
        config = ExperimentConfig(
            node_names=["pi-worker-02"],
            model_id="model.gguf",
            execution_strategy="single_node",
            experiment_type="pilot",
            pilot_id="pilot-v1",
            pilot_cell_id="cell-v1",
            pilot_repeat_index=1,
            pilot_order_index=2,
        )
        config.validate()
        self.assertEqual(
            research_identity(config),
            {
                "experiment_type": "pilot",
                "pilot_id": "pilot-v1",
                "pilot_cell_id": "cell-v1",
                "pilot_repeat_index": 1,
                "pilot_order_index": 2,
            },
        )

    def test_pilot_and_formal_identity_cannot_mix(self) -> None:
        config = ExperimentConfig(
            node_names=["pi-worker-02"],
            model_id="model.gguf",
            execution_strategy="single_node",
            experiment_type="pilot",
            pilot_id="pilot-v1",
            pilot_cell_id="cell-v1",
            pilot_repeat_index=1,
            pilot_order_index=1,
            campaign_id="campaign-v1",
        )
        with self.assertRaisesRegex(DomainValidationError, "cannot be mixed"):
            config.validate()


class PilotAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = read_json("pilot_plan.v3.json")
        self.matrix = read_json("formal_experiment_matrix.json")

    def test_complete_low_variance_pilot_freezes_minimum_repeats_and_cooldown(self) -> None:
        result = analyze_pilot(self.plan, complete_observations(self.plan, self.matrix))
        self.assertTrue(result["freeze_ready"])
        self.assertEqual(result["observations"], 28)
        self.assertEqual(result["successful_observations"], 28)
        self.assertEqual(result["precision_decision"]["selected_formal_repeats"], 10)
        self.assertEqual(result["thermal_decision"]["selected_minimum_cooldown_s"], 3.0)
        self.assertGreater(result["median_total_run_s"], result["median_successful_run_s"])
        self.assertLessEqual(
            result["telemetry_decision"]["maximum_worker_collection_overhead_fraction_observed"],
            0.05,
        )

    def test_controller_wait_is_descriptive_but_worker_collection_overhead_blocks(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        for observation in observations:
            node = observation["summary"]["measurement_instrumentation"]["nodes"]["worker"]
            node["controller_collection_overhead_s"] = 95.0
        self.assertTrue(analyze_pilot(self.plan, observations)["freeze_ready"])

        node = observations[-1]["summary"]["measurement_instrumentation"]["nodes"]["worker"]
        node["worker_collection_overhead_samples_s"] = [
            {"sampled_at": "one", "overhead_s": 6.0}
        ]
        result = analyze_pilot(self.plan, observations)
        self.assertFalse(result["freeze_ready"])
        self.assertTrue(any("worker telemetry" in item for item in result["blockers"]))

    def test_multi_worker_overhead_uses_worst_worker_not_cluster_sum(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        summary = observations[-1]["summary"]
        template = summary["measurement_instrumentation"]["nodes"]["worker"]
        template["worker_collection_overhead_samples_s"] = [
            {"sampled_at": "one", "overhead_s": 2.0}
        ]
        summary["measurement_instrumentation"]["nodes"] = {
            f"worker-{index}": copy.deepcopy(template) for index in range(3)
        }

        result = analyze_pilot(self.plan, observations)

        self.assertTrue(result["freeze_ready"])
        self.assertEqual(
            result["telemetry_decision"]["aggregation"],
            "maximum_per_worker_per_run",
        )
        self.assertAlmostEqual(
            result["telemetry_decision"][
                "maximum_worker_collection_overhead_fraction_observed"
            ],
            0.02,
        )

        summary["measurement_instrumentation"]["nodes"]["worker-2"][
            "worker_collection_overhead_samples_s"
        ] = [{"sampled_at": "one", "overhead_s": 6.0}]
        blocked = analyze_pilot(self.plan, observations)
        self.assertFalse(blocked["freeze_ready"])
        self.assertTrue(any("worker telemetry" in item for item in blocked["blockers"]))

    def test_v4_requires_the_predeclared_platform_collection_interval(self) -> None:
        plan = read_json("pilot_plan.v4.json")
        observations = complete_observations(plan, self.matrix)
        for observation in observations:
            platform = "raspberry-pi" if "pi-" in observation["pilot_cell_id"] else "jetson"
            node = observation["summary"]["measurement_instrumentation"]["nodes"]["worker"]
            node["platform_kind"] = platform
            node["worker_collection_interval_s"] = (
                plan["telemetry_policy"]["worker_collection_interval_s_by_platform"][platform]
            )
        self.assertTrue(analyze_pilot(plan, observations)["freeze_ready"])

        pi_observation = next(
            item for item in observations if "pi-" in item["pilot_cell_id"]
        )
        pi_observation["summary"]["measurement_instrumentation"]["nodes"]["worker"][
            "worker_collection_interval_s"
        ] = 1.0
        result = analyze_pilot(plan, observations)
        self.assertFalse(result["freeze_ready"])
        self.assertTrue(any("collection interval mismatch" in item for item in result["blockers"]))

    def test_request_and_attempt_failures_are_preserved_and_gated(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        observations[-1]["summary"]["success_rate"] = 0.5
        result = analyze_pilot(self.plan, observations)
        self.assertFalse(result["freeze_ready"])
        self.assertEqual(result["failure_decision"]["maximum_request_failure_rate_observed"], 0.5)
        self.assertTrue(any("request success rate" in item for item in result["blockers"]))

    def test_incomplete_or_failed_pilot_never_opens_freeze_gate(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        observations.pop()
        observations[-1]["status"] = "failed"
        observations[-1]["summary"] = {"status": "failed", "error_code": "TIMEOUT"}
        result = analyze_pilot(self.plan, observations)
        self.assertFalse(result["freeze_ready"])
        self.assertGreater(result["failure_rate"], 0)
        self.assertTrue(any("pilot incomplete" in item for item in result["blockers"]))

    def test_distinct_retry_can_supply_five_successes_without_hiding_failure(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        observations.append({
            "pilot_cell_id": self.plan["variance_cells"][0]["pilot_cell_id"],
            "pilot_stage": "variance",
            "pilot_repeat_index": 6,
            "pilot_order_index": 29,
            "cooldown_before_s": 30.0,
            "status": "failed",
            "run_id": None,
            "error_code": "PRE_RUN_CLEANUP_FAILED",
            "summary": {"status": "failed", "error_code": "PRE_RUN_CLEANUP_FAILED"},
        })

        result = analyze_pilot(self.plan, observations)

        self.assertTrue(result["freeze_ready"])
        self.assertEqual(result["observations"], 29)
        self.assertEqual(result["successful_observations"], 28)
        self.assertAlmostEqual(result["failure_rate"], 1 / 29)
        self.assertEqual(result["failures"][0]["error_code"], "PRE_RUN_CLEANUP_FAILED")

    def test_unmeasured_cooldown_candidate_cannot_be_selected(self) -> None:
        observations = [
            item
            for item in complete_observations(self.plan, self.matrix)
            if not (
                item["pilot_cell_id"] == "calibration-pi-02-single-qwen-ko"
                and item["pilot_repeat_index"] > 1
            )
        ]
        result = analyze_pilot(self.plan, observations)
        self.assertIsNone(result["thermal_decision"]["selected_minimum_cooldown_s"])
        self.assertEqual(
            result["thermal_decision"]["candidate_pass"],
            {"3.0": False, "15.0": False, "30.0": False},
        )

    def test_high_variance_is_capped_and_reported_not_hidden(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        target = self.plan["variance_cells"][0]["pilot_cell_id"]
        values = [1.0, 20.0, 2.0, 18.0, 3.0]
        index = 0
        for observation in observations:
            if observation["pilot_cell_id"] == target:
                observation["summary"] = completed_summary(values[index])
                index += 1
        result = analyze_pilot(self.plan, observations)
        self.assertEqual(result["precision_decision"]["selected_formal_repeats"], 30)
        self.assertTrue(result["precision_decision"]["precision_limited_metrics"])


if __name__ == "__main__":
    unittest.main()
