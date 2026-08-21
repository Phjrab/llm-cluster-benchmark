"""FOLLOWUP 04 regression locks for Raspberry Pi power integrity."""

from __future__ import annotations

import ast
import inspect
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import cluster.domain.power as power_module
from cluster.benchmark.core import BenchmarkRunner
from cluster.benchmark.executor import ScenarioExecutor
from cluster.clusterctl import Node
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.power import PowerIntegrityStatus, decode_throttled_mask


FIXED_TIME = "2026-08-21T00:00:00+00:00"


def pi_node() -> Node:
    return Node(
        name="pi-01",
        role="worker",
        host="192.168.20.16",
        user="pi",
        ssh_port=22,
        api_port=8000,
        project_dir="/home/pi/llm-cluster-benchmark",
        enabled=True,
        platform="raspberry-pi",
    )


def config() -> ExperimentConfig:
    return ExperimentConfig(
        experiment_id="followup-04",
        node_names=["pi-01"],
        model_id="models/tiny.gguf",
        execution_strategy="single_node",
        requests=1,
        concurrency=1,
        warmup_requests=0,
        n_gpu_layers=0,
    )


def request_result(target, _config, task, warmup=False):
    if warmup:
        raise AssertionError("warmup is disabled")
    return {
        "request_id": task.request_id,
        "logical_request_id": task.logical_request_id,
        "scenario_id": task.scenario_id,
        "replica_index": task.replica_index,
        "node": target.name,
        "assigned_node": target.name,
        "node_host": target.host,
        "started_at": FIXED_TIME,
        "ok": True,
        "ttft_s": 0.1,
        "e2e_s": 0.5,
        "server_ttft_s": 0.08,
        "server_generation_s": 0.4,
        "generated_tokens": 4,
        "tokens_per_s": 10.0,
        "output_chars": 6,
        "output_sha256": "digest",
        "response": "answer",
        "error": "",
        "warmup": False,
    }


def runner(sample_power=None) -> BenchmarkRunner:
    node = pi_node()
    return BenchmarkRunner(
        lambda _node, experiment: {
            "node": node.name,
            "model_id": experiment.model_id,
            "n_ctx": experiment.n_ctx,
            "n_gpu_layers": experiment.n_gpu_layers,
            "n_batch": 64,
        },
        lambda _loaded, _config: [],
        lambda _nodes, _config: None,
        ScenarioExecutor(request_result, lambda *_args: {}),
        mock.Mock(),
        sample_power,
    )


class DecoderAuditTests(unittest.TestCase):
    def test_every_documented_bit_combination_is_deterministic_and_nonblocking(self) -> None:
        bit_positions = (0, 1, 2, 3, 16, 17, 18, 19)
        for combination in range(1 << len(bit_positions)):
            mask = sum(
                1 << position
                for index, position in enumerate(bit_positions)
                if combination & (1 << index)
            )
            with self.subTest(mask=hex(mask)):
                first = decode_throttled_mask(mask, observed_at=FIXED_TIME)
                second = decode_throttled_mask(mask, observed_at=FIXED_TIME)
                expected = (
                    PowerIntegrityStatus.ACTIVE_DEGRADED
                    if mask & 0xF
                    else PowerIntegrityStatus.HISTORY_WARNING
                    if mask & 0xF0000
                    else PowerIntegrityStatus.OK
                )
                self.assertEqual(first.status, expected)
                self.assertFalse(first.blocking)
                self.assertEqual(first.unknown_bits, 0)
                self.assertEqual(first.to_dict(), second.to_dict())
                self.assertEqual(len(first.reason_codes), combination.bit_count())

    def test_power_domain_remains_pure_and_side_effect_free(self) -> None:
        source_path = Path(inspect.getsourcefile(power_module) or "")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        imports = []
        calls = []
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                imports.extend(alias.name for alias in item.names)
            elif isinstance(item, ast.ImportFrom) and item.module:
                imports.append(item.module)
            elif isinstance(item, ast.Call):
                calls.append(getattr(item.func, "id", getattr(item.func, "attr", "")))
        self.assertFalse(
            {name.split(".", 1)[0] for name in imports}
            & {"fastapi", "pydantic", "subprocess", "os", "pathlib", "socket", "urllib"}
        )
        self.assertFalse(
            {"open", "read_text", "write_text", "mkdir", "unlink", "rename"} & set(calls)
        )


class BenchmarkInvariantAuditTests(unittest.TestCase):
    def test_clean_power_observation_is_additive_and_metrics_are_unchanged(self) -> None:
        clean = lambda _node: decode_throttled_mask(0, observed_at=FIXED_TIME)
        summaries = []
        csv_headers = []
        for power_sampler in (None, clean):
            with tempfile.TemporaryDirectory() as directory, mock.patch(
                "cluster.benchmark.core.time.perf_counter",
                side_effect=[100.0, 100.0, 101.0, 101.0],
            ):
                summary = runner(power_sampler).run(config(), [pi_node()], Path(directory))
                summaries.append(summary)
                csv_headers.append(
                    (Path(summary["result_dir"]) / "requests.csv").read_text().splitlines()[0]
                )
        metric_keys = (
            "requests", "logical_requests", "physical_requests", "successful", "failed",
            "success_rate", "wall_s", "requests_per_s", "total_generated_tokens",
            "cluster_tokens_per_s", "ttft_p50_s", "ttft_p95_s", "e2e_p50_s", "e2e_p95_s",
            "all_replicas_success_rate", "answer_agreement_rate", "per_node",
        )
        self.assertEqual(
            {key: summaries[0][key] for key in metric_keys},
            {key: summaries[1][key] for key in metric_keys},
        )
        self.assertEqual(csv_headers[0], csv_headers[1])
        self.assertNotIn("power_integrity", summaries[0])
        self.assertEqual(summaries[1]["measurement_quality"], "clean")

    def test_cancelled_run_keeps_nonblocking_power_evidence(self) -> None:
        cancel = threading.Event()
        cancel.set()
        observations = iter((0x50000, 0x50001))
        sampler = lambda _node: decode_throttled_mask(
            next(observations), observed_at=FIXED_TIME
        )
        with tempfile.TemporaryDirectory() as directory:
            summary = runner(sampler).run(
                config(), [pi_node()], Path(directory), cancel_event=cancel
            )
            saved = json.loads(
                (Path(summary["result_dir"]) / "summary.json").read_text(encoding="utf-8")
            )
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(saved["status"], "cancelled")
        self.assertEqual(saved["measurement_quality"], "degraded")
        evidence = saved["failure"]["evidence"]["last_power_integrity"]["pi-01"]
        self.assertEqual(evidence["status"], "active_degraded")
        self.assertFalse(evidence["blocking"])


if __name__ == "__main__":
    unittest.main()
