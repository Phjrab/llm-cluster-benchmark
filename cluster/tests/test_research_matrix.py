"""Roadmap Phase 04 formal matrix, protocol, and analysis gates."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from cluster.research.matrix import (
    MatrixValidationError,
    compute_matrix_volume,
    expand_formal_matrix,
    validate_analysis_plan,
    validate_experiment_protocol,
    validate_formal_matrix,
)


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "config" / "research"


def read_json(name: str) -> dict:
    return json.loads((RESEARCH / name).read_text(encoding="utf-8"))


def validate(matrix: dict) -> dict[str, int]:
    return validate_formal_matrix(
        matrix,
        model_lock=read_json("model_lock.json"),
        prompt_lock=read_json("prompt_set.json"),
        runtime_lock=read_json("runtime_lock.json"),
        experiment_conditions=read_json("experiment_conditions.json"),
    )


class ShippedMatrixTests(unittest.TestCase):
    def test_shipped_matrix_validates_and_expands_to_72_cells(self) -> None:
        matrix = read_json("formal_experiment_matrix.json")
        volume = validate(matrix)
        cells = expand_formal_matrix(matrix)
        self.assertEqual(len(cells), 72)
        self.assertEqual(len({cell["cell_id"] for cell in cells}), 72)
        self.assertEqual(volume, matrix["expected_volume"])

    def test_exact_workload_budget_separates_logical_and_physical_requests(self) -> None:
        volume = compute_matrix_volume(read_json("formal_experiment_matrix.json"))
        self.assertEqual(volume["runs_minimum"], 720)
        self.assertEqual(volume["runs_maximum"], 2160)
        self.assertEqual(volume["logical_requests_per_matrix_repeat"], 1440)
        self.assertEqual(volume["physical_requests_per_matrix_repeat"], 1760)
        self.assertEqual(volume["warmup_requests_per_matrix_repeat"], 112)
        self.assertEqual(volume["estimated_storage_bytes_minimum"], 320 * 1024 * 1024)
        self.assertEqual(volume["estimated_storage_bytes_maximum"], 960 * 1024 * 1024)

    def test_every_active_cell_is_formal_and_controller_free(self) -> None:
        matrix = read_json("formal_experiment_matrix.json")
        runtime_nodes = {item["node"] for item in read_json("runtime_lock.json")["workers"]}
        for cell in expand_formal_matrix(matrix):
            self.assertEqual(cell["experiment_type"], "formal")
            self.assertEqual(cell["repeat_index"], "campaign_manifest")
            self.assertTrue(set(cell["node_set"]).issubset(runtime_nodes))
            self.assertNotIn("controller", " ".join(cell["node_set"]).lower())

    def test_only_approved_models_and_locked_prompts_are_active(self) -> None:
        cells = expand_formal_matrix(read_json("formal_experiment_matrix.json"))
        model_lock = read_json("model_lock.json")
        approved = {
            item["model_key"]
            for item in model_lock["models"]
            if item["verification"]["status"] == "approved"
        }
        prompts = {item["prompt_id"] for item in read_json("prompt_set.json")["prompts"]}
        self.assertEqual({cell["model_lock_key"] for cell in cells}, approved)
        self.assertEqual({cell["prompt_id"] for cell in cells}, prompts)

    def test_formal_multi_node_cells_are_pi_only_and_within_one_cohort(self) -> None:
        cells = expand_formal_matrix(read_json("formal_experiment_matrix.json"))
        multi = [cell for cell in cells if cell["node_count"] > 1]
        self.assertEqual(len(multi), 24)
        self.assertTrue(all(cell["platform_cohort"].startswith("pi5-") for cell in multi))
        self.assertTrue(
            all(all(node.startswith("pi-worker-") for node in cell["node_set"]) for cell in multi)
        )

    def test_deferred_rpc_heterogeneous_and_model_size_questions_are_explicit(self) -> None:
        deferred = {
            item["question"]: item for item in read_json("formal_experiment_matrix.json")["deferred_questions"]
        }
        self.assertEqual(deferred["homogeneous-vs-heterogeneous-cluster"]["status"], "exploratory_only")
        self.assertEqual(deferred["model-size-by-platform-interaction"]["status"], "blocked")
        self.assertEqual(deferred["rpc-feasibility-performance-network-cost"]["status"], "blocked")
        self.assertEqual(deferred["power-and-thermal-causal-effect"]["status"], "descriptive_only")


class MatrixFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = read_json("formal_experiment_matrix.json")

    def test_unapproved_model_is_rejected(self) -> None:
        self.matrix["cell_templates"][0]["model_lock_keys"] = [
            "qwen2.5-3b-instruct-q4-k-m-official"
        ]
        with self.assertRaisesRegex(MatrixValidationError, "unapproved model"):
            validate(self.matrix)

    def test_unknown_or_controller_node_is_rejected(self) -> None:
        node_set = self.matrix["cell_templates"][0]["node_sets"][0]
        node_set.update({"nodes": ["controller"], "node_count": 1})
        with self.assertRaisesRegex(MatrixValidationError, "Controller or unknown"):
            validate(self.matrix)

    def test_cross_cohort_cell_is_rejected(self) -> None:
        node_set = self.matrix["cell_templates"][4]["node_sets"][0]
        node_set.update(
            {
                "nodes": ["pi-worker-02", "jetson-worker-01"],
                "node_count": 2,
            }
        )
        with self.assertRaisesRegex(MatrixValidationError, "crosses or misses"):
            validate(self.matrix)

    def test_smoke_or_pilot_cell_cannot_enter_formal_matrix(self) -> None:
        self.matrix["cell_templates"][0]["experiment_type"] = "pilot"
        with self.assertRaisesRegex(MatrixValidationError, "may not mix"):
            validate(self.matrix)

    def test_volume_drift_is_rejected(self) -> None:
        self.matrix["expected_volume"]["base_cells"] = 71
        with self.assertRaisesRegex(MatrixValidationError, "expected_volume.base_cells"):
            validate(self.matrix)

    def test_unlocked_parameter_profile_is_rejected(self) -> None:
        self.matrix["parameter_profiles"]["formal-jetson-cuda-v1"]["platform_profile"] = "latest"
        with self.assertRaisesRegex(MatrixValidationError, "unlocked platform_profile"):
            validate(self.matrix)


class ProtocolAndAnalysisTests(unittest.TestCase):
    def test_shipped_protocol_and_analysis_validate(self) -> None:
        validate_experiment_protocol(read_json("experiment_protocol.json"))
        validate_analysis_plan(read_json("analysis_plan.json"))

    def test_repeat_unit_and_seeded_order_are_locked(self) -> None:
        protocol = read_json("experiment_protocol.json")
        self.assertEqual(protocol["repeat_policy"]["independent_unit"], "run")
        self.assertEqual(protocol["repeat_policy"]["final_count_source"], "phase-09-pilot")
        self.assertEqual(protocol["execution_order"]["seed"], 20260823)
        self.assertFalse(protocol["parallel_formal_runs"])

    def test_protocol_rejects_result_type_mixing(self) -> None:
        protocol = read_json("experiment_protocol.json")
        protocol["experiment_type_separation"]["mixing_allowed"] = True
        with self.assertRaisesRegex(MatrixValidationError, "must not be mixed"):
            validate_experiment_protocol(protocol)

    def test_analysis_rejects_request_level_pseudoreplication(self) -> None:
        plan = read_json("analysis_plan.json")
        plan["analysis_units"]["independent_inference_unit"] = "request"
        with self.assertRaisesRegex(MatrixValidationError, "run must be"):
            validate_analysis_plan(plan)

    def test_analysis_requires_run_level_95_percent_ci_and_no_imputation(self) -> None:
        plan = read_json("analysis_plan.json")
        self.assertEqual(plan["confidence_intervals"]["level"], 0.95)
        self.assertEqual(plan["confidence_intervals"]["resampling_unit"], "run")
        self.assertEqual(plan["failure_and_exclusion"]["imputation"], "none")
        self.assertTrue(plan["failure_and_exclusion"]["preserve_raw"])

    def test_campaign_schema_requires_every_matrix_axis_and_repeat_index(self) -> None:
        schema = read_json("campaign_manifest.schema.json")
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        required = set(schema["$defs"]["campaignCell"]["required"])
        self.assertTrue(
            {
                "experiment_type",
                "platform_cohort",
                "strategy",
                "node_set",
                "node_count",
                "model_lock_key",
                "prompt_set_version",
                "parameter_profile",
                "repeat_index",
                "order_block",
                "measurement_quality_policy",
            }.issubset(required)
        )
        self.assertEqual(schema["properties"]["controller_participant_policy"]["const"], "forbidden")
        self.assertEqual(schema["properties"]["cells"]["minItems"], 720)
        self.assertEqual(schema["properties"]["cells"]["maxItems"], 2160)


if __name__ == "__main__":
    unittest.main()
