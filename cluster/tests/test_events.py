from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cluster.domain.events import ClusterEvent, EventChannel
from cluster.domain.experiment import ExperimentConfig
from cluster.benchmark.persistence import RunPersistence


class TypedEventTests(unittest.TestCase):
    def test_event_envelope_serializes_legacy_fields_with_additive_channel(self) -> None:
        event = ClusterEvent.create(
            EventChannel.EXPERIMENT,
            "request_completed",
            "2026-08-20T00:00:00+00:00",
            run_id="run_01",
            suite_id="suite_01",
            model_id="models/example.gguf",
            scenario_id="main",
            completed=1,
            total=2,
        )
        wire = event.to_dict()
        self.assertEqual(wire["type"], "request_completed")
        self.assertEqual(wire["channel"], "experiment")
        self.assertEqual(wire["run_id"], "run_01")
        self.assertEqual(wire["completed"], 1)
        self.assertEqual(event.model_id, "models/example.gguf")
        self.assertEqual(event.evidence, {})

    def test_event_envelope_keeps_typed_wire_fields_authoritative(self) -> None:
        event = ClusterEvent(
            channel=EventChannel.NODE_OPS,
            type="action_log",
            at="2026-08-20T00:00:00+00:00",
            payload={"type": "incorrect", "at": "incorrect", "channel": "experiment"},
        )
        wire = event.to_dict()
        self.assertEqual(wire["type"], "action_log")
        self.assertEqual(wire["at"], "2026-08-20T00:00:00+00:00")
        self.assertEqual(wire["channel"], "node_ops")

    def test_run_event_journal_marks_experiment_channel(self) -> None:
        with TemporaryDirectory() as temporary:
            persistence = RunPersistence(
                Path(temporary),
                "run_01",
                ExperimentConfig(node_names=["worker-01"]),
            )
            event = persistence.emit("request_started", node="worker-01")
        self.assertEqual(event["channel"], "experiment")

    def test_event_bus_routes_and_bounds_subscriber_buffer(self) -> None:
        from cluster.dashboard.app import EventBus

        bus = EventBus(subscriber_maxsize=2)
        stream = bus.stream()
        self.assertEqual(json.loads(next(stream)[6:])["channel"], "system")
        bus.publish("action_log", channel=EventChannel.NODE_OPS, line="sync-code")
        node_event = json.loads(next(stream)[6:])
        self.assertEqual(node_event["channel"], "node_ops")
        self.assertEqual(node_event["line"], "sync-code")
        bus.publish("experiment_event", channel=EventChannel.EXPERIMENT, event={"type": "request_completed"})
        experiment_event = json.loads(next(stream)[6:])
        self.assertEqual(experiment_event["channel"], "experiment")
        subscriber = bus._subscribers[0]
        for index in range(4):
            bus.publish("action_log", channel=EventChannel.NODE_OPS, line=str(index))
        self.assertLessEqual(subscriber.qsize(), 2)
        stream.close()

    def test_dashboard_producers_use_explicit_channels(self) -> None:
        source_path = Path(__file__).resolve().parents[1] / "dashboard" / "services.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        calls = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "events" and node.func.attr == "publish"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            calls.append((node.args[0].value, {keyword.arg: ast.unparse(keyword.value) for keyword in node.keywords if keyword.arg}))
        routed = {name: keywords.get("channel") for name, keywords in calls}
        self.assertEqual(routed["action_log"], "EventChannel.NODE_OPS")
        self.assertEqual(routed["environment_changed"], "EventChannel.NODE_OPS")
        self.assertEqual(routed["experiment_event"], "EventChannel.EXPERIMENT")
        self.assertEqual(routed["cluster_status"], "EventChannel.SYSTEM")

    def test_frontend_routes_node_logs_away_from_experiment_console(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function eventChannel(message)", source)
        self.assertIn("environmentLogLine(\"TASK\", message.line || message.message", source)
        self.assertIn('message.type === "experiment_event" && channel === "experiment"', source)


if __name__ == "__main__":
    unittest.main()
