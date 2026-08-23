from __future__ import annotations

import csv
import json
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from cluster.benchmark.instrumentation import (
    RunInstrumentation,
    normalize_telemetry_sample,
    request_measurement,
    rpc_lifecycle_measurement,
    summarize_measurements,
)
from cluster.benchmark.rpc import RpcSession
from cluster.benchmark.transport import stream_worker_request
from cluster.benchmark.models import RequestTask
from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.storage import FilesystemRunRepository


class MeasurementNormalizationTests(unittest.TestCase):
    def test_request_metrics_preserve_measured_zero_and_unavailable(self) -> None:
        measured = request_measurement(
            "run_01",
            {
                "request_id": 1,
                "scenario_id": "single",
                "node": "pi-02",
                "ok": True,
                "generated_tokens": 1,
                "input_tokens": 0,
                "input_token_source": "tokenizer",
                "prefill_time_s": 0.0,
                "decode_time_s": None,
                "bytes_sent": 0,
                "bytes_received": 12,
            },
        )
        self.assertEqual(measured["input_tokens"], 0)
        self.assertTrue(measured["availability"]["input_tokens"]["available"])
        self.assertTrue(measured["availability"]["prefill_time_s"]["available"])
        self.assertFalse(measured["availability"]["decode_time_s"]["available"])
        self.assertIsNone(measured["rtt_s"])
        self.assertEqual(
            measured["availability"]["rtt_s"]["reason"],
            "dedicated_rtt_probe_not_enabled",
        )

    def test_telemetry_sample_has_monotonic_identity_and_collection_overhead(self) -> None:
        node = SimpleNamespace(name="jetson-01")
        sample = normalize_telemetry_sample(
            run_id="run_01",
            scenario_id="single",
            node=node,
            raw={
                "metrics": {
                    "sampled_at": "2026-08-23T00:00:00+00:00",
                    "power": {"total_w": 12.5},
                    "temperatures_c": {"soc": 51.0},
                    "cpu": {"frequency_mhz": 1728},
                    "network": {"bytes_sent": 10, "bytes_received": 20},
                    "telemetry_collection_overhead_s": 0.004,
                },
                "power_integrity": {
                    "available": True,
                    "current": {"undervoltage": False, "throttled": False},
                },
            },
            run_started_monotonic=100.0,
            probe_started=101.0,
            probe_finished=101.025,
        )
        self.assertEqual(sample["run_id"], "run_01")
        self.assertEqual(sample["scenario_id"], "single")
        self.assertEqual(sample["node"], "jetson-01")
        self.assertEqual(sample["monotonic_elapsed_s"], 1.0125)
        self.assertEqual(sample["collection_overhead_s"], 0.025)
        self.assertEqual(sample["worker_collection_overhead_s"], 0.004)
        self.assertEqual(sample["power_w"], 12.5)
        self.assertFalse(sample["throttled"])
        self.assertTrue(sample["throttling_supported"])


class MeasurementSummaryTests(unittest.TestCase):
    def test_energy_thermal_frequency_network_and_zero_throttle_summary(self) -> None:
        samples = [
            {
                "node": "pi-02", "sample_kind": "idle", "power_w": 3.0,
                "monotonic_elapsed_s": -1.0, "collection_overhead_s": 0.01,
            },
            {
                "node": "pi-02", "sample_kind": "measurement", "power_w": 10.0,
                "monotonic_elapsed_s": 0.0, "temperatures_c": {"soc": 50.0},
                "cpu_frequency_mhz": 1800, "network_bytes_sent": 100,
                "network_bytes_received": 200, "throttling_supported": True,
                "throttled": False, "collection_overhead_s": 0.02,
            },
            {
                "node": "pi-02", "sample_kind": "measurement", "power_w": 20.0,
                "monotonic_elapsed_s": 2.0, "temperatures_c": {"soc": 60.0},
                "cpu_frequency_mhz": 1600, "network_bytes_sent": 500,
                "network_bytes_received": 1000, "throttling_supported": True,
                "throttled": False, "collection_overhead_s": 0.03,
            },
        ]
        requests = [
            {"node": "pi-02", "ok": True, "generated_tokens": 30},
            {"node": "pi-02", "ok": True, "generated_tokens": 15},
        ]
        summary = summarize_measurements(samples, requests)
        node = summary["nodes"]["pi-02"]
        self.assertEqual(node["idle_power_w"], 3.0)
        self.assertEqual(node["average_power_w"], 15.0)
        self.assertEqual(node["peak_power_w"], 20.0)
        self.assertEqual(node["energy_j"], 30.0)
        self.assertEqual(node["generated_tokens_per_j"], 1.5)
        self.assertEqual(node["requests_per_j"], round(2 / 30, 9))
        self.assertEqual(node["start_temperature_c"], 50.0)
        self.assertEqual(node["mean_temperature_c"], 55.0)
        self.assertEqual(node["peak_temperature_c"], 60.0)
        self.assertEqual(node["end_temperature_c"], 60.0)
        self.assertEqual(node["throttling_sample_count"], 0)
        self.assertEqual(len(node["frequency_samples"]), 2)
        self.assertEqual(node["bytes_sent"], 400)
        self.assertEqual(node["bytes_received"], 800)
        self.assertEqual(node["effective_bandwidth_bytes_s"], 600.0)
        self.assertIsNone(node["steady_state_start"])
        self.assertEqual(
            node["availability"]["steady_state_start"]["reason"],
            "phase_09_steady_state_rule_not_frozen",
        )

    def test_energy_is_null_when_sensor_or_second_sample_is_missing(self) -> None:
        summary = summarize_measurements(
            [{"node": "pi-02", "sample_kind": "measurement", "power_w": None}],
            [{"node": "pi-02", "ok": True, "generated_tokens": 5}],
        )
        node = summary["nodes"]["pi-02"]
        self.assertIsNone(node["average_power_w"])
        self.assertIsNone(node["energy_j"])
        self.assertIsNone(node["generated_tokens_per_j"])
        self.assertFalse(node["availability"]["energy_j"]["available"])

    def test_energy_does_not_integrate_unsampled_gap_between_scenarios(self) -> None:
        samples = [
            {"node": "pi-02", "scenario_id": "one", "sample_kind": "measurement", "power_w": 10, "monotonic_elapsed_s": 0},
            {"node": "pi-02", "scenario_id": "one", "sample_kind": "measurement", "power_w": 10, "monotonic_elapsed_s": 1},
            {"node": "pi-02", "scenario_id": "two", "sample_kind": "measurement", "power_w": 20, "monotonic_elapsed_s": 100},
            {"node": "pi-02", "scenario_id": "two", "sample_kind": "measurement", "power_w": 20, "monotonic_elapsed_s": 101},
        ]
        summary = summarize_measurements(samples, [])
        self.assertEqual(summary["nodes"]["pi-02"]["energy_j"], 30.0)

    def test_rpc_lifecycle_fields_are_additive(self) -> None:
        summary = summarize_measurements(
            [], [], rpc_topology={"model_load_s": 4.5, "cleanup_s": 0.75}
        )
        self.assertEqual(summary["rpc"]["model_load_distribution_s"], 4.5)
        self.assertEqual(summary["rpc"]["cleanup_s"], 0.75)
        self.assertIsNone(summary["rpc"]["coordinator_wait_s"])


class MeasurementPersistenceTests(unittest.TestCase):
    def test_measurement_journal_is_private_and_csv_stays_exactly_19_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = FilesystemRunRepository(Path(directory) / "results")
            run_id = "20260823_120000_ab12"
            run_dir = repository.create(run_id, {"model_id": "tiny.gguf"})
            repository.append_measurement(
                run_id,
                {"schema_version": 1, "record_type": "telemetry_sample", "power_w": None},
            )
            repository.append_measurement(run_id, {"schema_version": 1, "record_type": "request_metrics"})
            repository.write_requests(run_id, [{"request_id": 1, "node": "pi-02", "ok": True}])
            self.assertEqual(len(repository.read_measurements(run_id)), 2)
            path = run_dir / "measurements.jsonl"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with (run_dir / "requests.csv").open(newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle))
            self.assertEqual(len(header), 19)
            self.assertNotIn("energy_j", header)
            with path.open(encoding="utf-8") as handle:
                self.assertEqual(json.loads(next(handle))["power_w"], None)

    def test_sampler_persists_identity_and_overhead(self) -> None:
        records: list[dict[str, object]] = []
        node = SimpleNamespace(name="pi-02")

        def probe(_node: object) -> dict[str, object]:
            return {"metrics": {"power_w": None, "temperatures_c": {}}}

        tracker = RunInstrumentation("run_01", records.append, probe, interval_s=0.05)
        tracker.capture_idle([node])
        tracker.start_scenario("single", [node])
        time.sleep(0.08)
        tracker.stop_scenario()
        self.assertGreaterEqual(len(records), 2)
        self.assertEqual(records[0]["sample_kind"], "idle")
        self.assertEqual(records[1]["scenario_id"], "single")
        self.assertGreaterEqual(float(records[1]["collection_overhead_s"]), 0.0)

    def test_rpc_session_records_cleanup_duration_even_when_cleanup_fails(self) -> None:
        topology: dict[str, object] = {}
        session = RpcSession(
            SimpleNamespace(name="jetson-01"),
            "http://127.0.0.1:18080",
            topology,
            [],
            lambda: ["stop failed"],
        )
        with self.assertRaises(Exception):
            session.close()
        self.assertEqual(topology["cleanup_status"], "failed")
        self.assertGreaterEqual(float(topology["cleanup_s"]), 0.0)
        lifecycle = rpc_lifecycle_measurement("run_01", topology)
        self.assertEqual(lifecycle["record_type"], "rpc_lifecycle")
        self.assertEqual(lifecycle["node"], None)
        self.assertTrue(lifecycle["availability"]["cleanup_s"]["available"])


class TransportInstrumentationTests(unittest.TestCase):
    def test_worker_transport_counts_body_bytes_and_preserves_server_timing(self) -> None:
        lines = [
            b'data: {"type":"token","text":"ok"}\n\n',
            (
                b'data: {"type":"done","metrics":{"generated_tokens":2,'
                b'"input_tokens":4,"input_token_source":"tokenizer",'
                b'"input_tokens_exact":true,"prefill_time_s":0.2,'
                b'"prefill_tokens_per_s":20.0,"decode_time_s":0.3,'
                b'"decode_tokens_per_s":3.333333,"total_tokens":6}}\n\n'
            ),
        ]

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter(lines)

        config = ExperimentConfig(
            node_names=["pi-02"],
            model_id="tiny.gguf",
            requests=1,
            concurrency=1,
            max_tokens=2,
            warmup_requests=0,
            execution_strategy="single_node",
        )
        node = SimpleNamespace(
            name="pi-02",
            host="192.168.0.16",
            api_url="http://192.168.0.16:8000",
        )
        with mock.patch(
            "cluster.benchmark.transport.urllib.request.urlopen", return_value=Response()
        ):
            record = stream_worker_request(
                node, config, RequestTask(1, 1, "single", "pi-02")
            )
        self.assertTrue(record["ok"])
        self.assertEqual(record["input_tokens"], 4)
        self.assertEqual(record["total_tokens"], 6)
        self.assertEqual(record["prefill_time_s"], 0.2)
        self.assertEqual(record["decode_time_s"], 0.3)
        self.assertGreater(record["bytes_sent"], 0)
        self.assertEqual(record["bytes_received"], sum(map(len, lines)))
        self.assertGreater(record["effective_bandwidth_bytes_s"], 0)
        self.assertIsNone(record["rtt_s"])


if __name__ == "__main__":
    unittest.main()
