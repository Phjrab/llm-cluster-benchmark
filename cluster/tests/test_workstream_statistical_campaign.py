from __future__ import annotations

import json
from pathlib import Path
import unittest

from cluster.research.scheduler import (
    ScheduleValidationError,
    schedule_condition_order,
)
from cluster.research.statistical_campaign import (
    StatisticalCampaignError,
    aggregate_campaign_attempts,
    aggregate_run_summaries,
    describe_samples,
)


def cell(cell_id: str, *, platform: str = "jetson", block: str = "default") -> dict:
    return {
        "cell_id": cell_id,
        "platform_cohort": platform,
        "order_block": block,
    }


class ConditionOrderTests(unittest.TestCase):
    def test_fixed_order_repeats_original_sequence(self) -> None:
        scheduled = schedule_condition_order(
            [cell("b"), cell("a")], repeat_count=2, policy="fixed"
        )
        self.assertEqual([item["cell_id"] for item in scheduled], ["b", "a", "b", "a"])
        self.assertEqual([item["order_index"] for item in scheduled], [1, 2, 3, 4])
        self.assertTrue(all(item["order_seed"] is None for item in scheduled))

    def test_randomized_order_is_reproducible_for_seed(self) -> None:
        cells = [cell("a"), cell("b"), cell("c")]
        first = schedule_condition_order(
            cells, repeat_count=3, policy="randomized", seed=17
        )
        second = schedule_condition_order(
            cells, repeat_count=3, policy="randomized", seed=17
        )
        changed = schedule_condition_order(
            cells, repeat_count=3, policy="randomized", seed=18
        )
        self.assertEqual(first, second)
        self.assertNotEqual(
            [item["cell_id"] for item in first],
            [item["cell_id"] for item in changed],
        )
        self.assertEqual({item["order_seed"] for item in first}, {17})

    def test_latin_square_rotates_within_platform_blocks(self) -> None:
        scheduled = schedule_condition_order(
            [cell("a"), cell("b"), cell("c")],
            repeat_count=3,
            policy="latin_square",
        )
        rows = [
            [item["cell_id"] for item in scheduled if item["repeat_index"] == repeat]
            for repeat in (1, 2, 3)
        ]
        self.assertEqual(rows, [["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]])
        self.assertTrue(all(item["latin_square_cycle_complete"] for item in scheduled))
        incomplete = schedule_condition_order(
            [cell("a"), cell("b"), cell("c")],
            repeat_count=2,
            policy="latin_square",
        )
        self.assertFalse(any(item["latin_square_cycle_complete"] for item in incomplete))

    def test_order_validation_rejects_unknown_policy_and_duplicates(self) -> None:
        with self.assertRaisesRegex(ScheduleValidationError, "policy"):
            schedule_condition_order([cell("a")], repeat_count=1, policy="shuffle")
        with self.assertRaisesRegex(ScheduleValidationError, "duplicate"):
            schedule_condition_order(
                [cell("a"), cell("a")], repeat_count=1, policy="fixed"
            )


class StatisticalAggregateTests(unittest.TestCase):
    def attempts(self) -> list[dict]:
        return [
            {
                "condition_id": "jetson-scale",
                "attempt_id": "attempt-1",
                "run_id": "run-1",
                "repeat_index": 1,
                "status": "completed",
                "measurement_quality": "clean",
                "platform_families": ["jetson"],
                "metrics": {"tokens_per_s": 10.0, "ttft_s": 0.5},
            },
            {
                "condition_id": "jetson-scale",
                "attempt_id": "attempt-2",
                "run_id": "run-2",
                "repeat_index": 2,
                "status": "failed",
                "failure_code": "MODEL_LOAD_OOM",
                "measurement_quality": "degraded",
                "platform_families": ["jetson"],
                "metrics": {},
            },
            {
                "condition_id": "jetson-scale",
                "attempt_id": "attempt-3",
                "run_id": "run-3",
                "repeat_index": 3,
                "status": "completed",
                "measurement_quality": "warning",
                "platform_families": ["jetson"],
                "metrics": {"tokens_per_s": 14.0},
            },
            {
                "condition_id": "mixed-exploratory",
                "attempt_id": "attempt-4",
                "run_id": "run-4",
                "repeat_index": 1,
                "status": "completed",
                "measurement_quality": "warning",
                "platform_families": ["jetson", "raspberry-pi"],
                "metrics": {"tokens_per_s": 4.0, "ttft_s": 2.0},
            },
        ]

    def test_aggregate_is_deterministic_and_preserves_failures(self) -> None:
        first = aggregate_campaign_attempts(
            self.attempts(),
            metric_names=["tokens_per_s", "ttft_s"],
            seed=7,
            bootstrap_resamples=200,
        )
        second = aggregate_campaign_attempts(
            self.attempts(),
            metric_names=["tokens_per_s", "ttft_s"],
            seed=7,
            bootstrap_resamples=200,
        )
        self.assertEqual(first, second)
        jetson = next(
            item for item in first["conditions"] if item["condition_id"] == "jetson-scale"
        )
        self.assertEqual(jetson["attempt_count"], 3)
        self.assertEqual(jetson["successful_repeat_count"], 2)
        self.assertEqual(jetson["failed_repeat_count"], 1)
        self.assertEqual(jetson["exclusions"][0]["run_id"], "run-2")
        self.assertEqual(jetson["exclusions"][0]["reason"], "MODEL_LOAD_OOM")
        self.assertEqual(jetson["metrics"]["tokens_per_s"]["median"], 12.0)
        self.assertEqual(jetson["metrics"]["tokens_per_s"]["iqr"], 2.0)
        self.assertEqual(
            jetson["metrics"]["ttft_s"]["excluded_measurements"][0]["run_id"],
            "run-3",
        )
        self.assertEqual(jetson["comparison_class"], "homogeneous")

    def test_mixed_platform_is_explicitly_exploratory(self) -> None:
        output = aggregate_campaign_attempts(
            self.attempts(),
            metric_names=["tokens_per_s"],
            bootstrap_resamples=100,
        )
        mixed = next(
            item
            for item in output["conditions"]
            if item["condition_id"] == "mixed-exploratory"
        )
        self.assertEqual(mixed["platform_families"], ["jetson", "raspberry_pi"])
        self.assertEqual(mixed["comparison_class"], "exploratory")
        self.assertFalse(mixed["formal_comparison_eligible"])

    def test_run_summaries_map_campaign_identity_and_platforms(self) -> None:
        output = aggregate_run_summaries(
            [
                {
                    "run_id": "run-summary-1",
                    "status": "completed",
                    "cluster_tokens_per_s": 8.5,
                    "measurement_quality": "clean",
                    "research_identity": {
                        "campaign_cell_id": "pi-scale--repeat-02",
                        "campaign_attempt_id": "attempt-summary-1",
                        "repeat_index": 2,
                    },
                    "participant_nodes": [
                        {"detected_platform": "raspberry-pi"},
                        {"configured_platform": "raspberry-pi"},
                    ],
                }
            ],
            metric_names=["cluster_tokens_per_s"],
            bootstrap_resamples=100,
        )
        condition = output["conditions"][0]
        self.assertEqual(condition["condition_id"], "pi-scale")
        self.assertEqual(condition["platform_families"], ["raspberry_pi"])
        self.assertTrue(condition["formal_comparison_eligible"])
        self.assertEqual(condition["metrics"]["cluster_tokens_per_s"]["mean"], 8.5)

    def test_aggregate_schema_is_valid_json_and_names_the_artifact(self) -> None:
        root = Path(__file__).resolve().parents[2]
        schema = json.loads(
            (root / "config/research/statistical_campaign_aggregate.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(
            schema["properties"]["artifact_type"]["const"],
            "statistical_campaign_aggregate",
        )

    def test_empty_values_and_invalid_attempts_are_not_silently_accepted(self) -> None:
        self.assertEqual(
            describe_samples([], seed=1, bootstrap_resamples=100)["count"], 0
        )
        with self.assertRaisesRegex(StatisticalCampaignError, "nonterminal"):
            aggregate_campaign_attempts(
                [{"condition_id": "a", "attempt_id": "x", "status": "running"}],
                metric_names=["tokens_per_s"],
                bootstrap_resamples=100,
            )


if __name__ == "__main__":
    unittest.main()
