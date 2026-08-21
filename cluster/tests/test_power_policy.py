"""Follow-up 02 power policy, event, and result compatibility contracts."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from cluster.application.suite_runner import suite_document
from cluster.benchmark.core import BenchmarkRunner
from cluster.benchmark.executor import ScenarioExecutor
from cluster.benchmark.power import RunPowerIntegrityTracker
from cluster.clusterctl import Node
from cluster.domain.events import EventChannel
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.power import (
    MeasurementQuality,
    PowerWarningCode,
    decode_throttled_mask,
    unavailable_power_integrity,
)
from cluster.domain.worker import WorkerNode


FIXED_TIME = "2026-08-21T00:00:00+00:00"


def snapshot(mask: int, suffix: str = ""):
    return decode_throttled_mask(mask, observed_at=FIXED_TIME + suffix)


def pi_worker(name: str = "pi-01") -> WorkerNode:
    return WorkerNode(
        name=name,
        host="192.168.20.16",
        user="pi",
        ssh_port=22,
        api_port=8000,
        project_dir=f"/home/pi/{name}/llm-cluster",
        platform="raspberry-pi",
    )


class MeasurementQualityTests(unittest.TestCase):
    def summarize(self, records):
        tracker = RunPowerIntegrityTracker()
        for method, node, value in records:
            getattr(tracker, method)(node, value)
        return tracker.summarize()

    def test_all_clean_is_clean(self) -> None:
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0)),
            ("record_pre_measurement", "pi-01", snapshot(0)),
            ("record_measurement_sample", "pi-01", snapshot(0)),
            ("record_postflight", "pi-01", snapshot(0)),
        ])
        self.assertEqual(result["overall"]["quality"], MeasurementQuality.CLEAN.value)
        self.assertEqual(result["overall"]["reason_codes"], [])

    def test_history_only_is_warning(self) -> None:
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0x50000)),
            ("record_pre_measurement", "pi-01", snapshot(0x50000)),
            ("record_measurement_sample", "pi-01", snapshot(0x50000)),
            ("record_postflight", "pi-01", snapshot(0x50000)),
        ])
        node = result["nodes"]["pi-01"]
        self.assertEqual(node["quality"], MeasurementQuality.WARNING.value)
        self.assertIn(PowerWarningCode.PI_UNDERVOLTAGE_HISTORY.value, node["reason_codes"])
        self.assertIn(PowerWarningCode.PI_THROTTLING_HISTORY.value, node["reason_codes"])

    def test_active_measurement_sample_is_degraded(self) -> None:
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0)),
            ("record_pre_measurement", "pi-01", snapshot(0)),
            ("record_measurement_sample", "pi-01", snapshot(0x1)),
            ("record_postflight", "pi-01", snapshot(0x10001)),
        ])
        node = result["nodes"]["pi-01"]
        self.assertEqual(node["quality"], MeasurementQuality.DEGRADED.value)
        self.assertEqual(node["measurement"]["active_warning_samples"], 1)
        self.assertIn(PowerWarningCode.PI_UNDERVOLTAGE_ACTIVE.value, node["reason_codes"])

    def test_new_history_bit_after_run_is_degraded(self) -> None:
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0)),
            ("record_pre_measurement", "pi-01", snapshot(0)),
            ("record_measurement_sample", "pi-01", snapshot(0)),
            ("record_postflight", "pi-01", snapshot(0x10000)),
        ])
        node = result["nodes"]["pi-01"]
        self.assertEqual(node["quality"], MeasurementQuality.DEGRADED.value)
        self.assertIn(
            PowerWarningCode.PI_UNDERVOLTAGE_HISTORY_APPEARED_DURING_RUN.value,
            node["reason_codes"],
        )

    def test_active_postflight_boundary_is_degraded(self) -> None:
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0)),
            ("record_pre_measurement", "pi-01", snapshot(0)),
            ("record_postflight", "pi-01", snapshot(0x1)),
        ])
        self.assertEqual(result["overall"]["quality"], MeasurementQuality.DEGRADED.value)
        self.assertIn(
            PowerWarningCode.PI_UNDERVOLTAGE_ACTIVE.value,
            result["overall"]["reason_codes"],
        )

    def test_all_unavailable_is_unknown(self) -> None:
        unavailable = unavailable_power_integrity(observed_at=FIXED_TIME)
        result = self.summarize([
            ("record_preflight", "pi-01", unavailable),
            ("record_pre_measurement", "pi-01", unavailable),
            ("record_measurement_sample", "pi-01", unavailable),
            ("record_postflight", "pi-01", unavailable),
        ])
        self.assertEqual(result["overall"]["quality"], MeasurementQuality.UNKNOWN.value)
        self.assertEqual(
            result["nodes"]["pi-01"]["reason_codes"],
            [PowerWarningCode.PI_POWER_STATUS_UNAVAILABLE.value],
        )

    def test_clean_valid_with_unavailable_is_warning_incomplete(self) -> None:
        unavailable = unavailable_power_integrity(observed_at=FIXED_TIME)
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0)),
            ("record_pre_measurement", "pi-01", snapshot(0)),
            ("record_measurement_sample", "pi-01", unavailable),
            ("record_postflight", "pi-01", snapshot(0)),
        ])
        self.assertEqual(result["overall"]["quality"], MeasurementQuality.WARNING.value)
        self.assertIn(
            PowerWarningCode.PI_POWER_OBSERVATION_INCOMPLETE.value,
            result["overall"]["reason_codes"],
        )

    def test_multi_node_uses_worst_explicit_severity(self) -> None:
        result = self.summarize([
            ("record_preflight", "pi-01", snapshot(0)),
            ("record_measurement_sample", "pi-01", snapshot(0)),
            ("record_postflight", "pi-01", snapshot(0)),
            ("record_preflight", "pi-02", snapshot(0x50000)),
            ("record_measurement_sample", "pi-02", snapshot(0x50001)),
            ("record_postflight", "pi-02", snapshot(0x50000)),
        ])
        self.assertEqual(result["overall"]["quality"], MeasurementQuality.DEGRADED.value)
        self.assertEqual(result["nodes"]["pi-01"]["quality"], "clean")
        self.assertEqual(result["nodes"]["pi-02"]["quality"], "degraded")


class TransitionAndWarningTests(unittest.TestCase):
    def test_transition_events_are_semantic_and_deduplicated(self) -> None:
        events = []

        def emit(event_type, **payload):
            events.append({"type": event_type, **payload})
            return events[-1]

        tracker = RunPowerIntegrityTracker(emit)
        tracker.record_preflight("pi-01", snapshot(0x50000))
        tracker.record_pre_measurement("pi-01", snapshot(0x50000))
        tracker.record_measurement_sample("pi-01", snapshot(0x50001))
        tracker.record_measurement_sample("pi-01", snapshot(0x50001))
        tracker.record_postflight("pi-01", snapshot(0x50000))

        changes = [event for event in events if event["type"] == "power_integrity_changed"]
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["evidence"]["status"], "active_degraded")
        self.assertEqual(changes[1]["evidence"]["status"], "history_warning")
        self.assertTrue(all(event["evidence"]["blocking"] is False for event in changes))

    def test_warning_records_are_typed_nonblocking_and_deduplicated(self) -> None:
        tracker = RunPowerIntegrityTracker()
        tracker.record_preflight("pi-01", snapshot(0x50000))
        tracker.record_pre_measurement("pi-01", snapshot(0x50000))
        tracker.record_measurement_sample("pi-01", snapshot(0x50000))
        warnings = tracker.warning_records()
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], PowerWarningCode.PI_POWER_HISTORY.value)
        self.assertFalse(warnings[0]["blocking"])

    def test_preflight_event_is_durable_even_without_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from cluster.benchmark.persistence import RunPersistence

            persistence = RunPersistence(
                Path(directory),
                "run_power_crash",
                ExperimentConfig(node_names=["pi-01"]),
            )
            tracker = RunPowerIntegrityTracker(persistence.emit)
            tracker.record_preflight("pi-01", snapshot(0x50000))
            event_path = Path(directory) / "run_power_crash" / "events.jsonl"
            events = [json.loads(line) for line in event_path.read_text().splitlines()]
            self.assertEqual(events[-1]["type"], "power_integrity_snapshot")
            self.assertEqual(events[-1]["channel"], EventChannel.EXPERIMENT.value)
            self.assertEqual(events[-1]["stage"], "preflight")
            self.assertFalse((event_path.parent / "summary.json").exists())


class BenchmarkPowerPersistenceTests(unittest.TestCase):
    def test_completed_run_persists_additive_quality_without_metric_or_csv_change(self) -> None:
        node = pi_worker()
        observations = iter([0x50000, 0x50000, 0x50001, 0x50000])

        def sample_power(_node):
            return snapshot(next(observations))

        def load_model(_node, config):
            return {
                "node": node.name,
                "model_id": config.model_id,
                "n_ctx": config.n_ctx,
                "n_gpu_layers": config.n_gpu_layers,
                "n_batch": 64,
            }

        def stream(target, _config, task, warmup=False):
            self.assertFalse(warmup)
            return {
                "request_id": task.request_id,
                "logical_request_id": task.logical_request_id,
                "scenario_id": task.scenario_id,
                "replica_index": task.replica_index,
                "node": target.name,
                "assigned_node": target.name,
                "started_at": FIXED_TIME,
                "ok": True,
                "ttft_s": 0.1,
                "e2e_s": 0.5,
                "server_generation_s": 0.4,
                "generated_tokens": 4,
                "tokens_per_s": 10.0,
                "output_sha256": "digest",
                "response": "answer",
                "error": "",
            }

        runner = BenchmarkRunner(
            load_model,
            lambda _loaded, _config: [],
            lambda _nodes, _config: None,
            ScenarioExecutor(stream, lambda *_args: {}),
            mock.Mock(),
            sample_power,
        )
        config = ExperimentConfig(
            experiment_id="power-quality",
            node_names=[node.name],
            model_id="models/tiny.gguf",
            execution_strategy="single_node",
            requests=1,
            concurrency=1,
            warmup_requests=0,
            n_gpu_layers=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            summary = runner.run(config, [node], Path(directory), cancel_event=threading.Event())
            run_dir = Path(summary["result_dir"])
            saved = json.loads((run_dir / "summary.json").read_text())
            responses = [
                json.loads(line)
                for line in (run_dir / "responses.jsonl").read_text().splitlines()
            ]
            event_types = [
                json.loads(line)["type"]
                for line in (run_dir / "events.jsonl").read_text().splitlines()
            ]
            header = (run_dir / "requests.csv").read_text().splitlines()[0]

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["measurement_quality"], "degraded")
        self.assertEqual(saved["power_integrity"]["overall"]["quality"], "degraded")
        self.assertIn("measurement_quality_finalized", event_types)
        self.assertIn("power_integrity_changed", event_types)
        self.assertEqual(summary["total_generated_tokens"], 4)
        self.assertEqual(summary["successful"], 1)
        self.assertEqual(
            header,
            "request_id,logical_request_id,scenario_id,replica_index,node,assigned_node,"
            "node_host,started_at,ok,ttft_s,e2e_s,server_ttft_s,server_generation_s,generated_tokens,"
            "tokens_per_s,output_chars,output_sha256,error,warmup",
        )
        self.assertEqual(saved["schema_version"], 2)
        self.assertNotIn("power_integrity", responses[0])
        self.assertEqual(saved["power_integrity"]["warnings"][0]["blocking"], False)

    def test_legacy_non_pi_run_omits_new_fields(self) -> None:
        node = pi_worker("worker-01")
        runner = BenchmarkRunner(
            lambda _node, config: {
                "node": node.name,
                "model_id": config.model_id,
                "n_ctx": config.n_ctx,
                "n_gpu_layers": config.n_gpu_layers,
                "n_batch": 64,
            },
            lambda _loaded, _config: [],
            lambda _nodes, _config: None,
            ScenarioExecutor(
                lambda target, _config, task, warmup=False: {
                    "request_id": task.request_id,
                    "logical_request_id": task.logical_request_id,
                    "scenario_id": task.scenario_id,
                    "replica_index": task.replica_index,
                    "node": target.name,
                    "ok": True,
                    "ttft_s": 0.1,
                    "e2e_s": 0.2,
                    "generated_tokens": 1,
                    "tokens_per_s": 5.0,
                    "output_sha256": "digest",
                    "response": "ok",
                    "error": "",
                },
                lambda *_args: {},
            ),
            mock.Mock(),
        )
        config = ExperimentConfig(
            node_names=[node.name],
            model_id="models/tiny.gguf",
            execution_strategy="single_node",
            requests=1,
            warmup_requests=0,
            n_gpu_layers=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            summary = runner.run(config, [node], Path(directory))
        self.assertNotIn("measurement_quality", summary)
        self.assertNotIn("power_integrity", summary)

    def test_failed_run_keeps_status_and_adds_last_power_evidence(self) -> None:
        node = pi_worker()
        observations = iter([snapshot(0x50000), snapshot(0x50001)])
        runner = BenchmarkRunner(
            lambda _node, _config: (_ for _ in ()).throw(RuntimeError("load failed")),
            lambda _loaded, _config: [],
            lambda _nodes, _config: None,
            ScenarioExecutor(lambda *_args: {}, lambda *_args: {}),
            mock.Mock(),
            lambda _node: next(observations),
        )
        config = ExperimentConfig(
            node_names=[node.name],
            model_id="models/tiny.gguf",
            execution_strategy="single_node",
            requests=1,
            warmup_requests=0,
            n_gpu_layers=0,
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Failed to load model"):
                runner.run(config, [node], Path(directory))
            summaries = list(Path(directory).glob("*/summary.json"))
            saved = json.loads(summaries[0].read_text())
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["measurement_quality"], "degraded")
        evidence = saved["failure"]["evidence"]["last_power_integrity"]
        self.assertEqual(evidence[node.name]["status"], "active_degraded")

    def test_suite_quality_is_additive_and_does_not_change_status(self) -> None:
        suite = suite_document(
            suite_id="suite_power",
            experiment_id="experiment_power",
            name="Power suite",
            status="completed",
            model_ids=["a.gguf", "b.gguf"],
            attempted_models=2,
            completed_models=2,
            total_work_units=2,
            completed_work_units=2,
            continue_on_model_error=True,
            model_cooldown_s=0,
            started_at=FIXED_TIME,
            summaries=[
                {"model_index": 1, "status": "completed", "measurement_quality": "clean"},
                {"model_index": 2, "status": "completed", "measurement_quality": "degraded"},
            ],
            errors=[],
        )
        self.assertEqual(suite["status"], "completed")
        self.assertEqual(suite["measurement_quality"], "degraded")
        self.assertEqual(suite["measurement_quality_counts"]["degraded"], 1)
        self.assertEqual(suite["measurement_quality_counts"]["clean"], 1)


class ControllerPowerPolicyTests(unittest.TestCase):
    @staticmethod
    def node() -> Node:
        return Node(
            name="pi-01",
            role="worker",
            host="192.168.20.16",
            user="pi",
            ssh_port=22,
            api_port=8000,
            project_dir="/home/pi/llm-cluster",
            enabled=True,
            platform="raspberry-pi",
        )

    @staticmethod
    def environment_report(backend_verified: bool = True) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "node": "pi-01",
            "status": "ready",
            "checked_at": now,
            "received_at": now,
            "platform": "raspberry-pi",
            "backend": {"kind": "cpu-blas", "verified": backend_verified},
        }

    def test_preflight_warning_is_additive_and_nonblocking(self) -> None:
        from cluster.dashboard.services import experiment_power_warnings

        node = self.node()
        warnings = experiment_power_warnings(
            [node],
            {
                node.name: {
                    "profile": {"platform_kind": "raspberry-pi"},
                    "power_integrity": snapshot(0x50000).to_dict(),
                }
            },
        )
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["code"], PowerWarningCode.PI_POWER_HISTORY.value)
        self.assertFalse(warnings[0]["blocking"])

    def test_power_history_active_and_unavailable_do_not_block_ready_preflight(self) -> None:
        from cluster.dashboard import services

        node = self.node()
        with mock.patch.object(
            services, "read_environment_reports", return_value=[self.environment_report()]
        ):
            for power in (
                snapshot(0x50000),
                snapshot(0x50001),
                unavailable_power_integrity(observed_at=FIXED_TIME),
            ):
                with self.subTest(status=power.status.value):
                    services.validate_experiment_environment(
                        [node],
                        {
                            node.name: {
                                "api": True,
                                "model_ids": ["models/tiny.gguf"],
                                "power_integrity": power.to_dict(),
                            }
                        },
                        ["models/tiny.gguf"],
                        "single_node",
                    )

    def test_power_warning_does_not_hide_existing_blocking_preflight_failures(self) -> None:
        from cluster.dashboard import services
        from cluster.application.model_service import ModelPreflightError

        node = self.node()
        power = snapshot(0x50000).to_dict()
        with mock.patch.object(
            services, "read_environment_reports", return_value=[self.environment_report()]
        ):
            with self.assertRaisesRegex(ValueError, "오프라인"):
                services.validate_experiment_environment(
                    [node], {node.name: {"api": False, "power_integrity": power}},
                    ["models/tiny.gguf"], "single_node",
                )
            with self.assertRaises(ModelPreflightError):
                services.validate_experiment_environment(
                    [node], {node.name: {"api": True, "model_ids": [], "power_integrity": power}},
                    ["models/tiny.gguf"], "single_node",
                )
        with mock.patch.object(
            services,
            "read_environment_reports",
            return_value=[self.environment_report(backend_verified=False)],
        ):
            with self.assertRaisesRegex(ValueError, "백엔드"):
                services.validate_experiment_environment(
                    [node],
                    {node.name: {"api": True, "model_ids": ["models/tiny.gguf"], "power_integrity": power}},
                    ["models/tiny.gguf"],
                    "single_node",
                )

    def test_system_monitor_emits_only_power_transitions(self) -> None:
        from cluster.dashboard import services

        node = mock.Mock(name="node")
        node.name = "pi-01"
        monitor = services.StatusMonitor()
        states = [snapshot(0x50000), snapshot(0x50000), snapshot(0x50001)]

        def probe(_node):
            return {
                "name": "pi-01",
                "power_integrity": states.pop(0).to_dict(),
            }

        with mock.patch.object(services, "read_all_nodes", return_value=[node]), mock.patch.object(
            services, "probe_node", side_effect=probe
        ), mock.patch.object(services.events, "publish") as publish:
            monitor.refresh_now()
            monitor.refresh_now()
            monitor.refresh_now()

        power_calls = [
            call
            for call in publish.call_args_list
            if call.args and call.args[0] == "power_integrity_status"
        ]
        self.assertEqual(len(power_calls), 2)
        self.assertEqual(power_calls[0].kwargs["channel"], EventChannel.SYSTEM)
        self.assertIsNone(power_calls[0].kwargs["previous_status"])
        self.assertEqual(power_calls[1].kwargs["status"], "active_degraded")

    def test_controller_normalizes_new_and_legacy_pi_worker_health(self) -> None:
        from cluster.dashboard import services

        node = self.node()
        base_health = {
            "ok": True,
            "telemetry_version": 2,
            "node": {"name": node.name},
            "profile": {"platform_kind": "raspberry-pi"},
            "capabilities": {"inference_ready": True},
            "model_count": 0,
            "model_ids": [],
        }
        with mock.patch.object(
            services,
            "request_json",
            return_value={**base_health, "power_integrity": snapshot(0x50000).to_dict()},
        ):
            current = services.probe_node(node)
        self.assertEqual(current["power_integrity"]["status"], "history_warning")
        self.assertFalse(current["power_integrity"]["current"]["undervoltage"])

        with mock.patch.object(services, "request_json", return_value=base_health):
            legacy = services.probe_node(node)
        self.assertEqual(legacy["power_integrity"]["status"], "unavailable")
        self.assertFalse(legacy["power_integrity"]["blocking"])


if __name__ == "__main__":
    unittest.main()
