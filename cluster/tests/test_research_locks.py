"""FOLLOWUP 08 formal experiment identity lock gates."""

from __future__ import annotations

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from cluster.infrastructure.storage import FilesystemRunRepository
from cluster.research.locks import (
    PINNED_RPC_COMMIT,
    LockValidationError,
    assess_formal_eligibility,
    canonical_json_bytes,
    lock_set_sha256,
    validate_condition_lock,
    validate_model_lock,
    validate_prompt_lock,
    validate_runtime_lock,
)


ROOT = Path(__file__).resolve().parents[2]
LOCK_ROOT = ROOT / "config" / "research"
LOCK_NAMES = (
    "model_lock.json",
    "experiment_conditions.json",
    "prompt_set.json",
    "runtime_lock.json",
)


def read_lock(name: str) -> dict:
    return json.loads((LOCK_ROOT / name).read_text(encoding="utf-8"))


def approved_model_lock() -> dict:
    lock = read_lock("model_lock.json")
    model = lock["models"][0]
    expected = model["binary"]["sha256"]
    model["verification"].update(
        {
            "status": "approved",
            "verified_workers": ["worker-a"],
            "observed_worker_checksums": {"worker-a": expected},
        }
    )
    model["runtime_contract"]["chat_template_hash"] = "a" * 64
    model["runtime_contract"]["tokenizer_metadata_hash"] = "b" * 64
    model["license"]["accepted_for_this_project"] = True
    return lock


def verified_runtime_lock() -> dict:
    lock = read_lock("runtime_lock.json")
    for worker in lock["workers"]:
        worker["deployment"]["git_commit"] = lock["source_commit"]
        worker["deployment"]["working_tree_clean"] = True
    return lock


def formal_config(model_key: str = "qwen2.5-1.5b-instruct-q4-k-m-official") -> dict:
    conditions = read_lock("experiment_conditions.json")
    return {
        "experiment_lock_id": conditions["lock_id"],
        "experiment_lock_sha256": conditions["lock_sha256"],
        "model_lock_entry": model_key,
        "prompt_set_version": read_lock("prompt_set.json")["prompt_set_version"],
        "runtime_lock_version": read_lock("runtime_lock.json")["lock_version"],
        "condition_profile_id": conditions["fixed_profile"]["profile_id"],
    }


class ShippedResearchLockTests(unittest.TestCase):
    def test_shipped_locks_validate_and_share_fingerprint(self) -> None:
        locks = {name: read_lock(name) for name in LOCK_NAMES}
        validate_model_lock(locks["model_lock.json"])
        validate_condition_lock(locks["experiment_conditions.json"])
        validate_prompt_lock(locks["prompt_set.json"])
        validate_runtime_lock(locks["runtime_lock.json"])
        fingerprint = lock_set_sha256(locks)
        self.assertEqual(
            fingerprint,
            "4a03235dd7ae27a0a915e918c583df99aad2011e99f0baf1a9f61a3dcb5c3ab4",
        )
        self.assertEqual({item["lock_sha256"] for item in locks.values()}, {fingerprint})

    def test_no_shipped_model_is_prematurely_approved(self) -> None:
        statuses = [model["verification"]["status"] for model in read_lock("model_lock.json")["models"]]
        self.assertEqual(statuses, ["source_locked"] * 4)


class FingerprintTests(unittest.TestCase):
    def test_key_order_does_not_change_fingerprint(self) -> None:
        first = {"a": {"x": 1, "y": 2}, "b": {"z": [1, 2]}}
        second = {"b": {"z": [1, 2]}, "a": {"y": 2, "x": 1}}
        self.assertEqual(lock_set_sha256(first), lock_set_sha256(second))

    def test_timestamps_do_not_change_fingerprint(self) -> None:
        first = {"lock": {"value": 1, "created_at": "one", "observed_at": "two"}}
        second = {"lock": {"value": 1, "created_at": "later", "observed_at": "latest"}}
        self.assertEqual(lock_set_sha256(first), lock_set_sha256(second))

    def test_condition_change_changes_fingerprint(self) -> None:
        first = {"lock": {"n_ctx": 4096}}
        second = {"lock": {"n_ctx": 8192}}
        self.assertNotEqual(lock_set_sha256(first), lock_set_sha256(second))

    def test_canonical_bytes_are_utf8_deterministic(self) -> None:
        self.assertEqual(canonical_json_bytes({"한글": "값", "a": 1}), canonical_json_bytes({"a": 1, "한글": "값"}))


class ModelLockValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = read_lock("model_lock.json")

    def test_duplicate_model_key_is_rejected(self) -> None:
        self.lock["models"].append(copy.deepcopy(self.lock["models"][0]))
        with self.assertRaisesRegex(LockValidationError, "duplicate model_key"):
            validate_model_lock(self.lock)

    def test_exact_revision_is_required(self) -> None:
        self.lock["models"][0]["source"]["revision"] = "main"
        with self.assertRaisesRegex(LockValidationError, "exact 40-character"):
            validate_model_lock(self.lock)

    def test_exact_gguf_basename_is_required(self) -> None:
        self.lock["models"][0]["binary"]["filename"] = "nested/model.gguf"
        with self.assertRaisesRegex(LockValidationError, "GGUF basename"):
            validate_model_lock(self.lock)

    def test_sha256_and_license_are_required(self) -> None:
        self.lock["models"][0]["binary"]["sha256"] = "guessed"
        with self.assertRaisesRegex(LockValidationError, "SHA-256"):
            validate_model_lock(self.lock)
        self.lock = read_lock("model_lock.json")
        self.lock["models"][0]["license"]["spdx_or_name"] = ""
        with self.assertRaisesRegex(LockValidationError, "license"):
            validate_model_lock(self.lock)

    def test_community_binary_requires_converter_provenance(self) -> None:
        model = self.lock["models"][0]
        model["source"]["official_gguf"] = False
        model["binary"]["converter"] = "unverified"
        with self.assertRaisesRegex(LockValidationError, "community GGUF provenance"):
            validate_model_lock(self.lock)

    def test_approved_model_requires_worker_checksum_match(self) -> None:
        lock = approved_model_lock()
        lock["models"][0]["verification"]["observed_worker_checksums"]["worker-a"] = "0" * 64
        with self.assertRaisesRegex(LockValidationError, "checksum mismatch"):
            validate_model_lock(lock)

    def test_complete_approved_model_contract_is_accepted(self) -> None:
        validate_model_lock(approved_model_lock())


class PromptAndConditionValidationTests(unittest.TestCase):
    def test_prompt_ids_are_unique(self) -> None:
        lock = read_lock("prompt_set.json")
        lock["prompts"][1]["prompt_id"] = lock["prompts"][0]["prompt_id"]
        with self.assertRaisesRegex(LockValidationError, "duplicate prompt_id"):
            validate_prompt_lock(lock)

    def test_prompt_text_change_causes_hash_mismatch(self) -> None:
        lock = read_lock("prompt_set.json")
        lock["prompts"][0]["text"] += " 변경"
        with self.assertRaisesRegex(LockValidationError, "prompt hash mismatch"):
            validate_prompt_lock(lock)

    def test_prompt_set_version_is_required(self) -> None:
        lock = read_lock("prompt_set.json")
        lock.pop("prompt_set_version")
        with self.assertRaisesRegex(LockValidationError, "prompt_set_version"):
            validate_prompt_lock(lock)

    def test_missing_inference_parameter_is_rejected(self) -> None:
        lock = read_lock("experiment_conditions.json")
        lock["fixed_profile"].pop("repeat_penalty")
        with self.assertRaisesRegex(LockValidationError, "missing inference parameters"):
            validate_condition_lock(lock)

    def test_implicit_backend_default_is_rejected(self) -> None:
        lock = read_lock("experiment_conditions.json")
        lock["fixed_profile"]["n_batch"] = "auto"
        with self.assertRaisesRegex(LockValidationError, "implicit backend default"):
            validate_condition_lock(lock)

    def test_all_platform_profiles_are_required(self) -> None:
        lock = read_lock("experiment_conditions.json")
        lock["platform_profiles"].pop("rpc")
        with self.assertRaisesRegex(LockValidationError, "platform_profiles.rpc"):
            validate_condition_lock(lock)


class RuntimeAndEligibilityTests(unittest.TestCase):
    def test_controller_and_rpc_commits_are_exact(self) -> None:
        lock = read_lock("runtime_lock.json")
        lock["controller"]["git_commit"] = "main"
        with self.assertRaisesRegex(LockValidationError, "controller.git_commit"):
            validate_runtime_lock(lock)
        lock = read_lock("runtime_lock.json")
        lock["native_llama_cpp_rpc"]["pinned_commit"] = "0" * 40
        with self.assertRaisesRegex(LockValidationError, "RPC commit"):
            validate_runtime_lock(lock)
        self.assertEqual(PINNED_RPC_COMMIT, "f49e9178767d557a522618b16ce8694f9ddac628")

    def test_unverified_backend_is_rejected(self) -> None:
        lock = read_lock("runtime_lock.json")
        lock["workers"][0]["runtime"]["backend_verified"] = False
        with self.assertRaisesRegex(LockValidationError, "backend is not verified"):
            validate_runtime_lock(lock)

    def test_jetson_power_mismatch_blocks_formal_eligibility(self) -> None:
        result = assess_formal_eligibility(
            experiment_config=formal_config(),
            experiment_conditions=read_lock("experiment_conditions.json"),
            model_lock=approved_model_lock(),
            prompt_lock=read_lock("prompt_set.json"),
            runtime_lock=verified_runtime_lock(),
            model_key="qwen2.5-1.5b-instruct-q4-k-m-official",
            prompt_ids=["general-ko-001"],
            selected_workers=["jetson-worker-01", "jetson-worker-03"],
        )
        self.assertFalse(result["eligible"])
        self.assertIn("JETSON_POWER_MODE_MISMATCH", {item["code"] for item in result["blocking_issues"]})

    def test_pi_power_history_is_non_blocking(self) -> None:
        result = assess_formal_eligibility(
            experiment_config=formal_config(),
            experiment_conditions=read_lock("experiment_conditions.json"),
            model_lock=approved_model_lock(),
            prompt_lock=read_lock("prompt_set.json"),
            runtime_lock=verified_runtime_lock(),
            model_key="qwen2.5-1.5b-instruct-q4-k-m-official",
            prompt_ids=["general-ko-001"],
            selected_workers=["pi-worker-02"],
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["blocking_issues"], [])
        self.assertEqual(result["warnings"], [{"code": "PI_POWER_HISTORY", "node": "pi-worker-02"}])

    def test_live_model_checksum_mismatch_blocks(self) -> None:
        result = assess_formal_eligibility(
            experiment_config=formal_config(),
            experiment_conditions=read_lock("experiment_conditions.json"),
            model_lock=approved_model_lock(),
            prompt_lock=read_lock("prompt_set.json"),
            runtime_lock=verified_runtime_lock(),
            model_key="qwen2.5-1.5b-instruct-q4-k-m-official",
            prompt_ids=["general-ko-001"],
            selected_workers=["pi-worker-04"],
            live_preflight_snapshot={"pi-worker-04": {"model_sha256": "0" * 64}},
        )
        self.assertFalse(result["eligible"])
        self.assertIn("MODEL_SHA_MISMATCH", {item["code"] for item in result["blocking_issues"]})

    def test_unapproved_shipped_model_is_blocked(self) -> None:
        result = assess_formal_eligibility(
            experiment_config=formal_config("granite-3.3-2b-instruct-q4-k-m-official"),
            experiment_conditions=read_lock("experiment_conditions.json"),
            model_lock=read_lock("model_lock.json"),
            prompt_lock=read_lock("prompt_set.json"),
            runtime_lock=verified_runtime_lock(),
            model_key="granite-3.3-2b-instruct-q4-k-m-official",
            prompt_ids=["general-ko-001"],
            selected_workers=["pi-worker-04"],
        )
        self.assertFalse(result["eligible"])
        self.assertIn("MODEL_NOT_APPROVED", {item["code"] for item in result["blocking_issues"]})

    def test_formal_trace_mismatch_is_blocking(self) -> None:
        config = formal_config()
        config["prompt_set_version"] = 99
        result = assess_formal_eligibility(
            experiment_config=config,
            experiment_conditions=read_lock("experiment_conditions.json"),
            model_lock=approved_model_lock(),
            prompt_lock=read_lock("prompt_set.json"),
            runtime_lock=verified_runtime_lock(),
            model_key="qwen2.5-1.5b-instruct-q4-k-m-official",
            prompt_ids=["general-ko-001"],
            selected_workers=["pi-worker-04"],
        )
        self.assertFalse(result["eligible"])
        self.assertIn(
            "EXPERIMENT_CONDITION_MISMATCH",
            {item["code"] for item in result["blocking_issues"]},
        )


class BenchmarkSchemaRegressionTests(unittest.TestCase):
    def test_requests_csv_remains_exactly_nineteen_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory))
            repository.create("20260821_120000_abcd", {"experiment_id": "lock-regression"})
            repository.write_requests("20260821_120000_abcd", [{"request_id": 1}])
            with (Path(directory) / "20260821_120000_abcd" / "requests.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                header = next(csv.reader(handle))
        self.assertEqual(len(header), 19)
        self.assertEqual(header[0], "request_id")
        self.assertEqual(header[-1], "warmup")


if __name__ == "__main__":
    unittest.main()
