"""S10 integration acceptance tests use loopback and injected fake runtimes only."""

from __future__ import annotations

import copy
import dataclasses
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cluster.application.sweep_models import build_cell_config, preview_catalog_sweep
from cluster.application.sweep_planner import compile_plan
from cluster.application.sweep_runner import SweepRepository, SweepSupervisor
from cluster.application.jobs import JobService
from cluster.benchmark.core import BenchmarkRunner
from cluster.benchmark.executor import ScenarioExecutor
from cluster.benchmark.runner import _load_model
from cluster.benchmark.transport import stream_rpc_request, stream_worker_request
from cluster.domain.sweep import SweepSpec
from cluster.domain.worker import WorkerNode
from cluster.tests.test_sweep_models import TEXT, fixtures
from cluster.tests.test_sweep_rpc import FakeNative, node, rpc_preview
from cluster.tests.test_sweep_runner import FakeBackend, no_drift


class LoopbackWorker:
    """Minimal HTTP Worker exercising the real urllib transport boundary."""

    def __init__(self, model_sha256_by_id: dict[str, str]) -> None:
        self.model_sha256_by_id = model_sha256_by_id
        self.selected: list[dict[str, object]] = []
        self.prepared: list[dict[str, object]] = []
        self.streamed: list[dict[str, object]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _json(self) -> dict[str, object]:
                length = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(length) or b"{}")

            def _write_json(self, value: dict[str, object]) -> None:
                data = json.dumps(value).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path != "/cluster/health":
                    self.send_error(404)
                    return
                self._write_json({
                    "node": {"hostname": "loopback-worker"},
                    "profile": {
                        "platform_kind": "jetson",
                        "runtime_backend": {"kind": "fake-loopback", "verified": True},
                    },
                    "capabilities": {},
                })

            def do_POST(self):
                payload = self._json()
                if self.path == "/api/select-model":
                    owner.selected.append(payload)
                    model_id = str(payload["model_id"])
                    n_ctx = int(payload["n_ctx"])
                    n_gpu_layers = int(payload["n_gpu_layers"])
                    self._write_json({
                        "ok": True,
                        "current": {
                            "model_id": model_id,
                            "model_sha256": owner.model_sha256_by_id[model_id],
                            "model_size_bytes": 4,
                            "factory_config": {
                                "n_ctx": n_ctx,
                                "n_gpu_layers": n_gpu_layers,
                            },
                            "effective_config": {"n_ctx": n_ctx},
                            "adjustment_reasons": [],
                        },
                    })
                    return
                if self.path == "/cluster/input/prepare":
                    owner.prepared.append(payload)
                    selected = owner.selected[-1]
                    model_id = str(selected["model_id"])
                    input_tokens = 4 if "/a-" in model_id else 6
                    self._write_json({
                        "ok": True,
                        "preparation": {
                            "preparation_id": payload["preparation_id"],
                            "prompt_sha256": payload["prompt_sha256"],
                            "template_hash": payload["template_sha256"],
                            "model_sha256": payload["model_sha256"],
                            "effective_n_ctx": selected["n_ctx"],
                            "output_reserve_tokens": payload["max_tokens"],
                            "input_token_source": "prepared_chat_template",
                            "input_tokens": input_tokens,
                            "input_tokens_exact": True,
                        },
                    })
                    return
                if self.path == "/cluster/chat/stream":
                    owner.streamed.append(payload)
                    selected = owner.selected[-1]
                    model_id = str(selected["model_id"])
                    input_tokens = 4 if "/a-" in model_id else 6
                    events = (
                        'data: {"type":"token","text":"ok"}\n\n'
                        "data: "
                        + json.dumps({
                            "type": "done",
                            "metrics": {
                                "generated_tokens": 1,
                                "generation_s": 0.001,
                                "input_tokens": input_tokens,
                                "total_tokens": input_tokens + 1,
                                "input_token_source": "prepared_chat_template",
                                "input_tokens_exact": True,
                                "output_tokens_exact": True,
                                "finish_reason": "stop",
                                "requested_n_ctx": selected["n_ctx"],
                                "effective_n_ctx": selected["n_ctx"],
                                "requested_max_tokens": payload["max_tokens"],
                                "effective_max_tokens": payload["max_tokens"],
                            },
                        })
                        + "\n\n"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(events)))
                    self.end_headers()
                    self.wfile.write(events)
                    return
                self.send_error(404)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def grid_108_plan():
    data = fixtures()
    base = data["spec"].base.to_dict()
    base.update(worker_ids=["j1"], requests=1, warmup_requests=0)
    data["spec"] = SweepSpec.from_dict({
        "base": base,
        "axes": [
            {"name": "model_ref", "values": ["a", "b"]},
            {"name": "n_ctx", "values": [1024, 2048, 4096]},
            {"name": "concurrency", "values": [1, 3, 6]},
            {"name": "max_tokens", "values": [64, 128]},
        ],
        "repeat_count": 3,
    })
    return preview_catalog_sweep(**data).plan


def three_pool_plan():
    data = fixtures()
    data["workers"][1] = dataclasses.replace(
        data["workers"][1], platform="raspberry-pi"
    )
    data["catalog"] = [
        dataclasses.replace(
            item, verified_platforms=("jetson", "raspberry-pi")
        )
        for item in data["catalog"]
    ]
    base = data["spec"].base.to_dict()
    data["spec"] = SweepSpec.from_dict({
        "base": base,
        "combination": "explicit",
        "explicit": [
            {**base, "model_ref": "a", "worker_ids": ["j1"], "n_ctx": 128},
            {**base, "model_ref": "b", "worker_ids": ["j2"], "n_ctx": 256},
            {**base, "model_ref": "b", "worker_ids": ["j1"], "n_ctx": 512},
        ],
        "execution": {
            "mode": "disjoint_parallel",
            "max_parallel_jobs": 2,
            "backfill_policy": "bounded",
            "backfill_window": 8,
            "failure_policy": "continue_ready",
        },
    })
    return preview_catalog_sweep(**data).plan


class S10LoopbackIntegrationTests(unittest.TestCase):
    def test_108_preview_budget_and_concrete_runner_use_loopback_worker(self):
        plan = grid_108_plan()
        self.assertEqual(
            (plan.counts.unique_cells, plan.counts.trials),
            (36, 108),
        )
        self.assertEqual(plan.counts.workload.logical_requests, 108)
        self.assertEqual(plan.counts.workload.model_loads, 108)

        model_hashes = {
            cell.model.model_id: cell.model.artifact_sha256
            for cell in plan.cells
            if cell.model is not None
        }
        representatives = (plan.trials[0], plan.trials[-1])
        with tempfile.TemporaryDirectory() as directory, LoopbackWorker(model_hashes) as worker:
            runtime = WorkerNode(
                "j1", "127.0.0.1", "fake", 22, worker.port,
                "/home/fake/llm-cluster", platform="jetson",
            )
            runner = BenchmarkRunner(
                _load_model,
                lambda _loaded, _config: [],
                lambda _nodes, _config: None,
                ScenarioExecutor(stream_worker_request, stream_rpc_request),
                mock.Mock(side_effect=AssertionError("RPC backend not expected")),
            )
            observed = []
            for index, trial in enumerate(representatives):
                config = build_cell_config(
                    plan,
                    trial_id=trial.trial_id,
                    sweep_id="sweep-s10-loopback",
                    attempt_id=f"attempt-s10-{index}",
                    prompt_text=TEXT,
                )
                summary = runner.run(config, [runtime], Path(directory) / "results")
                self.assertEqual(summary["status"], "completed")
                observed.append((config.model_id, config.n_ctx, config.max_tokens))

        self.assertEqual(len(worker.selected), 2)
        self.assertEqual(len(worker.prepared), 2)
        self.assertEqual(len(worker.streamed), 2)
        self.assertEqual(len({item[0] for item in observed}), 2)
        self.assertEqual({item[1] for item in observed}, {1024, 4096})
        self.assertEqual({item[2] for item in observed}, {64, 128})

    def test_36_rpc_trials_bind_profile_arguments_and_cleanup(self):
        base_plan, templates = rpc_preview()
        raw = base_plan.spec.to_dict()
        raw["repeat_count"] = 3
        plan = compile_plan(SweepSpec.from_dict(raw), base_plan.context)
        self.assertEqual((plan.counts.unique_cells, plan.counts.trials), (12, 36))

        fake = FakeNative(templates)
        nodes = {name: node(name) for name in ("j1", "j2", "j3")}
        observed = set()
        for index, trial in enumerate(plan.trials):
            config = build_cell_config(
                plan,
                trial_id=trial.trial_id,
                sweep_id="sweep-s10-rpc",
                attempt_id=f"attempt-s10-rpc-{index}",
                prompt_text=TEXT,
            )
            fake.config = config
            session = fake.backend().start(
                [nodes[name] for name in config.node_names],
                config,
                lambda *_args, **_kwargs: None,
            )
            start_args = next(
                args for name, action, args in reversed(fake.commands)
                if name == config.rpc_coordinator_node and action == "start-coordinator"
            )
            observed.add((
                config.model_id,
                config.n_ctx,
                str(config.rpc_split_mode),
                str(config.rpc_split_policy),
                tuple(config.node_names),
                start_args[5],
                start_args[6],
            ))
            session.close()
            self.assertEqual(session.topology["cleanup_status"], "completed")

        self.assertEqual(len(observed), 12)
        self.assertEqual({item[1] for item in observed}, {1024, 2048})
        self.assertEqual({item[2] for item in observed}, {"layer", "row"})
        self.assertEqual({item[3] for item in observed}, {"auto", "equal", "custom"})
        self.assertEqual({item[5] for item in observed}, {"layer", "row"})
        self.assertEqual({item[6] for item in observed}, {"-", "1,1", "3,2,1"})


class S10DurabilityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = SweepRepository(Path(self.temporary.name) / "sweeps")
        self.backend = FakeBackend()

    def supervisor(self):
        return SweepSupervisor(self.repository, self.backend, no_drift)

    def test_disjoint_a_b_overlap_c_waits_and_targeted_cancel_keeps_b(self):
        plan = three_pool_plan()
        supervisor = self.supervisor()
        supervisor.create("sweep-s10-pools", plan)
        active = supervisor.tick("sweep-s10-pools")
        self.assertEqual(active["coverage"]["running"], 2)
        self.assertEqual(active["coverage"]["pending"], 1)
        self.assertEqual(plan.cells[0].workers[0].platform, "jetson")
        self.assertEqual(plan.cells[1].workers[0].platform, "raspberry-pi")
        first, second, third = active["trials"]
        self.assertEqual(third["status"], "pending")
        first_attempt = first["attempts"][-1]
        second_attempt = copy.deepcopy(second["attempts"][-1])

        self.backend.cancel(first_attempt)
        restarted = self.supervisor()
        after_cancel = restarted.tick("sweep-s10-pools")
        self.assertEqual(after_cancel["trials"][0]["status"], "cancelled")
        after_dispatch = restarted.tick("sweep-s10-pools")
        self.assertEqual(after_dispatch["trials"][1]["attempts"][-1], second_attempt)
        self.assertEqual(after_dispatch["trials"][1]["status"], "running")
        self.assertEqual(after_dispatch["trials"][2]["status"], "running")
        self.assertEqual(len(self.backend.cancels), 1)

    def test_restart_response_loss_crash_claim_result_recovery_and_cleanup_timeout(self):
        plan = three_pool_plan()
        supervisor = self.supervisor()
        supervisor.create("sweep-s10-recovery", plan)
        self.backend.lose_next_response = True
        active = supervisor.tick("sweep-s10-recovery")
        self.assertEqual(active["coverage"]["running"], 2)
        original_starts = list(self.backend.starts)

        restarted = self.supervisor()
        restarted.tick("sweep-s10-recovery")
        self.assertEqual(self.backend.starts, original_starts)

        first_job = active["trials"][0]["attempts"][-1]["backend_job_id"]
        self.backend.finish(first_job)
        reconciled = restarted.tick("sweep-s10-recovery")
        self.assertEqual(reconciled["trials"][0]["status"], "completed")
        self.assertEqual(len(reconciled["trials"][0]["attempts"]), 1)

        claimed = restarted._claim(
            "sweep-s10-recovery", reconciled["trials"][2]["trial_id"]
        )
        claim = claimed["trials"][2]["attempts"][-1]
        recovered = self.supervisor().tick("sweep-s10-recovery")
        self.assertEqual(recovered["trials"][2]["attempts"][-1]["attempt_id"], claim["attempt_id"])
        self.assertEqual(recovered["trials"][2]["attempts"][-1]["backend_job_id"], claim["backend_job_id"])

        third_job = claim["backend_job_id"]
        self.backend.finish(third_job, cleanup="quarantined")
        paused = self.supervisor().tick("sweep-s10-recovery")
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["trials"][2]["status"], "needs_reconciliation")

    def test_partial_rpc_start_cleans_all_attempted_devices(self):
        plan, templates = rpc_preview()
        config = build_cell_config(
            plan,
            trial_id=plan.trials[0].trial_id,
            sweep_id="sweep-s10-partial-rpc",
            attempt_id="attempt-s10-partial-rpc",
            prompt_text=TEXT,
        )
        fake = FakeNative(templates)
        fake.config = config
        original_runtime = fake.runtime

        def partial(worker, action, *args, timeout=0):
            if action == "start-coordinator":
                fake.commands.append((worker.name, action, args))
                raise TimeoutError("synthetic partial start timeout")
            return original_runtime(worker, action, *args, timeout=timeout)

        fake.runtime = partial
        with self.assertRaises(Exception):
            fake.backend().start(
                [node(name) for name in config.node_names],
                config,
                lambda *_args, **_kwargs: None,
            )
        actions = {(name, action) for name, action, _args in fake.commands}
        self.assertIn(("j2", "stop-worker"), actions)
        self.assertIn(("j1", "stop-coordinator"), actions)

    def test_old_worker_parallel_is_blocked_and_default_single_job_still_starts(self):
        class Child:
            pid = 424242

            def poll(self):
                return None

        root = Path(self.temporary.name)
        service = JobService(
            root / "jobs",
            root / "missing.csv",
            root / "results",
            Path(__file__).resolve().parents[2],
            start_watcher=False,
        )
        parallel = {
            "job_id": "job-old-worker-parallel",
            "suite_id": "suite-old-worker-parallel",
            "status": "queued",
            "phase": "queued",
            "nodes": ["old-worker"],
            "max_parallel_jobs": 2,
        }
        with mock.patch("cluster.application.jobs.subprocess.Popen") as popen:
            with self.assertRaisesRegex(ValueError, "WORKER_OWNERSHIP_UPGRADE_REQUIRED"):
                service.start(parallel)
            popen.assert_not_called()

        compatible = {
            **parallel,
            "job_id": "job-old-worker-exclusive",
            "suite_id": "suite-old-worker-exclusive",
            "max_parallel_jobs": 1,
        }
        with mock.patch(
            "cluster.application.jobs.subprocess.Popen", return_value=Child()
        ) as popen:
            started = service.start(compatible)
        self.assertEqual(started["max_parallel_jobs"], 1)
        self.assertEqual(popen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
