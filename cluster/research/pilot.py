"""Pure Phase 09 pilot planning and run-level precision analysis.

Pilot observations are deliberately separate from formal campaign cells.  The
functions in this module perform no filesystem, network, or process I/O; the
CLI integration owns those boundaries.
"""

from __future__ import annotations

import hashlib
import math
from statistics import mean, median, stdev
from typing import Any, Mapping, Sequence

from .matrix import expand_formal_matrix


Z_95 = 1.959963984540054
PRIMARY_METRICS = (
    "cluster_tokens_per_s",
    "requests_per_s",
    "ttft_p50_s",
    "e2e_p50_s",
)


class PilotValidationError(ValueError):
    """The predeclared plan or collected pilot evidence is inconsistent."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotValidationError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise PilotValidationError(f"{label} must be a non-empty list")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PilotValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _integer(value: Any, label: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PilotValidationError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: Any, label: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotValidationError(f"{label} must be a finite number >= {minimum}")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise PilotValidationError(f"{label} must be a finite number >= {minimum}")
    return result


def _cell_key(cell: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(cell.get("template_id") or ""),
        str(cell.get("node_set_id") or ""),
        str(cell.get("model_lock_key") or ""),
        str(cell.get("prompt_id") or ""),
    )


def validate_pilot_plan(
    plan: Mapping[str, Any],
    *,
    matrix: Mapping[str, Any],
    model_lock: Mapping[str, Any],
    prompt_lock: Mapping[str, Any],
) -> None:
    """Validate that every pilot cell is a separated subset of the matrix."""
    if plan.get("schema_version") != 1:
        raise PilotValidationError("pilot schema_version must be 1")
    pilot_version = _integer(plan.get("pilot_version"), "pilot_version")
    if plan.get("experiment_type") != "pilot":
        raise PilotValidationError("pilot experiment_type must be pilot")
    pilot_id = _text(plan.get("pilot_id"), "pilot_id")
    if len(pilot_id) > 96 or "/" in pilot_id or "\\" in pilot_id:
        raise PilotValidationError("pilot_id is unsafe")
    if pilot_version > 1:
        supersedes = _mapping(plan.get("supersedes"), "supersedes")
        previous_id = _text(supersedes.get("pilot_id"), "supersedes.pilot_id")
        reason_code = _text(supersedes.get("reason_code"), "supersedes.reason_code")
        if previous_id == pilot_id:
            raise PilotValidationError("a revised pilot cannot supersede itself")
        if reason_code not in {
            "DEPLOYMENT_SOURCE_DRIFT",
            "WORKER_LLAMA_CONTEXT_RACE",
            "TELEMETRY_INTRUSION",
        }:
            raise PilotValidationError("revised pilot reason_code is not an approved pilot remediation")
        _text(supersedes.get("failed_run_id"), "supersedes.failed_run_id")
        _text(supersedes.get("preregistered_parent_commit"), "supersedes.preregistered_parent_commit")
    separation = _mapping(plan.get("separation"), "separation")
    if separation.get("formal_pooling_allowed") is not False:
        raise PilotValidationError("pilot observations may not enter formal estimates")
    if separation.get("selective_deletion_allowed") is not False:
        raise PilotValidationError("pilot evidence may not be selectively deleted")
    if plan.get("matrix_id") != matrix.get("matrix_id"):
        raise PilotValidationError("pilot matrix_id does not match the formal matrix")
    lock_ref = _mapping(plan.get("lock_ref"), "lock_ref")
    for lock in (model_lock, prompt_lock):
        if (
            lock.get("lock_id") != lock_ref.get("lock_id")
            or lock.get("lock_sha256") != lock_ref.get("lock_sha256")
        ):
            raise PilotValidationError("pilot lock_ref does not match the frozen locks")

    workload = _mapping(plan.get("workload"), "workload")
    matrix_workload = _mapping(matrix.get("workload"), "matrix.workload")
    expected_workload = {
        "requests": matrix_workload.get("requests_per_run"),
        "concurrency": matrix_workload.get("logical_concurrency"),
        "max_tokens": matrix_workload.get("max_tokens"),
        "warmup_requests_per_node": matrix_workload.get("warmup_requests_per_node"),
        "request_timeout_s": matrix_workload.get("request_timeout_s"),
    }
    for key, expected in expected_workload.items():
        if workload.get(key) != expected:
            raise PilotValidationError(f"pilot workload.{key} must equal the formal workload")
    for key in ("requests", "concurrency", "max_tokens", "warmup_requests_per_node", "n_ctx"):
        _integer(workload.get(key), f"workload.{key}", minimum=0 if key == "warmup_requests_per_node" else 1)
    for key in ("temperature", "top_p", "request_timeout_s"):
        _number(workload.get(key), f"workload.{key}")
    _integer(workload.get("seed"), "workload.seed", minimum=0)

    precision = _mapping(plan.get("precision_policy"), "precision_policy")
    if precision.get("independent_unit") != "run":
        raise PilotValidationError("run must remain the independent pilot unit")
    minimum = _integer(precision.get("minimum_formal_repeats"), "minimum_formal_repeats")
    maximum = _integer(precision.get("maximum_formal_repeats"), "maximum_formal_repeats")
    if maximum < minimum:
        raise PilotValidationError("maximum_formal_repeats must be >= minimum")
    minimum_pilot = _integer(
        precision.get("minimum_successful_pilot_repeats_per_cell"),
        "minimum_successful_pilot_repeats_per_cell",
        minimum=3,
    )
    targets = _mapping(precision.get("relative_half_width_targets"), "relative_half_width_targets")
    if set(targets) != set(PRIMARY_METRICS):
        raise PilotValidationError("relative half-width targets must cover the four primary metrics")
    for metric, target in targets.items():
        value = _number(target, f"relative_half_width_targets.{metric}")
        if not 0 < value < 1:
            raise PilotValidationError("relative half-width targets must be between 0 and 1")

    thermal = _mapping(plan.get("thermal_policy"), "thermal_policy")
    candidates = [_number(item, "cooldown candidate") for item in _list(
        thermal.get("calibration_cooldown_candidates_s"), "calibration_cooldown_candidates_s"
    )]
    if candidates != sorted(set(candidates)):
        raise PilotValidationError("cooldown candidates must be unique and ascending")
    _number(thermal.get("temperature_recovery_tolerance_c"), "temperature recovery tolerance")
    _integer(thermal.get("steady_state_window_samples"), "steady_state_window_samples", minimum=2)
    _number(thermal.get("steady_state_temperature_span_c"), "steady_state_temperature_span_c")
    _number(thermal.get("maximum_temperature_c"), "maximum_temperature_c")
    if thermal.get("active_throttling_allowed") is not False:
        raise PilotValidationError("active throttling must remain forbidden")

    telemetry = _mapping(plan.get("telemetry_policy"), "telemetry_policy")
    if pilot_version == 1:
        overhead_limit = _number(
            telemetry.get("maximum_controller_overhead_fraction"),
            "maximum_controller_overhead_fraction",
        )
    else:
        overhead_limit = _number(
            telemetry.get("maximum_worker_collection_overhead_fraction"),
            "maximum_worker_collection_overhead_fraction",
        )
    if not 0 < overhead_limit < 1:
        raise PilotValidationError("telemetry overhead limit must be between 0 and 1")
    if pilot_version > 1:
        failure_policy = _mapping(plan.get("failure_policy"), "failure_policy")
        maximum_attempt_failure_rate = _number(
            failure_policy.get("maximum_attempt_failure_rate"),
            "maximum_attempt_failure_rate",
        )
        minimum_request_success_rate = _number(
            failure_policy.get("minimum_request_success_rate_per_run"),
            "minimum_request_success_rate_per_run",
        )
        if not 0 <= maximum_attempt_failure_rate < 1:
            raise PilotValidationError("maximum attempt failure rate must be below 1")
        if not 0 < minimum_request_success_rate <= 1:
            raise PilotValidationError("minimum request success rate must be in (0, 1]")
        if failure_policy.get("failed_attempts_and_requests_are_preserved") is not True:
            raise PilotValidationError("pilot failures must be preserved")
        if failure_policy.get("retry_is_a_distinct_attempt") is not True:
            raise PilotValidationError("pilot retries must remain distinct attempts")
    if pilot_version >= 4:
        intervals = _mapping(
            telemetry.get("worker_collection_interval_s_by_platform"),
            "worker_collection_interval_s_by_platform",
        )
        if set(intervals) != {"jetson", "raspberry-pi"}:
            raise PilotValidationError("telemetry intervals must cover Jetson and Raspberry Pi")
        for platform, interval in intervals.items():
            _number(interval, f"worker_collection_interval_s_by_platform.{platform}", minimum=1.0)
        if telemetry.get("duplicate_controller_cache_reads_count_as_new_worker_samples") is not False:
            raise PilotValidationError("duplicate Worker cache reads must not count as new samples")

    approved = {
        str(item.get("model_key"))
        for item in model_lock.get("models", [])
        if isinstance(item, Mapping)
        and (item.get("verification") or {}).get("status") == "approved"
    }
    prompts = {
        str(item.get("prompt_id")) for item in prompt_lock.get("prompts", [])
        if isinstance(item, Mapping)
    }
    formal_cells = {_cell_key(cell): cell for cell in expand_formal_matrix(matrix)}
    seen: set[str] = set()
    for stage, records in (
        ("calibration", _list(plan.get("calibration_cells"), "calibration_cells")),
        ("variance", _list(plan.get("variance_cells"), "variance_cells")),
    ):
        for index, raw in enumerate(records):
            cell = _mapping(raw, f"{stage}_cells[{index}]")
            identifier = _text(cell.get("pilot_cell_id"), f"{stage}.pilot_cell_id")
            if identifier in seen:
                raise PilotValidationError(f"duplicate pilot_cell_id: {identifier}")
            seen.add(identifier)
            if _cell_key(cell) not in formal_cells:
                raise PilotValidationError(f"pilot cell is not an exact formal matrix subset: {identifier}")
            if cell.get("model_lock_key") not in approved:
                raise PilotValidationError(f"pilot model is not approved: {identifier}")
            if cell.get("prompt_id") not in prompts:
                raise PilotValidationError(f"pilot prompt is not locked: {identifier}")
            runs = _integer(cell.get("runs"), f"{identifier}.runs")
            if stage == "calibration" and runs != len(candidates) + 1:
                raise PilotValidationError("calibration runs must equal cooldown candidates plus baseline")
            if stage == "variance" and runs < minimum_pilot:
                raise PilotValidationError("variance cells need enough independent pilot runs")


def expand_pilot_plan(
    plan: Mapping[str, Any], matrix: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Return the predeclared serialized pilot order without performing I/O."""
    formal_cells = {_cell_key(cell): cell for cell in expand_formal_matrix(matrix)}
    candidates = list((plan.get("thermal_policy") or {}).get("calibration_cooldown_candidates_s") or [])
    fallback = float((plan.get("thermal_policy") or {}).get("variance_stage_fallback_cooldown_s") or 0)
    output: list[dict[str, Any]] = []
    order_index = 1
    for raw in plan.get("calibration_cells") or []:
        formal = formal_cells[_cell_key(raw)]
        for repeat_index in range(1, int(raw["runs"]) + 1):
            output.append({
                **formal,
                "pilot_id": plan["pilot_id"],
                "pilot_cell_id": raw["pilot_cell_id"],
                "pilot_stage": "calibration",
                "pilot_repeat_index": repeat_index,
                "pilot_order_index": order_index,
                "cooldown_before_s": 0.0 if repeat_index == 1 else float(candidates[repeat_index - 2]),
            })
            order_index += 1

    seed = int((plan.get("execution") or {}).get("seed") or 0)
    variance = list(plan.get("variance_cells") or [])
    max_repeats = max(int(item["runs"]) for item in variance)
    for repeat_index in range(1, max_repeats + 1):
        eligible = [item for item in variance if int(item["runs"]) >= repeat_index]
        eligible.sort(key=lambda item: hashlib.sha256(
            f"phase09-pilot-order-v1:{seed}:{repeat_index}:{item['pilot_cell_id']}".encode("utf-8")
        ).hexdigest())
        for raw in eligible:
            formal = formal_cells[_cell_key(raw)]
            output.append({
                **formal,
                "pilot_id": plan["pilot_id"],
                "pilot_cell_id": raw["pilot_cell_id"],
                "pilot_stage": "variance",
                "pilot_repeat_index": repeat_index,
                "pilot_order_index": order_index,
                "cooldown_before_s": fallback,
            })
            order_index += 1
    return output


def _metric_summary(values: Sequence[float], target: float, minimum: int, maximum: int) -> dict[str, Any]:
    count = len(values)
    average = mean(values) if values else None
    deviation = stdev(values) if count >= 2 else None
    cv = deviation / abs(average) if deviation is not None and average not in {None, 0} else None
    raw_required = math.ceil((Z_95 * cv / target) ** 2) if cv is not None else maximum
    selected = min(max(raw_required, minimum), maximum)
    achieved = cv is not None and raw_required <= maximum
    return {
        "count": count,
        "mean": average,
        "median": median(values) if values else None,
        "sd": deviation,
        "coefficient_of_variation": cv,
        "relative_half_width_target": target,
        "raw_required_repeats": raw_required,
        "selected_repeats": selected,
        "target_achievable_within_cap": achieved,
    }


def _instrumentation(summary: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], float, list[str]]:
    value = summary.get("measurement_instrumentation")
    if not isinstance(value, Mapping):
        return [], 0.0, ["measurement_instrumentation missing"]
    nodes = value.get("nodes")
    if not isinstance(nodes, Mapping) or not nodes:
        return [], 0.0, ["measurement node summaries missing"]
    records = [item for item in nodes.values() if isinstance(item, Mapping)]
    overhead = 0.0
    errors: list[str] = []
    for item in records:
        samples = item.get("worker_collection_overhead_samples_s")
        if not isinstance(samples, list) or not samples:
            errors.append("worker telemetry collection overhead missing")
            continue
        for sample in samples:
            value = sample.get("overhead_s") if isinstance(sample, Mapping) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append("worker telemetry collection overhead is invalid")
                continue
            overhead += float(value)
    return records, overhead, errors


def analyze_pilot(
    plan: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Analyze preserved pilot run summaries without request pseudoreplication."""
    precision = _mapping(plan.get("precision_policy"), "precision_policy")
    thermal = _mapping(plan.get("thermal_policy"), "thermal_policy")
    telemetry = _mapping(plan.get("telemetry_policy"), "telemetry_policy")
    failure_policy = _mapping(plan.get("failure_policy"), "failure_policy")
    minimum = int(precision["minimum_formal_repeats"])
    maximum = int(precision["maximum_formal_repeats"])
    minimum_pilot = int(precision["minimum_successful_pilot_repeats_per_cell"])
    targets = precision["relative_half_width_targets"]
    expected = {item["pilot_cell_id"]: item for item in plan.get("variance_cells") or []}
    calibration_ids = {item["pilot_cell_id"] for item in plan.get("calibration_cells") or []}
    by_cell: dict[str, list[Mapping[str, Any]]] = {}
    seen: set[tuple[str, int]] = set()
    failures: list[dict[str, Any]] = []
    blockers: list[str] = []
    overhead_fractions: list[float] = []
    request_failure_rates: list[float] = []
    for observation in observations:
        cell_id = str(observation.get("pilot_cell_id") or "")
        repeat_index = observation.get("pilot_repeat_index")
        if not cell_id or isinstance(repeat_index, bool) or not isinstance(repeat_index, int):
            raise PilotValidationError("pilot observation identity is incomplete")
        identity = (cell_id, repeat_index)
        if identity in seen:
            raise PilotValidationError(f"duplicate pilot observation: {cell_id}/{repeat_index}")
        seen.add(identity)
        by_cell.setdefault(cell_id, []).append(observation)
        summary = observation.get("summary") if isinstance(observation.get("summary"), Mapping) else {}
        if observation.get("status") != "completed" or summary.get("status") != "completed":
            failures.append({
                "pilot_cell_id": cell_id,
                "pilot_repeat_index": repeat_index,
                "run_id": observation.get("run_id"),
                "error_code": summary.get("error_code") or observation.get("error_code") or "RUN_FAILED",
            })
            continue
        success_rate = summary.get("success_rate")
        if isinstance(success_rate, bool) or not isinstance(success_rate, (int, float)):
            blockers.append(f"{cell_id}/{repeat_index}: request success rate missing")
        else:
            request_failure_rates.append(1.0 - float(success_rate))
            if float(success_rate) < float(failure_policy["minimum_request_success_rate_per_run"]):
                blockers.append(f"{cell_id}/{repeat_index}: request success rate below policy")
        records, overhead, errors = _instrumentation(summary)
        blockers.extend(f"{cell_id}/{repeat_index}: {error}" for error in errors)
        if int(plan.get("pilot_version") or 0) >= 4:
            intervals = telemetry["worker_collection_interval_s_by_platform"]
            for record in records:
                platform = record.get("platform_kind")
                observed_interval = record.get("worker_collection_interval_s")
                expected_interval = intervals.get(platform) if isinstance(platform, str) else None
                if (
                    expected_interval is None
                    or isinstance(observed_interval, bool)
                    or not isinstance(observed_interval, (int, float))
                    or not math.isclose(float(observed_interval), float(expected_interval))
                ):
                    blockers.append(
                        f"{cell_id}/{repeat_index}: Worker telemetry collection interval mismatch"
                    )
        wall_s = float(summary.get("wall_s") or 0.0)
        if wall_s > 0:
            overhead_fractions.append(overhead / wall_s)
        for record in records:
            if int(record.get("sample_count") or 0) < int(telemetry["minimum_measurement_samples_per_node"]):
                blockers.append(f"{cell_id}/{repeat_index}: insufficient telemetry samples")

    cell_summaries: dict[str, Any] = {}
    required_repeats = minimum
    precision_limited: list[str] = []
    for cell_id, declaration in expected.items():
        completed = []
        for observation in by_cell.get(cell_id, []):
            summary = observation.get("summary")
            if observation.get("status") == "completed" and isinstance(summary, Mapping) and summary.get("status") == "completed":
                completed.append(summary)
        metrics: dict[str, Any] = {}
        for metric in PRIMARY_METRICS:
            values = [float(item[metric]) for item in completed if isinstance(item.get(metric), (int, float)) and not isinstance(item.get(metric), bool)]
            metrics[metric] = _metric_summary(values, float(targets[metric]), minimum, maximum)
            required_repeats = max(required_repeats, int(metrics[metric]["selected_repeats"]))
            if not metrics[metric]["target_achievable_within_cap"]:
                precision_limited.append(f"{cell_id}:{metric}")
        if len(completed) < minimum_pilot:
            blockers.append(f"{cell_id}: requires {minimum_pilot} successful independent runs")
        cell_summaries[cell_id] = {
            "declared_runs": declaration["runs"],
            "successful_runs": len(completed),
            "failed_runs": len(by_cell.get(cell_id, [])) - len(completed),
            "metrics": metrics,
            "median_wall_s": median([float(item.get("wall_s") or 0.0) for item in completed]) if completed else None,
        }

    candidates = [float(item) for item in thermal["calibration_cooldown_candidates_s"]]
    tolerance = float(thermal["temperature_recovery_tolerance_c"])
    max_temp = float(thermal["maximum_temperature_c"])
    candidate_pass: dict[str, bool] = {str(item): True for item in candidates}
    calibration_evidence: dict[str, Any] = {}
    for cell_id in calibration_ids:
        records = sorted(by_cell.get(cell_id, []), key=lambda item: int(item["pilot_repeat_index"]))
        first_summary = records[0].get("summary") if records else None
        first_nodes, _, first_errors = _instrumentation(first_summary or {})
        baseline_completed = bool(
            records
            and records[0].get("status") == "completed"
            and isinstance(first_summary, Mapping)
            and first_summary.get("status") == "completed"
        )
        baseline = max(
            (float(item["start_temperature_c"]) for item in first_nodes if isinstance(item.get("start_temperature_c"), (int, float))),
            default=None,
        )
        cell_evidence: list[dict[str, Any]] = []
        if not baseline_completed or baseline is None or first_errors:
            blockers.append(f"{cell_id}: thermal baseline unavailable")
            for candidate in candidates:
                candidate_pass[str(candidate)] = False
        observed_candidates: set[str] = set()
        for record in records[1:]:
            summary = record.get("summary") if isinstance(record.get("summary"), Mapping) else {}
            nodes, _, errors = _instrumentation(summary)
            starts = [float(item["start_temperature_c"]) for item in nodes if isinstance(item.get("start_temperature_c"), (int, float))]
            peaks = [float(item["peak_temperature_c"]) for item in nodes if isinstance(item.get("peak_temperature_c"), (int, float))]
            throttling = [item.get("throttling_sample_count") for item in nodes]
            cooldown = float(record.get("cooldown_before_s") or 0.0)
            passed = (
                record.get("status") == "completed"
                and not errors
                and baseline is not None
                and bool(starts)
                and max(starts) <= baseline + tolerance
                and bool(peaks)
                and max(peaks) <= max_temp
                and all(value in {0, None} for value in throttling)
            )
            if str(cooldown) in candidate_pass:
                observed_candidates.add(str(cooldown))
                candidate_pass[str(cooldown)] = candidate_pass[str(cooldown)] and passed
            cell_evidence.append({
                "cooldown_s": cooldown,
                "baseline_start_temperature_c": baseline,
                "observed_start_temperature_c": max(starts) if starts else None,
                "observed_peak_temperature_c": max(peaks) if peaks else None,
                "passed": passed,
            })
        for candidate in candidates:
            if str(candidate) not in observed_candidates:
                candidate_pass[str(candidate)] = False
        calibration_evidence[cell_id] = cell_evidence
    selected_cooldown = next((item for item in candidates if candidate_pass[str(item)]), None)
    if selected_cooldown is None:
        blockers.append("no tested cooldown satisfied thermal recovery on every calibration cell")

    maximum_overhead = max(overhead_fractions, default=None)
    if maximum_overhead is None:
        blockers.append("telemetry overhead could not be measured")
    elif maximum_overhead > float(telemetry["maximum_worker_collection_overhead_fraction"]):
        blockers.append("worker telemetry collection overhead exceeded the predeclared fraction")

    attempt_failure_rate = len(failures) / len(observations) if observations else None
    if attempt_failure_rate is not None and attempt_failure_rate > float(
        failure_policy["maximum_attempt_failure_rate"]
    ):
        blockers.append("pilot attempt failure rate exceeded the predeclared fraction")

    expected_observations = sum(int(item["runs"]) for item in plan.get("calibration_cells") or []) + sum(
        int(item["runs"]) for item in plan.get("variance_cells") or []
    )
    if len(observations) < expected_observations:
        blockers.append(f"pilot incomplete: {len(observations)}/{expected_observations} observations")
    blockers = sorted(set(blockers))
    return {
        "schema_version": 1,
        "artifact_type": "pilot_analysis",
        "pilot_id": plan.get("pilot_id"),
        "experiment_type": "pilot",
        "formal_pooling_allowed": False,
        "observations": len(observations),
        "expected_observations": expected_observations,
        "successful_observations": len(observations) - len(failures),
        "failures": failures,
        "failure_rate": attempt_failure_rate,
        "failure_decision": {
            "maximum_attempt_failure_rate": failure_policy["maximum_attempt_failure_rate"],
            "minimum_request_success_rate_per_run": failure_policy["minimum_request_success_rate_per_run"],
            "maximum_request_failure_rate_observed": max(request_failure_rates, default=None),
        },
        "cell_summaries": cell_summaries,
        "precision_decision": {
            "selected_formal_repeats": required_repeats,
            "minimum": minimum,
            "maximum": maximum,
            "precision_limited_metrics": sorted(set(precision_limited)),
        },
        "thermal_decision": {
            "selected_minimum_cooldown_s": selected_cooldown,
            "candidate_pass": candidate_pass,
            "evidence": calibration_evidence,
            "steady_state_rule": {
                "window_samples": thermal["steady_state_window_samples"],
                "maximum_temperature_span_c": thermal["steady_state_temperature_span_c"],
                "active_throttling_allowed": False,
            },
        },
        "telemetry_decision": {
            "metric": "worker_internal_collection_time_over_measurement_wall_time",
            "maximum_worker_collection_overhead_fraction_observed": maximum_overhead,
            "maximum_allowed_fraction": telemetry["maximum_worker_collection_overhead_fraction"],
            "controller_probe_elapsed_is_descriptive_only": True,
        },
        "median_successful_run_s": median([
            float(observation["summary"].get("wall_s") or 0.0)
            for observation in observations
            if observation.get("status") == "completed"
            and isinstance(observation.get("summary"), Mapping)
            and observation["summary"].get("status") == "completed"
        ]) if any(
            observation.get("status") == "completed"
            and isinstance(observation.get("summary"), Mapping)
            and observation["summary"].get("status") == "completed"
            for observation in observations
        ) else None,
        "median_total_run_s": median([
            float(observation["total_elapsed_s"])
            for observation in observations
            if observation.get("status") == "completed"
            and isinstance(observation.get("total_elapsed_s"), (int, float))
            and not isinstance(observation.get("total_elapsed_s"), bool)
        ]) if any(
            observation.get("status") == "completed"
            and isinstance(observation.get("total_elapsed_s"), (int, float))
            and not isinstance(observation.get("total_elapsed_s"), bool)
            for observation in observations
        ) else None,
        "blockers": blockers,
        "freeze_ready": not blockers,
    }


__all__ = [
    "PRIMARY_METRICS",
    "PilotValidationError",
    "analyze_pilot",
    "expand_pilot_plan",
    "validate_pilot_plan",
]
