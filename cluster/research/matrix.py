"""Pure validation and workload accounting for a formal research matrix.

Phase 04 defines plans only.  This module performs no file I/O, Worker calls,
campaign scheduling, or benchmark execution.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from cluster.domain.strategy import ExecutionStrategy

from .locks import (
    validate_condition_lock,
    validate_model_lock,
    validate_prompt_lock,
    validate_runtime_lock,
)


EXPERIMENT_TYPES = {"smoke", "pilot", "formal"}
QUALITY_CLASSES = {"clean", "warning", "degraded", "unknown"}


class MatrixValidationError(ValueError):
    """The planned formal matrix or its analysis contract is inconsistent."""


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MatrixValidationError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise MatrixValidationError(f"{label} must be a non-empty list")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatrixValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MatrixValidationError(f"{label} must be a positive integer")
    return value


def _nonnegative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MatrixValidationError(f"{label} must be a non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise MatrixValidationError(f"{label} must be a non-negative number")
    return number


def expand_formal_matrix(matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand template Cartesian products into deterministic base cells.

    A base cell deliberately excludes ``repeat_index``.  The final repeat
    count is frozen after the Phase 09 pilot, and concrete campaign manifests
    add one-based repeat indices without changing the matrix cell identity.
    """
    cells: list[dict[str, Any]] = []
    for template_index, raw_template in enumerate(
        _list(matrix.get("cell_templates"), "cell_templates")
    ):
        template = _mapping(raw_template, f"cell_templates[{template_index}]")
        template_id = _text(template.get("template_id"), f"cell_templates[{template_index}].template_id")
        node_sets = _list(template.get("node_sets"), f"{template_id}.node_sets")
        model_keys = _list(template.get("model_lock_keys"), f"{template_id}.model_lock_keys")
        prompt_ids = _list(template.get("prompt_ids"), f"{template_id}.prompt_ids")
        for raw_node_set in node_sets:
            node_set = _mapping(raw_node_set, f"{template_id}.node_set")
            node_set_id = _text(node_set.get("node_set_id"), f"{template_id}.node_set_id")
            nodes = list(_list(node_set.get("nodes"), f"{node_set_id}.nodes"))
            for model_key in model_keys:
                for prompt_id in prompt_ids:
                    cell_id = "--".join(
                        (template_id, node_set_id, str(model_key), str(prompt_id))
                    )
                    cells.append(
                        {
                            "cell_id": cell_id,
                            "template_id": template_id,
                            "experiment_type": template.get("experiment_type"),
                            "platform_cohort": template.get("platform_cohort"),
                            "strategy": template.get("strategy"),
                            "node_set_id": node_set_id,
                            "node_set": nodes,
                            "node_count": node_set.get("node_count"),
                            "model_lock_key": model_key,
                            "prompt_set_version": template.get("prompt_set_version"),
                            "prompt_id": prompt_id,
                            "parameter_profile": template.get("parameter_profile"),
                            "repeat_index": "campaign_manifest",
                            "order_block": template.get("order_block"),
                            "measurement_quality_policy": template.get(
                                "measurement_quality_policy"
                            ),
                        }
                    )
    return cells


def compute_matrix_volume(matrix: Mapping[str, Any]) -> dict[str, int]:
    """Compute exact logical/physical work and provisional planning budgets."""
    cells = expand_formal_matrix(matrix)
    repeat = _mapping(matrix.get("repeat_axis"), "repeat_axis")
    minimum = _positive_int(repeat.get("minimum"), "repeat_axis.minimum")
    maximum = _positive_int(repeat.get("maximum"), "repeat_axis.maximum")
    if maximum < minimum:
        raise MatrixValidationError("repeat_axis.maximum must be >= minimum")
    workload = _mapping(matrix.get("workload"), "workload")
    requests = _positive_int(workload.get("requests_per_run"), "workload.requests_per_run")
    warmups = _positive_int(
        workload.get("warmup_requests_per_node"),
        "workload.warmup_requests_per_node",
    )
    concurrency = _positive_int(workload.get("logical_concurrency"), "workload.logical_concurrency")
    timeout_s = _nonnegative_number(workload.get("request_timeout_s"), "workload.request_timeout_s")
    cooldown_s = _nonnegative_number(workload.get("model_cooldown_s"), "workload.model_cooldown_s")
    planning = _mapping(matrix.get("planning_assumptions"), "planning_assumptions")
    nominal_run_s = _nonnegative_number(
        planning.get("nominal_successful_run_s"),
        "planning_assumptions.nominal_successful_run_s",
    )
    request_bytes = _positive_int(
        planning.get("bytes_per_physical_request"),
        "planning_assumptions.bytes_per_physical_request",
    )
    run_bytes = _positive_int(
        planning.get("bytes_per_run_metadata"),
        "planning_assumptions.bytes_per_run_metadata",
    )

    logical_per_repeat = len(cells) * requests
    physical_per_repeat = 0
    warmups_per_repeat = 0
    for cell in cells:
        nodes = _positive_int(cell["node_count"], f"{cell['cell_id']}.node_count")
        factor = nodes if cell["strategy"] == ExecutionStrategy.BROADCAST_COMPARE.value else 1
        physical_per_repeat += requests * factor
        warmups_per_repeat += warmups * nodes

    runs_per_repeat = len(cells)
    timeout_run_s = (math.ceil(requests / concurrency) + warmups) * timeout_s + cooldown_s
    storage_per_repeat = physical_per_repeat * request_bytes + runs_per_repeat * run_bytes
    return {
        "base_cells": runs_per_repeat,
        "runs_per_matrix_repeat": runs_per_repeat,
        "runs_minimum": runs_per_repeat * minimum,
        "runs_maximum": runs_per_repeat * maximum,
        "logical_requests_per_matrix_repeat": logical_per_repeat,
        "logical_requests_minimum": logical_per_repeat * minimum,
        "logical_requests_maximum": logical_per_repeat * maximum,
        "physical_requests_per_matrix_repeat": physical_per_repeat,
        "physical_requests_minimum": physical_per_repeat * minimum,
        "physical_requests_maximum": physical_per_repeat * maximum,
        "warmup_requests_per_matrix_repeat": warmups_per_repeat,
        "warmup_requests_minimum": warmups_per_repeat * minimum,
        "warmup_requests_maximum": warmups_per_repeat * maximum,
        "model_loads_per_matrix_repeat": runs_per_repeat,
        "model_unloads_per_matrix_repeat": runs_per_repeat,
        "nominal_runtime_seconds_per_matrix_repeat": round(runs_per_repeat * nominal_run_s),
        "nominal_runtime_seconds_minimum": round(runs_per_repeat * nominal_run_s * minimum),
        "nominal_runtime_seconds_maximum": round(runs_per_repeat * nominal_run_s * maximum),
        "timeout_envelope_seconds_per_run": round(timeout_run_s),
        "timeout_envelope_seconds_minimum": round(timeout_run_s * runs_per_repeat * minimum),
        "timeout_envelope_seconds_maximum": round(timeout_run_s * runs_per_repeat * maximum),
        "estimated_storage_bytes_per_matrix_repeat": storage_per_repeat,
        "estimated_storage_bytes_minimum": storage_per_repeat * minimum,
        "estimated_storage_bytes_maximum": storage_per_repeat * maximum,
    }


def validate_formal_matrix(
    matrix: Mapping[str, Any],
    *,
    model_lock: Mapping[str, Any],
    prompt_lock: Mapping[str, Any],
    runtime_lock: Mapping[str, Any],
    experiment_conditions: Mapping[str, Any],
) -> dict[str, int]:
    """Validate every expanded formal cell against the approved lock set."""
    validate_model_lock(model_lock)
    validate_prompt_lock(prompt_lock)
    validate_runtime_lock(runtime_lock)
    validate_condition_lock(experiment_conditions)
    if matrix.get("schema_version") != 1 or matrix.get("matrix_version") != 1:
        raise MatrixValidationError("matrix schema_version and matrix_version must be 1")
    if matrix.get("controller_participant_policy") != "forbidden":
        raise MatrixValidationError("Controller participation must be forbidden")
    lock_ref = _mapping(matrix.get("lock_ref"), "lock_ref")
    common_fingerprint = model_lock.get("lock_sha256")
    if any(
        lock.get("lock_id") != lock_ref.get("lock_id")
        or lock.get("lock_sha256") != lock_ref.get("lock_sha256")
        for lock in (model_lock, prompt_lock, runtime_lock, experiment_conditions)
    ):
        raise MatrixValidationError("matrix lock_ref does not match the frozen lock set")
    if common_fingerprint != lock_ref.get("lock_sha256"):
        raise MatrixValidationError("matrix lock fingerprint mismatch")

    repeat_axis = _mapping(matrix.get("repeat_axis"), "repeat_axis")
    repeat_minimum = _positive_int(repeat_axis.get("minimum"), "repeat_axis.minimum")
    repeat_maximum = _positive_int(repeat_axis.get("maximum"), "repeat_axis.maximum")
    selected_count = _positive_int(
        repeat_axis.get("selected_count"), "repeat_axis.selected_count"
    )
    if not repeat_minimum <= selected_count <= repeat_maximum:
        raise MatrixValidationError("repeat_axis.selected_count must be within the frozen range")
    if repeat_axis.get("selection_frozen") is not True:
        raise MatrixValidationError("repeat_axis selection must be frozen")
    _text(repeat_axis.get("decision_evidence"), "repeat_axis.decision_evidence")

    approved_models = {
        item["model_key"]
        for item in model_lock.get("models", [])
        if (item.get("verification") or {}).get("status") == "approved"
    }
    locked_prompts = {item["prompt_id"] for item in prompt_lock.get("prompts", [])}
    prompt_version = prompt_lock.get("prompt_set_version")
    workers = {item["node"] for item in runtime_lock.get("workers", [])}
    cohorts = {
        item["cohort_id"]: item for item in runtime_lock.get("formal_cohorts", [])
    }
    profiles = _mapping(matrix.get("parameter_profiles"), "parameter_profiles")
    condition_fixed = _mapping(experiment_conditions.get("fixed_profile"), "fixed_profile")
    condition_benchmark = _mapping(
        experiment_conditions.get("benchmark_profile"), "benchmark_profile"
    )
    condition_platforms = _mapping(
        experiment_conditions.get("platform_profiles"), "platform_profiles"
    )
    valid_profile_components = {
        "inference_profile": {condition_fixed.get("profile_id")},
        "benchmark_profile": {condition_benchmark.get("profile_id")},
        "platform_profile": {
            item.get("profile_id") for item in condition_platforms.values()
            if isinstance(item, Mapping)
        },
    }
    for profile_id, raw_profile in profiles.items():
        profile = _mapping(raw_profile, f"parameter_profiles.{profile_id}")
        for component, accepted in valid_profile_components.items():
            if profile.get(component) not in accepted:
                raise MatrixValidationError(
                    f"parameter profile {profile_id} has an unlocked {component}"
                )
    quality_policies = _mapping(
        matrix.get("measurement_quality_policies"), "measurement_quality_policies"
    )
    for policy_id, raw_policy in quality_policies.items():
        policy = _mapping(raw_policy, f"measurement_quality_policies.{policy_id}")
        for key in ("primary_include", "sensitivity_include", "exclude"):
            values = policy.get(key)
            if not isinstance(values, list) or not set(values).issubset(QUALITY_CLASSES):
                raise MatrixValidationError(f"invalid quality classes in {policy_id}.{key}")

    cells = expand_formal_matrix(matrix)
    declared_models = set(
        _list(matrix.get("formal_model_lock_keys"), "formal_model_lock_keys")
    )
    declared_prompts = set(_list(matrix.get("prompt_ids"), "prompt_ids"))
    if not declared_models or not declared_models.issubset(approved_models):
        raise MatrixValidationError("formal_model_lock_keys must all be approved")
    if declared_prompts != locked_prompts:
        raise MatrixValidationError("matrix prompt_ids must exactly match the locked prompt set")
    seen_cells: set[str] = set()
    seen_templates: set[str] = set()
    for cell in cells:
        if cell["cell_id"] in seen_cells:
            raise MatrixValidationError(f"duplicate matrix cell: {cell['cell_id']}")
        seen_cells.add(cell["cell_id"])
        seen_templates.add(str(cell["template_id"]))
        if cell["experiment_type"] not in EXPERIMENT_TYPES:
            raise MatrixValidationError(f"invalid experiment_type: {cell['cell_id']}")
        if cell["experiment_type"] != "formal":
            raise MatrixValidationError("active formal matrix may not mix smoke or pilot cells")
        try:
            strategy = ExecutionStrategy(cell["strategy"])
        except (TypeError, ValueError) as exc:
            raise MatrixValidationError(f"unknown strategy: {cell['strategy']}") from exc
        node_count = _positive_int(cell["node_count"], f"{cell['cell_id']}.node_count")
        nodes = cell["node_set"]
        if node_count != len(nodes) or len(set(nodes)) != len(nodes):
            raise MatrixValidationError(f"node count or uniqueness mismatch: {cell['cell_id']}")
        if any(node not in workers for node in nodes):
            raise MatrixValidationError(f"Controller or unknown node in cell: {cell['cell_id']}")
        cohort = cohorts.get(cell["platform_cohort"])
        if not cohort or not set(nodes).issubset(set(cohort.get("workers") or [])):
            raise MatrixValidationError(f"cell crosses or misses its runtime cohort: {cell['cell_id']}")
        if strategy is ExecutionStrategy.SINGLE_NODE and node_count != 1:
            raise MatrixValidationError("single_node cells require exactly one Worker")
        if strategy in {
            ExecutionStrategy.BROADCAST_COMPARE,
            ExecutionStrategy.MODEL_PARALLEL_RPC,
        } and node_count < 2:
            raise MatrixValidationError(f"{strategy.value} requires at least two Workers")
        if cell["model_lock_key"] not in approved_models:
            raise MatrixValidationError(f"formal cell uses an unapproved model: {cell['model_lock_key']}")
        if cell["model_lock_key"] not in declared_models:
            raise MatrixValidationError(f"cell model is not declared by the matrix: {cell['model_lock_key']}")
        if cell["prompt_set_version"] != prompt_version or cell["prompt_id"] not in locked_prompts:
            raise MatrixValidationError(f"formal cell uses an invalid prompt lock: {cell['cell_id']}")
        if cell["parameter_profile"] not in profiles:
            raise MatrixValidationError(f"unknown parameter profile: {cell['parameter_profile']}")
        if cell["measurement_quality_policy"] not in quality_policies:
            raise MatrixValidationError(
                f"unknown measurement quality policy: {cell['measurement_quality_policy']}"
            )
        _text(cell["order_block"], f"{cell['cell_id']}.order_block")

    if len(seen_templates) != len(matrix.get("cell_templates") or []):
        raise MatrixValidationError("template_id values must be unique")
    volume = compute_matrix_volume(matrix)
    expected = _mapping(matrix.get("expected_volume"), "expected_volume")
    for key, actual in volume.items():
        if expected.get(key) != actual:
            raise MatrixValidationError(
                f"expected_volume.{key} is {expected.get(key)!r}, computed {actual!r}"
            )
    return volume


def validate_experiment_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("schema_version") != 1 or protocol.get("protocol_version") != 1:
        raise MatrixValidationError("protocol schema_version and protocol_version must be 1")
    separation = _mapping(protocol.get("experiment_type_separation"), "experiment_type_separation")
    if separation.get("mixing_allowed") is not False:
        raise MatrixValidationError("smoke, pilot, and formal results must not be mixed")
    repeat = _mapping(protocol.get("repeat_policy"), "repeat_policy")
    if repeat.get("independent_unit") != "run" or repeat.get("final_count_source") != "phase-09-pilot":
        raise MatrixValidationError("the independent repeat must be a run selected by the pilot")
    minimum = _positive_int(repeat.get("minimum"), "repeat_policy.minimum")
    maximum = _positive_int(repeat.get("maximum"), "repeat_policy.maximum")
    selected = _positive_int(repeat.get("selected_count"), "repeat_policy.selected_count")
    if not minimum <= selected <= maximum or repeat.get("selection_frozen") is not True:
        raise MatrixValidationError("the pilot-selected repeat count must be frozen within range")
    _text(repeat.get("decision_evidence"), "repeat_policy.decision_evidence")
    ordering = _mapping(protocol.get("execution_order"), "execution_order")
    _positive_int(ordering.get("seed"), "execution_order.seed")
    if ordering.get("algorithm") != "seeded_randomized_blocks":
        raise MatrixValidationError("execution order must use seeded randomized blocks")
    timing = _mapping(protocol.get("timing_boundary"), "timing_boundary")
    if timing.get("model_download") != "excluded" or timing.get("model_load") != "excluded":
        raise MatrixValidationError("download and model load must remain outside measurement timing")
    if protocol.get("parallel_formal_runs") is not False:
        raise MatrixValidationError("formal runs must be serialized for the frozen protocol")
    thermal = _mapping(protocol.get("thermal_policy"), "thermal_policy")
    if thermal.get("formal_start_blocked_until_frozen") is not False:
        raise MatrixValidationError("the pilot-derived thermal policy must be frozen")
    if _nonnegative_number(
        thermal.get("minimum_cooldown_s"), "thermal_policy.minimum_cooldown_s"
    ) <= 0:
        raise MatrixValidationError("thermal_policy.minimum_cooldown_s must be positive")
    _text(
        thermal.get("final_stabilization_rule_source"),
        "thermal_policy.final_stabilization_rule_source",
    )


def validate_analysis_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != 1 or plan.get("analysis_plan_version") != 1:
        raise MatrixValidationError("analysis schema_version and analysis_plan_version must be 1")
    units = _mapping(plan.get("analysis_units"), "analysis_units")
    if units.get("independent_inference_unit") != "run":
        raise MatrixValidationError("run must be the independent inferential unit")
    if units.get("request_level_role") != "nested_descriptive_only":
        raise MatrixValidationError("request-level observations may not be treated as independent repeats")
    ci = _mapping(plan.get("confidence_intervals"), "confidence_intervals")
    if ci.get("level") != 0.95 or ci.get("resampling_unit") != "run":
        raise MatrixValidationError("95% confidence intervals must resample runs")
    _positive_int(ci.get("bootstrap_resamples"), "confidence_intervals.bootstrap_resamples")
    summaries = set(_list(plan.get("required_summaries"), "required_summaries"))
    required = {"mean", "median", "sd", "iqr", "p50", "p95", "ci95"}
    if not required.issubset(summaries):
        raise MatrixValidationError("analysis plan is missing required summary statistics")
    failures = _mapping(plan.get("failure_and_exclusion"), "failure_and_exclusion")
    if failures.get("imputation") != "none" or failures.get("preserve_raw") is not True:
        raise MatrixValidationError("failures must not be imputed or deleted")
    separation = _mapping(plan.get("experiment_type_separation"), "experiment_type_separation")
    if separation.get("pooling_allowed") is not False:
        raise MatrixValidationError("smoke, pilot, and formal analyses must remain separate")


__all__ = [
    "MatrixValidationError",
    "compute_matrix_volume",
    "expand_formal_matrix",
    "validate_analysis_plan",
    "validate_experiment_protocol",
    "validate_formal_matrix",
]
