"""S04 RPC sweep tests use only cached evidence and injected fake native calls."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import unittest
from types import SimpleNamespace

from cluster.application.model_service import WorkerModelInventory
from cluster.application.sweep_models import build_cell_config, preview_catalog_sweep
from cluster.benchmark.rpc import RpcBackendError, WorkerRpcBackend
from cluster.domain.errors import ErrorCode
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.sweep import PromptVariant, SweepSpec, WorkerReference
from cluster.domain.worker import WorkerNode
from cluster.infrastructure.gguf import canonical_metadata_sha256
from cluster.tests.test_sweep_models import RUNTIME_COMMIT, TEXT, fixtures


CAPABILITIES = (
    "rpc_split_modes=layer,row rpc_gpu_layers=all,integer "
    "rpc_input_preparation=apply-template,tokenize,props"
)


def node(name: str, platform: str = "jetson") -> WorkerNode:
    suffix = {"j1": 11, "j2": 12, "j3": 13}.get(name, 21)
    return WorkerNode(
        name=name,
        host=f"192.168.10.{suffix}",
        user="bench",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/bench/{name}/llm-cluster",
        platform=platform,
    )


def rpc_preview():
    data = fixtures()
    templates = {"a": "{{ bos_token }}A", "b": "{{ bos_token }}B"}
    template_hashes = {
        key: canonical_metadata_sha256({"tokenizer.chat_template": value})
        for key, value in templates.items()
    }
    updated_inventories = []
    for inventory in data["inventories"]:
        updated_inventories.append(
            dataclasses.replace(
                inventory,
                models=tuple(
                    dataclasses.replace(
                        model,
                        chat_template_hash=template_hashes["a" if index == 0 else "b"],
                    )
                    for index, model in enumerate(inventory.models)
                ),
            )
        )
    j3 = WorkerReference(
        "j3", "physical-j3", "jetson", "valid", "valid", "valid", "valid",
        RUNTIME_COMMIT, True, 8192, 6144,
    )
    data["workers"] = [
        dataclasses.replace(worker, rpc_row="valid") for worker in data["workers"]
    ] + [j3]
    data["inventories"] = updated_inventories + [
        WorkerModelInventory("j3", updated_inventories[0].models)
    ]
    data["prompts"] = [
        PromptVariant(
            ref="same",
            model_ref=alias,
            text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
            template_sha256=template_hashes[alias],
            rendered_input_tokens=4 if alias == "a" else 6,
            input_token_source="fake-template",
            input_tokens_exact=True,
        )
        for alias in ("a", "b")
    ]
    data["spec"] = SweepSpec.from_dict(
        {
            "base": {
                "model_ref": "a",
                "prompt_ref": "same",
                "execution_strategy": "model_parallel_rpc",
                "n_ctx": 1024,
                "max_tokens": 16,
                "requests": 1,
                "warmup_requests": 0,
            },
            "rpc_profiles": [
                {
                    "profile_id": "two-auto-all",
                    "worker_ids": ["j1", "j2"],
                    "coordinator_id": "j1",
                    "split_policy": "auto",
                    "rpc_gpu_layers": "all",
                },
                {
                    "profile_id": "two-equal-all",
                    "worker_ids": ["j1", "j2"],
                    "coordinator_id": "j1",
                    "split_policy": "equal",
                    "rpc_gpu_layers": "all",
                },
                {
                    "profile_id": "three-custom-int",
                    "worker_ids": ["j3", "j1", "j2"],
                    "coordinator_id": "j1",
                    "split_mode": "row",
                    "split_policy": "custom",
                    "weights_by_worker": {"j3": 3, "j1": 1, "j2": 2},
                    "rpc_gpu_layers": 17,
                },
            ],
            "axes": [
                {"name": "model_ref", "values": ["a", "b"]},
                {"name": "n_ctx", "values": [1024, 2048]},
                {
                    "name": "rpc_profile_ref",
                    "values": ["two-auto-all", "two-equal-all", "three-custom-int"],
                },
            ],
        }
    )
    result = preview_catalog_sweep(**data)
    return result.plan, templates


class FakeNative:
    def __init__(self, templates: dict[str, str]) -> None:
        self.templates = templates
        self.config: ExperimentConfig | None = None
        self.commands: list[tuple[str, str, tuple[str, ...]]] = []
        self.requests: list[tuple[str, str]] = []
        self.capabilities = CAPABILITIES
        self.fail_residual = False
        self.wrong_template = False
        self.wrong_checksum = False
        self.token_count: int | None = None

    def runtime(self, worker, action, *args, timeout=0):
        self.commands.append((worker.name, action, args))
        if action == "check":
            return {
                "node": worker.name,
                "ok": True,
                "stdout": f"platform={worker.platform} {self.capabilities}",
                "stderr": "",
            }
        if self.fail_residual and action.startswith("assert-stopped"):
            return {"node": worker.name, "ok": False, "stdout": "port busy", "stderr": ""}
        return {"node": worker.name, "ok": True, "stdout": "ok", "stderr": ""}

    def remote(self, worker, argv, timeout=0):
        assert self.config is not None
        if argv[:2] == ["test", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if argv[:2] == ["sha256sum", "--"]:
            digest = "0" * 64 if self.wrong_checksum else self.config.sweep["model_sha256"]
            return SimpleNamespace(returncode=0, stdout=f"{digest}  {argv[2]}\n", stderr="")
        return SimpleNamespace(returncode=0, stdout=RUNTIME_COMMIT + "\n", stderr="")

    def request(self, url, method="GET", payload=None, timeout=0):
        assert self.config is not None
        self.requests.append((method, url))
        if url.endswith("/props"):
            alias = "a" if "/a-" in self.config.model_id else "b"
            template = "wrong" if self.wrong_template else self.templates[alias]
            coordinator = self.config.rpc_coordinator_node
            return {
                "model_path": f"/home/bench/{coordinator}/llm-cluster/models/{self.config.model_id}",
                "chat_template": template,
                "default_generation_settings": {"n_ctx": self.config.n_ctx},
            }
        if url.endswith("/apply-template"):
            return {"prompt": "<rendered>" + payload["messages"][0]["content"]}
        if url.endswith("/tokenize"):
            count = self.token_count or (10 if "/a-" in self.config.model_id else 12)
            return {"tokens": list(range(count))}
        return {"ok": True}

    def backend(self) -> WorkerRpcBackend:
        return WorkerRpcBackend(self.runtime, self.request, self.remote)


class RpcSweepBindingTests(unittest.TestCase):
    def test_grid_binds_profiles_models_contexts_and_gpu_argv(self) -> None:
        plan, templates = rpc_preview()
        self.assertEqual((plan.counts.valid_cells, len(plan.trials)), (12, 12))
        fake = FakeNative(templates)
        nodes = {name: node(name) for name in ("j1", "j2", "j3")}
        observed = set()
        custom_topology = None
        for trial in plan.trials:
            config = build_cell_config(
                plan,
                trial_id=trial.trial_id,
                sweep_id="rpc-sweep",
                attempt_id="attempt-" + str(trial.generation_order_index),
                prompt_text=TEXT,
            )
            config.validate()
            fake.config = config
            participants = [nodes[name] for name in config.node_names]
            session = fake.backend().start(participants, config, lambda *args, **kwargs: None)
            start_args = next(
                args for name, action, args in reversed(fake.commands)
                if name == config.rpc_coordinator_node and action == "start-coordinator"
            )
            observed.add((config.model_id, config.n_ctx, config.rpc_gpu_layers, start_args[1], start_args[2], start_args[3], start_args[5], start_args[6]))
            self.assertEqual(session.topology["input_preparation"]["input_tokens_exact"], True)
            self.assertNotIn(TEXT, str(session.topology))
            if config.rpc_split_policy.value == "custom":
                custom_topology = copy.deepcopy(session.topology)
            session.close()
        self.assertEqual(len(observed), 12)
        self.assertEqual({item[1] for item in observed}, {1024, 2048})
        self.assertEqual({item[2] for item in observed}, {"all", 17})
        self.assertEqual({item[3] for item in observed}, {
            "/home/bench/j1/llm-cluster/models/family/a-Q4_K_M.gguf",
            "/home/bench/j1/llm-cluster/models/family/b-Q8_0.gguf",
        })
        self.assertEqual({item[4] for item in observed}, {"1024", "2048"})
        self.assertEqual({item[5] for item in observed}, {"all", "17"})
        self.assertEqual({item[6] for item in observed}, {"layer", "row"})
        self.assertEqual({item[7] for item in observed}, {"-", "1,1", "3,2,1"})
        self.assertEqual(custom_topology["requested_weights_by_worker"], {"j3": 3.0, "j1": 1.0, "j2": 2.0})
        self.assertEqual(custom_topology["resolved_device_order"], ["j3", "j2", "j1"])
        self.assertEqual(custom_topology["tensor_split"], [3.0, 2.0, 1.0])
        self.assertIsNone(custom_topology["actual_layer_placement"])

    def test_profile_trace_tamper_and_participant_memory_are_fail_closed(self) -> None:
        plan, _ = rpc_preview()
        config = build_cell_config(
            plan,
            trial_id=plan.trials[0].trial_id,
            sweep_id="rpc-sweep",
            attempt_id="attempt-0",
            prompt_text=TEXT,
        )
        config.sweep["rpc_profile"]["coordinator_id"] = "j2"
        with self.assertRaisesRegex(ValueError, "profile"):
            config.validate()
        self.assertTrue(any(
            check.code == "RPC_PARTICIPANT_MEMORY_ESTIMATE_FITS"
            for cell in plan.cells for check in cell.capabilities
        ))


class RpcSweepNativeGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan, templates = rpc_preview()
        self.config = build_cell_config(
            self.plan,
            trial_id=self.plan.trials[0].trial_id,
            sweep_id="rpc-sweep",
            attempt_id="attempt-0",
            prompt_text=TEXT,
        )
        self.fake = FakeNative(templates)
        self.fake.config = self.config
        self.nodes = [node(name) for name in self.config.node_names]

    def test_unreported_pinned_capability_blocks_before_start(self) -> None:
        self.fake.capabilities = "rpc_split_modes=layer rpc_gpu_layers=all"
        with self.assertRaises(RpcBackendError) as caught:
            self.fake.backend().start(self.nodes, self.config, lambda *args, **kwargs: None)
        self.assertEqual((caught.exception.code, caught.exception.stage), (ErrorCode.RPC_NOT_PREPARED, "rpc_capability"))
        self.assertFalse(any(action == "start-worker" for _, action, _ in self.fake.commands))

    def test_residual_port_blocks_next_cell_before_start(self) -> None:
        self.fake.fail_residual = True
        with self.assertRaises(RpcBackendError) as caught:
            self.fake.backend().start(self.nodes, self.config, lambda *args, **kwargs: None)
        self.assertEqual(caught.exception.stage, "rpc_residual_guard")
        self.assertFalse(any(action == "start-worker" for _, action, _ in self.fake.commands))

    def test_checksum_template_and_context_mismatch_cleanup_attempted_devices(self) -> None:
        cases = ("wrong_checksum", "wrong_template", "token_count")
        for case in cases:
            with self.subTest(case=case):
                fake = FakeNative(self.fake.templates)
                fake.config = self.config
                if case == "token_count":
                    fake.token_count = self.config.n_ctx
                else:
                    setattr(fake, case, True)
                with self.assertRaises(RpcBackendError):
                    fake.backend().start(self.nodes, self.config, lambda *args, **kwargs: None)
                actions = [(name, action) for name, action, _ in fake.commands]
                self.assertIn(("j2", "stop-worker"), actions)
                self.assertIn(("j1", "stop-coordinator"), actions)
                self.assertIn(("j2", "assert-stopped-worker"), actions)
                self.assertIn(("j1", "assert-stopped-coordinator"), actions)

    def test_auto_policy_is_not_rewritten_to_equal(self) -> None:
        config = ExperimentConfig(
            node_names=["j1", "j2"],
            model_id="family/a-Q4_K_M.gguf",
            execution_strategy="model_parallel_rpc",
            rpc_coordinator_node="j1",
            rpc_split_policy="auto",
            acknowledge_experimental_rpc=True,
            warmup_requests=0,
            requests=1,
            concurrency=1,
        )
        config.validate()
        fake = FakeNative(self.fake.templates)
        fake.config = config
        session = fake.backend().start([node("j1"), node("j2")], config, lambda *args, **kwargs: None)
        start_args = next(args for name, action, args in fake.commands if action == "start-coordinator")
        self.assertEqual(start_args[6], "-")
        self.assertEqual(session.topology["tensor_split"], [])
        self.assertEqual(session.topology["requested_weights_by_worker"], {})
        session.close()


if __name__ == "__main__":
    unittest.main()
