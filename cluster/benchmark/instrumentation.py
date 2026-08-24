"""Additive research measurement records and run-level summaries.

The legacy 19-column request CSV is deliberately outside this module.  All
high-resolution measurements are written as versioned JSONL records so a
missing sensor remains distinguishable from a measured zero.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable, Dict, Mapping, Optional, Sequence


MEASUREMENT_SCHEMA_VERSION = 1
STEADY_STATE_POLICY = "phase09-pilot-window-v1"
STEADY_STATE_WINDOW_SAMPLES = 3
STEADY_STATE_TEMPERATURE_SPAN_C = 1.5
TelemetryProbe = Callable[[Any], Mapping[str, Any]]
MeasurementWriter = Callable[[Mapping[str, Any]], None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _available(value: Any, *, source: str, reason: str = "") -> Dict[str, Any]:
    return {
        "available": value is not None,
        "source": source,
        "reason": "" if value is not None else (reason or "unavailable"),
    }


def request_measurement(run_id: str, result: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize one request result into the additive measurement contract."""
    generated = int(result.get("generated_tokens") or 0)
    input_tokens = result.get("input_tokens")
    total_tokens = result.get("total_tokens")
    source = str(result.get("input_token_source") or "worker_not_reported")
    prefill = result.get("prefill_time_s")
    decode = result.get("decode_time_s")
    bytes_sent = result.get("bytes_sent")
    bytes_received = result.get("bytes_received")
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "record_type": "request_metrics",
        "sampled_at": _utc_now(),
        "run_id": run_id,
        "scenario_id": result.get("scenario_id"),
        "node": result.get("node"),
        "request_id": result.get("request_id"),
        "logical_request_id": result.get("logical_request_id"),
        "ok": bool(result.get("ok")),
        "monotonic_started_s": result.get("monotonic_started_s"),
        "monotonic_finished_s": result.get("monotonic_finished_s"),
        "input_tokens": input_tokens,
        "input_token_source": source,
        "input_tokens_exact": bool(result.get("input_tokens_exact", False)),
        "prefill_time_s": prefill,
        "prefill_tokens_per_s": result.get("prefill_tokens_per_s"),
        "decode_time_s": decode,
        "decode_tokens_per_s": result.get("decode_tokens_per_s"),
        "total_tokens": total_tokens,
        "generated_tokens": generated,
        "connection_setup_s": result.get("connection_setup_s"),
        "rtt_s": result.get("rtt_s"),
        "bytes_sent": bytes_sent,
        "bytes_received": bytes_received,
        "effective_bandwidth_bytes_s": result.get("effective_bandwidth_bytes_s"),
        "coordinator_wait_s": result.get("coordinator_wait_s"),
        "availability": {
            "input_tokens": _available(input_tokens, source=source),
            "prefill_time_s": _available(
                prefill,
                source=str(result.get("prefill_time_source") or "worker_not_reported"),
            ),
            "decode_time_s": _available(
                decode,
                source=str(result.get("decode_time_source") or "worker_not_reported"),
            ),
            "rtt_s": _available(
                result.get("rtt_s"), source="not_measured", reason="dedicated_rtt_probe_not_enabled"
            ),
            "network_bytes": _available(
                bytes_sent if bytes_sent is not None and bytes_received is not None else None,
                source="http_body_bytes",
            ),
            "coordinator_wait_s": _available(
                result.get("coordinator_wait_s"),
                source="not_exposed_by_runtime",
                reason="coordinator_internal_wait_unavailable",
            ),
        },
    }


def rpc_lifecycle_measurement(run_id: str, topology: Mapping[str, Any]) -> Dict[str, Any]:
    """Return one durable RPC lifecycle record after cleanup was attempted."""
    model_load = topology.get("model_load_distribution_s") or topology.get("model_load_s")
    cleanup_s = topology.get("cleanup_s")
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "record_type": "rpc_lifecycle",
        "sampled_at": _utc_now(),
        "run_id": run_id,
        "scenario_id": "rpc-sharded",
        "node": topology.get("coordinator"),
        "model_load_distribution_s": model_load,
        "cleanup_s": cleanup_s,
        "cleanup_status": topology.get("cleanup_status"),
        "cleanup_attempts": topology.get("cleanup_attempts", []),
        "coordinator_wait_s": None,
        "availability": {
            "model_load_distribution_s": _available(
                model_load, source="rpc_runtime_start"
            ),
            "cleanup_s": _available(cleanup_s, source="rpc_session_cleanup"),
            "coordinator_wait_s": _available(
                None,
                source="not_exposed_by_runtime",
                reason="coordinator_internal_wait_unavailable",
            ),
        },
    }


def normalize_telemetry_sample(
    *,
    run_id: str,
    scenario_id: str,
    node: Any,
    raw: Mapping[str, Any],
    run_started_monotonic: float,
    probe_started: float,
    probe_finished: float,
    sample_kind: str = "measurement",
) -> Dict[str, Any]:
    metrics = raw.get("metrics") if isinstance(raw.get("metrics"), Mapping) else raw
    metrics = metrics if isinstance(metrics, Mapping) else {}
    power = metrics.get("power") if isinstance(metrics.get("power"), Mapping) else {}
    cpu = metrics.get("cpu") if isinstance(metrics.get("cpu"), Mapping) else {}
    network = metrics.get("network") if isinstance(metrics.get("network"), Mapping) else {}
    temperatures = (
        dict(metrics.get("temperatures_c"))
        if isinstance(metrics.get("temperatures_c"), Mapping)
        else {}
    )
    power_integrity = (
        dict(raw.get("power_integrity"))
        if isinstance(raw.get("power_integrity"), Mapping)
        else None
    )
    power_w = _number(metrics.get("power_w"))
    if power_w is None:
        power_w = _number(power.get("total_w"))
    frequency = _number(cpu.get("frequency_mhz"))
    current_faults = (
        power_integrity.get("current_fault_bits")
        if isinstance(power_integrity, Mapping)
        else None
    )
    current_state = (
        power_integrity.get("current")
        if isinstance(power_integrity, Mapping) else None
    )
    if isinstance(current_faults, list):
        throttling_supported = True
        throttled = bool(current_faults)
    elif isinstance(current_state, Mapping):
        throttling_supported = True
        throttled = any(value is True for value in current_state.values())
    else:
        throttling_supported = False
        throttled = None
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "record_type": "telemetry_sample",
        "sample_kind": sample_kind,
        "sampled_at": str(metrics.get("sampled_at") or _utc_now()),
        "run_id": run_id,
        "scenario_id": scenario_id,
        "node": str(getattr(node, "name", "") or ""),
        # Use the probe midpoint as the best monotonic estimate of the remote
        # snapshot instant; retain full collection overhead separately.
        "monotonic_elapsed_s": round(
            ((probe_started + probe_finished) / 2.0) - run_started_monotonic, 9
        ),
        "collection_overhead_s": round(probe_finished - probe_started, 9),
        "worker_collection_overhead_s": _number(
            metrics.get("telemetry_collection_overhead_s")
        ),
        "power_w": power_w,
        "temperatures_c": temperatures,
        "cpu_frequency_mhz": frequency,
        "network_bytes_sent": network.get("bytes_sent"),
        "network_bytes_received": network.get("bytes_received"),
        "throttling_supported": throttling_supported,
        "throttled": throttled,
        "power_integrity": power_integrity,
        "availability": {
            "power_w": _available(
                power_w,
                source="worker_telemetry",
                reason="power_sensor_unavailable",
            ),
            "temperature": _available(
                max(
                    (
                        number for number in (_number(value) for value in temperatures.values())
                        if number is not None
                    ),
                    default=None,
                ),
                source="worker_telemetry",
                reason="temperature_sensor_unavailable",
            ),
            "frequency": _available(
                frequency,
                source="psutil_cpu_freq",
                reason="frequency_sensor_unavailable",
            ),
            "throttling": _available(
                throttled,
                source="raspberry_pi_get_throttled",
                reason="platform_does_not_expose_throttling_state",
            ),
        },
    }


def _temperature(sample: Mapping[str, Any]) -> Optional[float]:
    values = sample.get("temperatures_c")
    if not isinstance(values, Mapping):
        return None
    valid = [_number(value) for value in values.values()]
    return max((value for value in valid if value is not None), default=None)


def _energy(samples: Sequence[Mapping[str, Any]]) -> Optional[float]:
    valid = [
        (_number(item.get("monotonic_elapsed_s")), _number(item.get("power_w")))
        for item in samples
    ]
    points = sorted((at, watts) for at, watts in valid if at is not None and watts is not None)
    if len(points) < 2:
        return None
    joules = 0.0
    for (left_t, left_w), (right_t, right_w) in zip(points, points[1:]):
        joules += max(right_t - left_t, 0.0) * (left_w + right_w) / 2.0
    return round(joules, 9)


def _scenario_energy(samples: Sequence[Mapping[str, Any]]) -> Optional[float]:
    by_scenario: Dict[str, list[Mapping[str, Any]]] = {}
    for item in samples:
        by_scenario.setdefault(str(item.get("scenario_id") or "unknown"), []).append(item)
    energies = [_energy(values) for values in by_scenario.values()]
    valid = [value for value in energies if value is not None]
    if not energies or len(valid) != len(energies):
        return None
    return round(sum(valid), 9)


def _scenario_duration(samples: Sequence[Mapping[str, Any]]) -> Optional[float]:
    by_scenario: Dict[str, list[Mapping[str, Any]]] = {}
    for item in samples:
        by_scenario.setdefault(str(item.get("scenario_id") or "unknown"), []).append(item)
    durations: list[float] = []
    for values in by_scenario.values():
        timestamps = [
            value for value in (_number(item.get("monotonic_elapsed_s")) for item in values)
            if value is not None
        ]
        if len(timestamps) < 2:
            return None
        durations.append(max(timestamps) - min(timestamps))
    return sum(durations) if durations else None


def _steady_state_start(samples: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Return the first predeclared stable thermal window endpoint.

    This is descriptive instrumentation only.  Formal admission still depends
    on the separately frozen Phase 09 cooldown and thermal gate.
    """
    ordered = sorted(
        (
            (_number(item.get("monotonic_elapsed_s")), _temperature(item), item.get("throttled"))
            for item in samples
        ),
        key=lambda item: float(item[0]) if item[0] is not None else float("inf"),
    )
    valid = [item for item in ordered if item[0] is not None and item[1] is not None]
    for index in range(STEADY_STATE_WINDOW_SAMPLES - 1, len(valid)):
        window = valid[index - STEADY_STATE_WINDOW_SAMPLES + 1:index + 1]
        temperatures = [float(item[1]) for item in window]
        if (
            max(temperatures) - min(temperatures) <= STEADY_STATE_TEMPERATURE_SPAN_C
            and all(item[2] is not True for item in window)
        ):
            return round(float(window[-1][0]), 9)
    return None


def summarize_measurements(
    samples: Sequence[Mapping[str, Any]],
    request_records: Sequence[Mapping[str, Any]],
    *,
    rpc_topology: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    nodes = sorted(
        {str(item.get("node")) for item in samples if item.get("node")}
        | {str(item.get("node")) for item in request_records if item.get("node")}
    )
    per_node: Dict[str, Any] = {}
    for node in nodes:
        node_samples = [item for item in samples if item.get("node") == node]
        measured = [item for item in node_samples if item.get("sample_kind") == "measurement"]
        idle = [item for item in node_samples if item.get("sample_kind") == "idle"]
        node_requests = [item for item in request_records if item.get("node") == node]
        powers = [_number(item.get("power_w")) for item in measured]
        powers = [value for value in powers if value is not None]
        idle_powers = [_number(item.get("power_w")) for item in idle]
        idle_powers = [value for value in idle_powers if value is not None]
        temperatures = [_temperature(item) for item in measured]
        temperatures = [value for value in temperatures if value is not None]
        steady_state_start = _steady_state_start(measured)
        # Never bridge unsampled cooldown time between sequential scenarios.
        energy_j = _scenario_energy(measured)
        power_duration_s = _scenario_duration(measured)
        generated = sum(int(item.get("generated_tokens") or 0) for item in node_requests if item.get("ok"))
        successful = sum(1 for item in node_requests if item.get("ok"))
        throttle_values = [item.get("throttled") for item in measured if item.get("throttling_supported")]
        frequency_samples = [
            {
                "monotonic_elapsed_s": item.get("monotonic_elapsed_s"),
                "frequency_mhz": item.get("cpu_frequency_mhz"),
            }
            for item in measured
            if item.get("cpu_frequency_mhz") is not None
        ]
        network_segments: list[tuple[int, int, float]] = []
        scenarios = sorted({str(item.get("scenario_id") or "unknown") for item in measured})
        for scenario in scenarios:
            segment = [
                item for item in measured
                if str(item.get("scenario_id") or "unknown") == scenario
            ]
            sent_values = [
                _number(item.get("network_bytes_sent")) for item in segment
                if _number(item.get("network_bytes_sent")) is not None
            ]
            received_values = [
                _number(item.get("network_bytes_received")) for item in segment
                if _number(item.get("network_bytes_received")) is not None
            ]
            if len(segment) < 2 or len(sent_values) < 2 or len(received_values) < 2:
                continue
            duration = max(
                (_number(segment[-1].get("monotonic_elapsed_s")) or 0)
                - (_number(segment[0].get("monotonic_elapsed_s")) or 0),
                0,
            )
            network_segments.append(
                (
                    int(max(sent_values) - min(sent_values)),
                    int(max(received_values) - min(received_values)),
                    duration,
                )
            )
        complete_network = bool(scenarios) and len(network_segments) == len(scenarios)
        bytes_sent = sum(item[0] for item in network_segments) if complete_network else None
        bytes_received = sum(item[1] for item in network_segments) if complete_network else None
        network_duration = sum(item[2] for item in network_segments) if network_segments else None
        bandwidth = (
            (bytes_sent + bytes_received) / network_duration
            if bytes_sent is not None and bytes_received is not None and network_duration
            else None
        )
        per_node[node] = {
            "sample_count": len(measured),
            "idle_power_w": round(mean(idle_powers), 6) if idle_powers else None,
            "average_power_w": (
                round(energy_j / power_duration_s, 6)
                if energy_j is not None and power_duration_s and power_duration_s > 0
                else None
            ),
            "peak_power_w": round(max(powers), 6) if powers else None,
            "energy_j": energy_j,
            "generated_tokens_per_j": round(generated / energy_j, 9) if energy_j and energy_j > 0 else None,
            "requests_per_j": round(successful / energy_j, 9) if energy_j and energy_j > 0 else None,
            "start_temperature_c": temperatures[0] if temperatures else None,
            "mean_temperature_c": round(mean(temperatures), 6) if temperatures else None,
            "peak_temperature_c": max(temperatures) if temperatures else None,
            "end_temperature_c": temperatures[-1] if temperatures else None,
            "throttling_sample_count": sum(bool(value) for value in throttle_values) if throttle_values else None,
            "frequency_samples": frequency_samples,
            "steady_state_start": steady_state_start,
            "steady_state_policy": {
                "id": STEADY_STATE_POLICY,
                "window_samples": STEADY_STATE_WINDOW_SAMPLES,
                "maximum_temperature_span_c": STEADY_STATE_TEMPERATURE_SPAN_C,
                "active_throttling_allowed": False,
            },
            "bytes_sent": bytes_sent,
            "bytes_received": bytes_received,
            "effective_bandwidth_bytes_s": round(bandwidth, 6) if bandwidth is not None else None,
            "controller_collection_overhead_s": round(
                sum(float(item.get("collection_overhead_s") or 0.0) for item in node_samples), 9
            ),
            "worker_collection_overhead_samples_s": [
                {
                    "sampled_at": sampled_at,
                    "overhead_s": overhead,
                }
                for sampled_at, overhead in dict.fromkeys(
                    (
                        str(item.get("sampled_at") or ""),
                        _number(item.get("worker_collection_overhead_s")),
                    )
                    for item in node_samples
                    if _number(item.get("worker_collection_overhead_s")) is not None
                )
            ],
            "availability": {
                "energy_j": _available(
                    energy_j,
                    source="trapezoidal_power_integration",
                    reason="fewer_than_two_power_samples",
                ),
                "thermal": _available(
                    temperatures[0] if temperatures else None,
                    source="worker_telemetry",
                    reason="temperature_sensor_unavailable",
                ),
                "steady_state_start": _available(
                    steady_state_start,
                    source=STEADY_STATE_POLICY,
                    reason="stable_temperature_window_not_observed",
                ),
                "network_counters": _available(
                    bytes_sent,
                    source="host_interface_counters",
                    reason="fewer_than_two_network_samples",
                ),
            },
        }

    energies = [value["energy_j"] for value in per_node.values() if value["energy_j"] is not None]
    all_energy_available = bool(per_node) and len(energies) == len(per_node)
    overall_energy = round(sum(energies), 9) if all_energy_available else None
    total_generated = sum(int(item.get("generated_tokens") or 0) for item in request_records if item.get("ok"))
    successful_requests = sum(1 for item in request_records if item.get("ok"))
    idle_values = [value["idle_power_w"] for value in per_node.values() if value["idle_power_w"] is not None]
    average_values = [value["average_power_w"] for value in per_node.values() if value["average_power_w"] is not None]
    peak_values = [value["peak_power_w"] for value in per_node.values() if value["peak_power_w"] is not None]
    all_idle_available = bool(per_node) and len(idle_values) == len(per_node)
    all_average_available = bool(per_node) and len(average_values) == len(per_node)
    all_peak_available = bool(per_node) and len(peak_values) == len(per_node)
    topology = dict(rpc_topology or {})
    rpc = {
        "model_load_distribution_s": (
            topology.get("model_load_distribution_s") or topology.get("model_load_s")
        ),
        "cleanup_s": topology.get("cleanup_s"),
        "coordinator_wait_s": None,
        "availability": {
            "model_load_distribution_s": _available(
                topology.get("model_load_distribution_s") or topology.get("model_load_s"),
                source="rpc_coordinator_start",
            ),
            "cleanup_s": _available(topology.get("cleanup_s"), source="rpc_session_cleanup"),
            "coordinator_wait_s": _available(
                None,
                source="not_exposed_by_runtime",
                reason="coordinator_internal_wait_unavailable",
            ),
        },
    }
    return {
        "schema_version": MEASUREMENT_SCHEMA_VERSION,
        "artifact": "measurements.jsonl",
        "clock": "time.perf_counter_monotonic_process_local",
        "nodes": per_node,
        "overall": {
            "idle_power_w": round(sum(idle_values), 6) if all_idle_available else None,
            "average_power_w": round(sum(average_values), 6) if all_average_available else None,
            "peak_power_w": round(sum(peak_values), 6) if all_peak_available else None,
            "energy_j": overall_energy,
            "generated_tokens_per_j": (
                round(total_generated / overall_energy, 9)
                if overall_energy and overall_energy > 0 else None
            ),
            "requests_per_j": (
                round(successful_requests / overall_energy, 9)
                if overall_energy and overall_energy > 0 else None
            ),
            "availability": {
                "energy_j": _available(
                    overall_energy,
                    source="sum_of_node_energy",
                    reason="one_or_more_nodes_unavailable",
                )
            },
        },
        "rpc": rpc,
    }


class RunInstrumentation:
    """Bounded background sampler tied to run/scenario/node identity."""

    def __init__(
        self,
        run_id: str,
        writer: MeasurementWriter,
        probe: Optional[TelemetryProbe],
        *,
        interval_s: float = 1.0,
    ) -> None:
        self.run_id = run_id
        self.writer = writer
        self.probe = probe
        self.interval_s = max(float(interval_s), 0.05)
        self.run_started_monotonic: Optional[float] = None
        self.samples: list[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active_context: Optional[tuple[str, tuple[Any, ...]]] = None

    def _sample(self, scenario_id: str, nodes: Sequence[Any], kind: str) -> None:
        if self.probe is None:
            return
        if self.run_started_monotonic is None:
            self.run_started_monotonic = time.perf_counter()

        def collect(node: Any) -> tuple[Any, float, float, Mapping[str, Any]]:
            started = time.perf_counter()
            try:
                raw = self.probe(node)
            except Exception as exc:
                raw = {"probe_error": f"{type(exc).__name__}: {exc}"}
            finished = time.perf_counter()
            return node, started, finished, raw

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, min(8, len(nodes)))
        ) as pool:
            futures = [pool.submit(collect, node) for node in nodes]
            results = [future.result() for future in futures]
        for node, started, finished, raw in results:
            sample = normalize_telemetry_sample(
                run_id=self.run_id,
                scenario_id=scenario_id,
                node=node,
                raw=raw,
                run_started_monotonic=self.run_started_monotonic,
                probe_started=started,
                probe_finished=finished,
                sample_kind=kind,
            )
            if raw.get("probe_error"):
                sample["probe_error"] = raw["probe_error"]
            self.samples.append(sample)
            self.writer(sample)

    def capture_idle(self, nodes: Sequence[Any]) -> None:
        self._sample("idle", nodes, "idle")

    def start_scenario(self, scenario_id: str, nodes: Sequence[Any]) -> None:
        if self.probe is None:
            return
        self.stop_scenario()
        if self._thread is not None:
            return
        self._stop.clear()
        self._active_context = (scenario_id, tuple(nodes))

        def loop() -> None:
            while not self._stop.is_set():
                self._sample(scenario_id, nodes, "measurement")
                if self._stop.wait(self.interval_s):
                    break

        self._thread = threading.Thread(
            target=loop,
            name=f"telemetry-{self.run_id}-{scenario_id}",
            daemon=True,
        )
        self._thread.start()

    def stop_scenario(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stop.set()
        thread.join(timeout=max(self.interval_s * 2, 6.0))
        if thread.is_alive():
            # Keep the stop flag and thread reference: never clear it and start
            # a second sampler over a stuck bounded probe.
            return
        self._thread = None
        context = self._active_context
        self._active_context = None
        if context is not None:
            scenario_id, nodes = context
            self._sample(scenario_id, nodes, "measurement")


__all__ = [
    "MEASUREMENT_SCHEMA_VERSION",
    "RunInstrumentation",
    "normalize_telemetry_sample",
    "request_measurement",
    "rpc_lifecycle_measurement",
    "summarize_measurements",
]
