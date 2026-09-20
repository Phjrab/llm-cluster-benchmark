"""S03 tests use synthetic cached evidence and injected fake inference only."""
import ast
import dataclasses
import hashlib
import inspect
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import cluster.application.sweep_models as sweep_models
from cluster.application.model_service import WorkerModelInventory, model_license_fingerprint
from cluster.application.suite_runner import SuiteRunner
from cluster.application.sweep_models import build_cell_config, preview_catalog_sweep
from cluster.application.sweep_planner import verify_plan
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.model import ModelCatalogEntry, ModelInventoryEntry
from cluster.domain.runtime_profile import require_applied_profile
from cluster.domain.sweep import PromptVariant, SweepSpec, WorkerReference
from cluster.tests.test_context_runtime import FakeLlama
from cluster.worker.inference import LlamaCppInferenceBackend

TEXT = "shared synthetic prompt"
RUNTIME_COMMIT = "e" * 40


def fixtures():
    catalog = []
    models = []
    prompts = []
    for alias, quantization, content, template in (
        ("a", "Q4_K_M", b"aaaa", "b" * 64),
        ("b", "Q8_0", b"bbbb", "c" * 64),
    ):
        sha256 = hashlib.sha256(content).hexdigest()
        model_id = f"family/{alias}-{quantization}.gguf"
        catalog.append(
            ModelCatalogEntry(
                id=model_id,
                gguf_repo="fake/model",
                gguf_revision="d" * 40,
                gguf_filename=model_id.split("/")[-1],
                size_bytes=4,
                sha256=sha256,
                architecture="fake",
                quantization=quantization,
                license="Apache-2.0",
                download_policy="direct",
                provenance_status="community_review",
                parameters_total_b=14 if alias == "a" else 70,
                kv_cache_bytes_per_token=1024,
                verified_platforms=("jetson",),
                verified_llama_cpp_commits=(RUNTIME_COMMIT,),
                verification_status="verified",
            )
        )
        models.append(
            ModelInventoryEntry(
                id=model_id,
                filename=model_id.split("/")[-1],
                size_bytes=4,
                sha256=sha256,
                quantization=quantization,
                source_revision="d" * 40,
                architecture="fake",
                chat_template_hash=template,
                tokenizer_metadata_hash="f" * 64,
                metadata_contract="gguf-metadata-v1",
                metadata_inspected=True,
                license_accepted=True,
            )
        )
        prompts.append(
            PromptVariant(
                ref="same",
                model_ref=alias,
                text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
                template_sha256=template,
                rendered_input_tokens=4 if alias == "a" else 6,
                input_token_source="fake-template",
                input_tokens_exact=True,
            )
        )
    workers = [
        WorkerReference(
            "j1", "physical-j1", "jetson", "valid", "valid", "unknown", "valid",
            RUNTIME_COMMIT, True, 8192, 6144,
        ),
        WorkerReference(
            "j2", "physical-j2", "jetson", "valid", "valid", "unknown", "valid",
            RUNTIME_COMMIT, True, 8192, 6144,
        ),
    ]
    inventories = [WorkerModelInventory(worker.worker_id, tuple(models)) for worker in workers]
    spec = SweepSpec.from_dict(
        {
            "base": {
                "model_ref": "a",
                "prompt_ref": "same",
                "worker_ids": ["j1", "j2"],
                "n_ctx": 128,
                "max_tokens": 16,
                "n_gpu_layers": 0,
                "requests": 1,
                "warmup_requests": 0,
            },
            "axes": [{"name": "model_ref", "values": ["a", "b"]}],
        }
    )
    return {
        "spec": spec,
        "selections": {"a": catalog[0].id, "b": catalog[1].id},
        "catalog": catalog,
        "inventories": inventories,
        "workers": workers,
        "prompts": prompts,
    }


def preview(**updates):
    data = fixtures()
    data.update(updates)
    return preview_catalog_sweep(**data)


class SweepModelResolutionTests(unittest.TestCase):
    def test_two_models_pin_identity_and_keep_model_specific_prompt_evidence(self):
        result = preview()
        self.assertEqual(result.plan.counts.valid_cells, 2)
        self.assertTrue(result.plan.executable)
        for candidate in result.candidates:
            self.assertEqual(candidate.installed_workers, ("j1", "j2"))
            self.assertTrue(candidate.downloadable)
            self.assertEqual(candidate.runtime_verified_workers, ("j1", "j2"))
            self.assertEqual(candidate.formal_approval, "not_assessed")
        first, second = result.plan.cells
        self.assertEqual(first.model.size_bytes, 4)
        self.assertEqual(first.model.architecture, "fake")
        self.assertEqual(first.model.tokenizer_sha256, "f" * 64)
        self.assertNotEqual(first.model.quantization, second.model.quantization)
        self.assertEqual(first.prompt.text_sha256, second.prompt.text_sha256)
        self.assertNotEqual(first.prompt.template_sha256, second.prompt.template_sha256)
        self.assertNotEqual(first.prompt.rendered_input_tokens, second.prompt.rendered_input_tokens)
        self.assertEqual(verify_plan(result.plan.to_json()), result.plan)

    def test_same_filename_different_sha_and_identity_fields_do_not_merge(self):
        original = preview().plan
        data = fixtures()
        common = data["catalog"][0].gguf_filename
        data["catalog"][1] = dataclasses.replace(data["catalog"][1], gguf_filename=common)
        for index, inventory in enumerate(data["inventories"]):
            entries = list(inventory.models)
            entries[1] = dataclasses.replace(entries[1], filename=common)
            data["inventories"][index] = dataclasses.replace(inventory, models=tuple(entries))
        same_name = preview_catalog_sweep(**data).plan
        self.assertNotEqual(
            same_name.cells[0].model.artifact_sha256,
            same_name.cells[1].model.artifact_sha256,
        )
        for field, value in (
            ("size_bytes", 5),
            ("architecture", "other"),
            ("quantization", "Q5_K_M"),
        ):
            data = fixtures()
            data["catalog"][0] = dataclasses.replace(data["catalog"][0], **{field: value})
            for index, inventory in enumerate(data["inventories"]):
                entries = list(inventory.models)
                entries[0] = dataclasses.replace(entries[0], **{field: value})
                data["inventories"][index] = dataclasses.replace(inventory, models=tuple(entries))
            changed = preview_catalog_sweep(**data).plan
            self.assertNotEqual(original.cells[0].cell_id, changed.cells[0].cell_id)

    def test_worker_identity_mismatches_block_only_affected_model(self):
        changes = (
            {"sha256": "0" * 64},
            {"quantization": "Q5_K_M"},
            {"source_revision": "0" * 40},
            {"size_bytes": 5},
            {"architecture": "other"},
            {"checksum_valid": False},
        )
        for change in changes:
            with self.subTest(change=change):
                data = fixtures()
                inventory = data["inventories"][1]
                data["inventories"][1] = dataclasses.replace(
                    inventory,
                    models=(dataclasses.replace(inventory.models[0], **change), inventory.models[1]),
                )
                result = preview_catalog_sweep(**data)
                self.assertEqual(result.plan.cells[0].status, "blocked")
                self.assertEqual(result.plan.cells[1].status, "valid")
                self.assertEqual(result.candidates[0].installed_workers, ("j1",))

    def test_template_conflict_is_not_arbitrarily_selected(self):
        data = fixtures()
        inventory = data["inventories"][1]
        data["inventories"][1] = dataclasses.replace(
            inventory,
            models=(
                dataclasses.replace(inventory.models[0], chat_template_hash="0" * 64),
                inventory.models[1],
            ),
        )
        result = preview_catalog_sweep(**data)
        self.assertIn("MODEL_TEMPLATE_EVIDENCE_CONFLICT", result.candidates[0].reason_codes)
        self.assertIsNone(result.plan.cells[0].model)
        self.assertEqual(result.plan.cells[0].status, "blocked")

    def test_missing_and_catalog_only_candidates_remain_visible(self):
        data = fixtures()
        data["inventories"] = []
        result = preview_catalog_sweep(**data)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.plan.counts.blocked_cells, 2)
        self.assertTrue(all(candidate.downloadable for candidate in result.candidates))
        data = fixtures()
        data["catalog"][0] = dataclasses.replace(
            data["catalog"][0], sha256="", gguf_revision="main", download_policy="catalog_only"
        )
        result = preview_catalog_sweep(**data)
        self.assertFalse(result.candidates[0].identity_resolved)
        self.assertFalse(result.candidates[0].downloadable)
        self.assertIn("MODEL_PINNED_IDENTITY_REQUIRED", result.candidates[0].reason_codes)

    def test_unknown_catalog_and_duplicate_snapshots_fail_closed(self):
        data = fixtures()
        data["catalog"] = data["catalog"][1:]
        result = preview_catalog_sweep(**data)
        self.assertEqual(result.candidates[0].reason_codes, ("CATALOG_MODEL_UNRESOLVED",))
        data = fixtures()
        data["inventories"].append(data["inventories"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            preview_catalog_sweep(**data)
        data = fixtures()
        data["acceptances"] = []
        with self.assertRaisesRegex(ValueError, "acceptances"):
            preview_catalog_sweep(**data)
        data = fixtures()
        inventory = data["inventories"][0]
        data["inventories"][0] = dataclasses.replace(
            inventory, models=inventory.models + (inventory.models[0],)
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            preview_catalog_sweep(**data)

    def test_license_gated_access_and_formal_approval_are_separate(self):
        data = fixtures()
        entry = dataclasses.replace(
            data["catalog"][0], gated=True, license_review_required=True
        )
        data["catalog"][0] = entry
        result = preview_catalog_sweep(**data)
        self.assertIn("MODEL_LICENSE_NOT_ACCEPTED", result.candidates[0].reason_codes)
        self.assertFalse(result.candidates[0].downloadable)
        data["acceptances"] = {
            entry.id: {"fingerprint": model_license_fingerprint(entry)}
        }
        result = preview_catalog_sweep(**data)
        self.assertEqual(result.plan.cells[0].status, "valid")
        self.assertFalse(result.candidates[0].downloadable)
        self.assertEqual(result.candidates[0].formal_approval, "not_assessed")
        data["gated_access"] = True
        self.assertTrue(preview_catalog_sweep(**data).candidates[0].downloadable)
        data["catalog"][0] = dataclasses.replace(entry, license="changed")
        self.assertEqual(preview_catalog_sweep(**data).plan.cells[0].status, "blocked")

    def test_installation_does_not_promote_runtime_evidence(self):
        data = fixtures()
        data["workers"][0] = dataclasses.replace(data["workers"][0], runtime_commit=None)
        result = preview_catalog_sweep(**data)
        self.assertTrue(result.candidates[0].identity_resolved)
        self.assertEqual(result.candidates[0].runtime_verified_workers, ("j2",))
        self.assertEqual(result.plan.cells[0].status, "unknown")
        data["workers"][0] = dataclasses.replace(
            data["workers"][0], backend_verified=False
        )
        self.assertEqual(preview_catalog_sweep(**data).plan.cells[0].status, "blocked")

    def test_memory_and_context_are_per_condition_not_parameter_count(self):
        data = fixtures()
        data["workers"][0] = dataclasses.replace(
            data["workers"][0], memory_available_mb=0
        )
        self.assertEqual(preview_catalog_sweep(**data).plan.counts.blocked_cells, 2)
        data = fixtures()
        data["catalog"][0] = dataclasses.replace(
            data["catalog"][0], kv_cache_bytes_per_token=None
        )
        self.assertEqual(preview_catalog_sweep(**data).plan.cells[0].status, "unknown")
        self.assertEqual(preview().plan.counts.valid_cells, 2)
        data = fixtures()
        data["catalog"][0] = dataclasses.replace(
            data["catalog"][0], default_context=128, context_length_advertised=128
        )
        raw = data["spec"].to_dict()
        raw["axes"].append({"name": "n_ctx", "values": [128, 256]})
        data["spec"] = SweepSpec.from_dict(raw)
        result = preview_catalog_sweep(**data)
        self.assertEqual(
            [cell.status for cell in result.plan.cells],
            ["valid", "blocked", "valid", "valid"],
        )

    def test_rpc_checks_only_coordinator_installation(self):
        data = fixtures()
        data["inventories"][1] = WorkerModelInventory("j2", ())
        raw = data["spec"].to_dict()
        raw["base"].update(
            execution_strategy="model_parallel_rpc", n_gpu_layers=30
        )
        raw["rpc_profiles"] = [
            {"profile_id": "first", "worker_ids": ["j1", "j2"], "coordinator_id": "j1"},
            {"profile_id": "second", "worker_ids": ["j1", "j2"], "coordinator_id": "j2"},
        ]
        raw["axes"] = [{"name": "rpc_profile_ref", "values": ["first", "second"]}]
        data["spec"] = SweepSpec.from_dict(raw)
        result = preview_catalog_sweep(**data)
        blocked = lambda cell: {
            check.code for check in cell.capabilities if check.status == "blocked"
        }
        self.assertEqual(blocked(result.plan.cells[0]), set())
        self.assertIn("MODEL_NOT_INSTALLED", blocked(result.plan.cells[1]))
        self.assertEqual(result.plan.cells[0].status, "valid")
        data["catalog"][0] = dataclasses.replace(
            data["catalog"][0], default_context=128, context_length_advertised=128
        )
        raw = data["spec"].to_dict()
        raw["base"]["n_ctx"] = 256
        data["spec"] = SweepSpec.from_dict(raw)
        result = preview_catalog_sweep(**data)
        self.assertIn("MODEL_CONTEXT_LIMIT_EXCEEDED", blocked(result.plan.cells[0]))

    def test_multipart_blocks_only_that_model(self):
        data = fixtures()
        data["catalog"][1] = dataclasses.replace(
            data["catalog"][1], multipart=True, download_policy="multipart_unsupported"
        )
        result = preview_catalog_sweep(**data)
        self.assertEqual([cell.status for cell in result.plan.cells], ["valid", "blocked"])
        self.assertIn(
            "ARTIFACT_SET_MANIFEST_REQUIRED", result.candidates[1].reason_codes
        )
        self.assertFalse(result.candidates[1].identity_resolved)

    def test_private_prompt_and_ready_cell_are_required_for_binding(self):
        result = preview().plan
        with self.assertRaisesRegex(ValueError, "prompt"):
            build_cell_config(
                result,
                trial_id=result.trials[0].trial_id,
                sweep_id="s",
                attempt_id="a",
                prompt_text="changed",
            )
        with self.assertRaisesRegex(ValueError, "trial"):
            build_cell_config(
                result,
                trial_id="missing",
                sweep_id="s",
                attempt_id="a",
                prompt_text=TEXT,
            )
        data = fixtures()
        data["workers"][0] = dataclasses.replace(
            data["workers"][0], backend_verified=None
        )
        result = preview_catalog_sweep(**data).plan
        with self.assertRaisesRegex(ValueError, "not ready"):
            build_cell_config(
                result,
                trial_id=result.trials[0].trial_id,
                sweep_id="s",
                attempt_id="a",
                prompt_text=TEXT,
            )

    def test_two_bound_models_use_own_fake_template_and_tokens(self):
        result = preview().plan
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "family").mkdir()
            for model, content in zip(result.context.models, (b"aaaa", b"bbbb")):
                (root / model.model_id).write_bytes(content)
            calls = []

            def factory(**kwargs):
                calls.append(kwargs)
                return FakeLlama(**kwargs)

            def prepare(llm, messages):
                second = llm.kwargs["model_path"].endswith("b-Q8_0.gguf")
                self.assertEqual(messages[-1]["content"], TEXT)
                return {"prompt": list(range(6 if second else 4))}, ("c" if second else "b") * 64

            backend = LlamaCppInferenceBackend(
                root, llama_factory=factory, torch_module=object(), chat_preparer=prepare
            )
            for index, trial in enumerate(result.trials):
                config = build_cell_config(
                    result,
                    trial_id=trial.trial_id,
                    sweep_id="sweep",
                    attempt_id="attempt" + str(index),
                    prompt_text=TEXT,
                )
                self.assertEqual(config.model_count, 1)
                self.assertEqual(
                    config.sweep["model_identity"]["quantization"],
                    ["Q4_K_M", "Q8_0"][index],
                )
                restored = ExperimentConfig.from_dict(dataclasses.asdict(config), strict=True)
                restored.validate()
                info = backend.load_model(config.model_id, config.n_ctx, config.n_gpu_layers)
                require_applied_profile(info, config)
                proof = backend.prepare_input(
                    preparation_id=config.sweep["attempt_id"],
                    message=config.prompt,
                    history=[],
                    max_tokens=config.max_tokens,
                    model_sha256=config.sweep["model_sha256"],
                    template_sha256=config.sweep["template_sha256"],
                    prompt_sha256=config.sweep["prompt_sha256"],
                )
                self.assertEqual(proof["input_tokens"], [4, 6][index])
                self.assertNotIn(TEXT, json.dumps(proof))
                output = list(
                    backend.stream_chat(
                        message=config.prompt,
                        history=[],
                        max_tokens=16,
                        temperature=0,
                        top_p=0.9,
                        prepared_input_id=config.sweep["attempt_id"],
                    )
                )
                self.assertEqual(output, ["early"])
                backend.unload_model()
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(calls[0]["model_path"], calls[1]["model_path"])

    def test_suite_cannot_reexpand_bound_sweep_child(self):
        result = preview().plan
        config = build_cell_config(
            result,
            trial_id=result.trials[0].trial_id,
            sweep_id="s",
            attempt_id="a",
            prompt_text=TEXT,
        )
        runner, repository, unload = mock.Mock(), mock.Mock(), mock.Mock()
        suite = SuiteRunner(runner, repository, unload)
        with self.assertRaisesRegex(ValueError, "nested model expansion"):
            suite.run(
                config,
                [cell.model.model_id for cell in result.cells],
                "suite",
                True,
                0,
                threading.Event(),
                2,
                1,
                "now",
            )
        runner.run.assert_not_called()
        repository.write.assert_not_called()
        unload.assert_not_called()

    def test_broadcast_and_node_sweep_keep_one_model_per_cell(self):
        for strategy in ("broadcast_compare", "node_sweep"):
            with self.subTest(strategy=strategy):
                data = fixtures()
                raw = data["spec"].to_dict()
                raw["base"]["execution_strategy"] = strategy
                data["spec"] = SweepSpec.from_dict(raw)
                plan = preview_catalog_sweep(**data).plan
                self.assertEqual(len(plan.cells), 2)
                self.assertEqual(
                    [cell.model.model_id for cell in plan.cells],
                    [data["catalog"][0].id, data["catalog"][1].id],
                )
                for trial in plan.trials:
                    config = build_cell_config(
                        plan,
                        trial_id=trial.trial_id,
                        sweep_id="s",
                        attempt_id="a" + str(trial.generation_order_index),
                        prompt_text=TEXT,
                    )
                    self.assertEqual(config.model_count, 1)

    def test_runtime_identity_changes_hash_and_trace_tamper_is_rejected(self):
        result = preview().plan
        data = fixtures()
        data["workers"][0] = dataclasses.replace(
            data["workers"][0], runtime_commit="0" * 40
        )
        self.assertNotEqual(
            preview_catalog_sweep(**data).plan.plan_sha256, result.plan_sha256
        )
        config = build_cell_config(
            result,
            trial_id=result.trials[0].trial_id,
            sweep_id="s",
            attempt_id="a",
            prompt_text=TEXT,
        )
        config.sweep["model_identity"]["artifact_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity"):
            config.validate()
        config = build_cell_config(
            result,
            trial_id=result.trials[0].trial_id,
            sweep_id="s",
            attempt_id="b",
            prompt_text=TEXT,
        )
        config.sweep["model_identity"].pop("size_bytes")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            config.validate()

    def test_model_adapter_has_no_io_or_dispatch_imports(self):
        tree = ast.parse(inspect.getsource(sweep_models))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = {"os", "pathlib", "socket", "subprocess", "urllib", "requests", "httpx"}
        self.assertFalse(forbidden & {name.split(".")[0] for name in imports})
        self.assertFalse(any(name.startswith("cluster.dashboard") for name in imports))


if __name__ == "__main__":
    unittest.main()
