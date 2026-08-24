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
        self.plan = read_json("pilot_plan.v2.json")
        self.matrix = read_json("formal_experiment_matrix.json")

    def test_shipped_plan_is_predeclared_separated_and_exact_matrix_subset(self) -> None:
        validate(self.plan)
        validate(read_json("pilot_plan.json"))
        self.assertEqual(self.plan["status"], "predeclared")
        self.assertEqual(self.plan["pilot_version"], 2)
        self.assertEqual(
            self.plan["supersedes"]["reason_code"], "DEPLOYMENT_SOURCE_DRIFT"
        )
        self.assertFalse(self.plan["separation"]["formal_pooling_allowed"])
        self.assertFalse(self.plan["separation"]["selective_deletion_allowed"])

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
        self.plan = read_json("pilot_plan.v2.json")
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
            result["telemetry_decision"]["maximum_controller_overhead_fraction_observed"],
            0.05,
        )

    def test_incomplete_or_failed_pilot_never_opens_freeze_gate(self) -> None:
        observations = complete_observations(self.plan, self.matrix)
        observations.pop()
        observations[-1]["status"] = "failed"
        observations[-1]["summary"] = {"status": "failed", "error_code": "TIMEOUT"}
        result = analyze_pilot(self.plan, observations)
        self.assertFalse(result["freeze_ready"])
        self.assertGreater(result["failure_rate"], 0)
        self.assertTrue(any("pilot incomplete" in item for item in result["blockers"]))

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
