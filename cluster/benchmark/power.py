"""Run-local Raspberry Pi power observation and measurement-quality policy."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from cluster.domain.power import (
    MeasurementQuality,
    PowerIntegrityStatus,
    PowerWarningCode,
    RaspberryPiPowerIntegrity,
    WarningRecord,
    power_semantic_signature,
    power_warning_records,
)


EventEmitter = Callable[..., Mapping[str, Any]]

_QUALITY_SEVERITY = {
    MeasurementQuality.CLEAN: 0,
    MeasurementQuality.UNKNOWN: 1,
    MeasurementQuality.WARNING: 2,
    MeasurementQuality.DEGRADED: 3,
}
_STATUS_SEVERITY = {
    PowerIntegrityStatus.OK: 0,
    PowerIntegrityStatus.UNAVAILABLE: 1,
    PowerIntegrityStatus.HISTORY_WARNING: 2,
    PowerIntegrityStatus.ACTIVE_DEGRADED: 3,
}
_ACTIVE_CODES = (
    PowerWarningCode.PI_UNDERVOLTAGE_ACTIVE,
    PowerWarningCode.PI_FREQUENCY_CAPPED_ACTIVE,
    PowerWarningCode.PI_THROTTLING_ACTIVE,
    PowerWarningCode.PI_SOFT_TEMP_LIMIT_ACTIVE,
)
_HISTORY_CODES = (
    PowerWarningCode.PI_UNDERVOLTAGE_HISTORY,
    PowerWarningCode.PI_FREQUENCY_CAPPED_HISTORY,
    PowerWarningCode.PI_THROTTLING_HISTORY,
    PowerWarningCode.PI_SOFT_TEMP_LIMIT_HISTORY,
)
_NEW_HISTORY_CODES = (
    PowerWarningCode.PI_UNDERVOLTAGE_HISTORY_APPEARED_DURING_RUN,
    PowerWarningCode.PI_FREQUENCY_CAPPED_HISTORY_APPEARED_DURING_RUN,
    PowerWarningCode.PI_THROTTLING_HISTORY_APPEARED_DURING_RUN,
    PowerWarningCode.PI_SOFT_TEMP_LIMIT_HISTORY_APPEARED_DURING_RUN,
)


def _append_unique(values: List[str], code: PowerWarningCode | str) -> None:
    value = code.value if isinstance(code, PowerWarningCode) else str(code)
    if value not in values:
        values.append(value)


def _history_values(snapshot: RaspberryPiPowerIntegrity) -> Tuple[bool, ...]:
    return tuple(snapshot.history.to_dict().values())


class RunPowerIntegrityTracker:
    """Collect bounded snapshots without coupling power quality to run status."""

    def __init__(self, emit: Optional[EventEmitter] = None) -> None:
        self._emit = emit
        self._lock = threading.Lock()
        self._observations: "OrderedDict[str, Dict[str, List[RaspberryPiPowerIntegrity]]]" = (
            OrderedDict()
        )
        self._last: Dict[str, RaspberryPiPowerIntegrity] = {}
        self._warnings: "OrderedDict[Tuple[str, str], WarningRecord]" = OrderedDict()

    @property
    def has_observations(self) -> bool:
        with self._lock:
            return bool(self._observations)

    def _record(
        self, node: str, stage: str, snapshot: RaspberryPiPowerIntegrity
    ) -> None:
        transition: Optional[Tuple[RaspberryPiPowerIntegrity, RaspberryPiPowerIntegrity]] = None
        with self._lock:
            by_stage = self._observations.setdefault(
                node,
                {
                    "preflight": [],
                    "pre_measurement": [],
                    "measurement": [],
                    "postflight": [],
                },
            )
            by_stage[stage].append(snapshot)
            previous = self._last.get(node)
            if previous is not None and power_semantic_signature(previous) != power_semantic_signature(snapshot):
                transition = (previous, snapshot)
            self._last[node] = snapshot
            for warning in power_warning_records(node, snapshot, stage=stage):
                self._warnings.setdefault((node, warning.code.value), warning)

        if self._emit and stage in {"preflight", "pre_measurement", "postflight"}:
            self._emit(
                "power_integrity_snapshot",
                node=node,
                stage=stage,
                power_integrity=snapshot.to_dict(),
            )
        if self._emit and transition is not None:
            previous, current = transition
            self._emit(
                "power_integrity_changed",
                node=node,
                message=self._transition_message(previous, current),
                evidence={
                    "previous_status": previous.status.value,
                    "status": current.status.value,
                    "previous_raw_hex": previous.raw_hex,
                    "raw_hex": current.raw_hex,
                    "blocking": False,
                    "stage": stage,
                },
            )

    @staticmethod
    def _transition_message(
        previous: RaspberryPiPowerIntegrity, current: RaspberryPiPowerIntegrity
    ) -> str:
        if not current.available:
            return "Raspberry Pi power status became unavailable."
        if not previous.available:
            return f"Raspberry Pi power status became available ({current.status.value})."
        if current.status is PowerIntegrityStatus.ACTIVE_DEGRADED:
            return "Active Raspberry Pi power or thermal condition detected."
        if previous.status is PowerIntegrityStatus.ACTIVE_DEGRADED:
            return f"Active Raspberry Pi power condition cleared ({current.status.value})."
        return f"Raspberry Pi power integrity changed to {current.status.value}."

    def record_preflight(self, node: str, snapshot: RaspberryPiPowerIntegrity) -> None:
        self._record(node, "preflight", snapshot)

    def record_pre_measurement(
        self, node: str, snapshot: RaspberryPiPowerIntegrity
    ) -> None:
        self._record(node, "pre_measurement", snapshot)

    def record_measurement_sample(
        self, node: str, snapshot: RaspberryPiPowerIntegrity
    ) -> None:
        self._record(node, "measurement", snapshot)

    def record_postflight(self, node: str, snapshot: RaspberryPiPowerIntegrity) -> None:
        self._record(node, "postflight", snapshot)

    def warning_records(self) -> List[Dict[str, object]]:
        with self._lock:
            return [warning.to_dict() for warning in self._warnings.values()]

    def human_warning_messages(self) -> List[str]:
        with self._lock:
            return [
                f"{warning.node}: {warning.message}"
                for warning in self._warnings.values()
            ]

    def summarize(self) -> Dict[str, Any]:
        with self._lock:
            observations = OrderedDict(
                (
                    node,
                    {stage: list(values) for stage, values in by_stage.items()},
                )
                for node, by_stage in self._observations.items()
            )
            warnings = [warning.to_dict() for warning in self._warnings.values()]

        node_summaries: Dict[str, Any] = OrderedDict()
        overall_reasons: List[str] = []
        qualities: List[MeasurementQuality] = []
        for node, by_stage in observations.items():
            node_summary = self._summarize_node(by_stage)
            node_summaries[node] = node_summary
            qualities.append(MeasurementQuality(node_summary["quality"]))
            for reason in node_summary["reason_codes"]:
                if reason not in overall_reasons:
                    overall_reasons.append(reason)

        overall = (
            max(qualities, key=lambda value: _QUALITY_SEVERITY[value])
            if qualities
            else MeasurementQuality.UNKNOWN
        )
        return {
            "overall": {
                "quality": overall.value,
                "reason_codes": overall_reasons,
            },
            "nodes": node_summaries,
            "warnings": warnings,
        }

    @staticmethod
    def _summarize_node(
        by_stage: Mapping[str, Sequence[RaspberryPiPowerIntegrity]]
    ) -> Dict[str, Any]:
        preflight = list(by_stage.get("preflight") or [])
        pre_measurement = list(by_stage.get("pre_measurement") or [])
        measurement = list(by_stage.get("measurement") or [])
        postflight = list(by_stage.get("postflight") or [])
        all_values = preflight + pre_measurement + measurement + postflight
        valid = [item for item in all_values if item.available]
        unavailable_count = len(all_values) - len(valid)
        # Postflight is sampled immediately after scheduling stops. Include an
        # active condition there so short runs without an interval sample do
        # not falsely appear clean.
        measurement_window = [
            item
            for item in (pre_measurement + measurement + postflight)
            if item.available
        ]
        reasons: List[str] = []

        active_observed = False
        for item in measurement_window:
            for code in _ACTIVE_CODES:
                if code in item.reason_codes:
                    active_observed = True
                    _append_unique(reasons, code)

        pre = next((item for item in preflight if item.available), None)
        post = next((item for item in reversed(postflight) if item.available), None)
        new_history = False
        if pre is not None and post is not None:
            for before, after, code in zip(
                _history_values(pre), _history_values(post), _NEW_HISTORY_CODES
            ):
                if not before and after:
                    new_history = True
                    _append_unique(reasons, code)

        history_observed = False
        for item in valid:
            for code in _HISTORY_CODES:
                if code in item.reason_codes:
                    history_observed = True
                    _append_unique(reasons, code)
            if PowerWarningCode.PI_POWER_UNKNOWN_BITS in item.reason_codes:
                history_observed = True
                _append_unique(reasons, PowerWarningCode.PI_POWER_UNKNOWN_BITS)

        if active_observed or new_history:
            quality = MeasurementQuality.DEGRADED
        elif not valid:
            quality = MeasurementQuality.UNKNOWN
            _append_unique(reasons, PowerWarningCode.PI_POWER_STATUS_UNAVAILABLE)
        elif history_observed:
            quality = MeasurementQuality.WARNING
        elif unavailable_count:
            quality = MeasurementQuality.WARNING
            _append_unique(reasons, PowerWarningCode.PI_POWER_OBSERVATION_INCOMPLETE)
        else:
            quality = MeasurementQuality.CLEAN

        valid_measurement = [item for item in measurement if item.available]
        worst_status = (
            max(valid_measurement, key=lambda item: _STATUS_SEVERITY[item.status]).status.value
            if valid_measurement
            else None
        )
        return {
            "quality": quality.value,
            "preflight": preflight[-1].to_dict() if preflight else None,
            "pre_measurement": pre_measurement[-1].to_dict() if pre_measurement else None,
            "measurement": {
                "sample_count": len(measurement),
                "valid_sample_count": len(valid_measurement),
                "unavailable_sample_count": len(measurement) - len(valid_measurement),
                "active_warning_samples": sum(
                    1
                    for item in valid_measurement
                    if item.status is PowerIntegrityStatus.ACTIVE_DEGRADED
                ),
                "worst_status": worst_status,
            },
            "postflight": postflight[-1].to_dict() if postflight else None,
            "reason_codes": reasons,
        }


def suite_measurement_quality(summaries: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregate model quality without influencing suite lifecycle status."""
    counts = {quality.value: 0 for quality in MeasurementQuality}
    qualities: List[MeasurementQuality] = []
    for summary in summaries:
        raw = summary.get("measurement_quality")
        try:
            quality = MeasurementQuality(raw)
        except (TypeError, ValueError):
            continue
        counts[quality.value] += 1
        qualities.append(quality)
    if not qualities:
        return {}
    worst = max(qualities, key=lambda value: _QUALITY_SEVERITY[value])
    return {
        "measurement_quality": worst.value,
        "measurement_quality_counts": counts,
    }


__all__ = ["RunPowerIntegrityTracker", "suite_measurement_quality"]
