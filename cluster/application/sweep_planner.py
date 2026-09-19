"""Pure bounded sweep compiler over caller-supplied cached evidence.

No Dashboard imports, file reads, Worker probes, model loads, or dispatch. S01
plans are previews, not execution authorization. Existing strategy definitions
own scenario/work-unit semantics; we do not materialize request tasks to count.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Iterator

from cluster.benchmark.strategies import get_strategy
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.sweep import (
    BaseCell, CapabilityResult, PlanCounts, ResolutionContext, ResolvedPlan,
    RunCondition, SweepSpec, Trial, Workload, canonical_json, fail, fingerprint,
    read_json,
)


@dataclass(frozen=True)
class _Participant:
    name: str
    role: str = "worker"


def candidate_count(spec: SweepSpec) -> int:
    """Check this *before* asking _conditions to build a Cartesian product."""
    if spec.combination == "explicit":
        return len(spec.explicit)
    if spec.combination == "one_at_a_time":
        return 1 + sum(len(axis.values) for axis in spec.axes)
    count = 1
    for axis in spec.axes:
        count *= len(axis.values)
    return count


def _conditions(spec: SweepSpec) -> Iterator[RunCondition]:
    if spec.combination == "explicit":
        yield from spec.explicit
    elif spec.combination == "one_at_a_time":
        yield spec.base
        for axis in spec.axes:
            for value in axis.values:
                yield spec.base.with_values({axis.name: value})
    else:
        for values in itertools.product(*(axis.values for axis in spec.axes)):
            yield spec.base.with_values(dict(zip((axis.name for axis in spec.axes), values)))


def _cell(spec: SweepSpec, context: ResolutionContext, condition: RunCondition, index: int) -> BaseCell:
    models = {model.ref: model for model in context.models}
    prompts = {(prompt.ref, prompt.model_ref): prompt for prompt in context.prompts}
    workers_by_id = {worker.worker_id: worker for worker in context.workers}
    profiles = {profile.profile_id: profile for profile in spec.rpc_profiles}
    rpc = condition.execution_strategy == "model_parallel_rpc"
    profile = profiles.get(condition.rpc_profile_ref) if rpc else None
    if rpc:
        if profile is None:
            fail("RPC condition must reference a declared RpcProfile")
        if condition.worker_ids and condition.worker_ids != profile.worker_ids:
            fail("RPC Worker order/set must match the atomic profile")
        condition = replace(condition, worker_ids=profile.worker_ids)
    elif condition.rpc_profile_ref is not None:
        fail("rpc_profile_ref only applies to RPC")
    if not condition.worker_ids:
        fail("condition needs explicit Worker IDs")

    model = models.get(condition.model_ref)
    prompt = prompts.get((condition.prompt_ref, condition.model_ref))
    workers = tuple(workers_by_id[name] for name in condition.worker_ids if name in workers_by_id)
    checks = []

    def check(status: str, code: str, subject: str = ""):
        checks.append(CapabilityResult(status, code, subject))

    required = (profile.coordinator_id,) if profile else condition.worker_ids
    model_checks = [
        item for item in context.model_checks
        if item.model_ref == condition.model_ref
        and item.worker_id in required
        and item.n_ctx in (None, condition.n_ctx)
    ]
    for item in model_checks:
        if rpc and item.kind == "memory" and item.code != "MODEL_CONTEXT_LIMIT_EXCEEDED":
            # Replicated file+KV estimates cannot prove a distributed RPC allocation.
            check("unknown", "RPC_MEMORY_REQUIRES_PROFILE_PREFLIGHT", item.worker_id)
        else:
            check(item.status, item.code, item.worker_id)
    if model is None:
        check("blocked", "MODEL_REFERENCE_UNRESOLVED", condition.model_ref)
    else:
        check(model.availability, "MODEL_AVAILABILITY", model.ref)
        for node in required:
            if not any(item.kind == "runtime" and item.worker_id == node for item in model_checks):
                check(model.runtime_compatibility, "MODEL_RUNTIME_COMPATIBILITY", node)
        if model.availability == "valid":
            for node in required:
                if node not in model.installed_workers:
                    check("blocked", "MODEL_NOT_INSTALLED", node)
        if model.artifact_kind == "artifact_set":
            check("blocked", "ARTIFACT_SET_LOADER_NOT_IMPLEMENTED", model.ref)
    for node in condition.worker_ids:
        worker = workers_by_id.get(node)
        if worker is None:
            check("blocked", "WORKER_REFERENCE_UNRESOLVED", node)
            continue
        check(worker.availability, "WORKER_AVAILABILITY", node)
        if not rpc:
            if worker.platform == "raspberry-pi" and condition.n_gpu_layers != 0:
                check("blocked", "PI_REQUIRES_CPU_ONLY", node)
            elif worker.platform == "unknown":
                check("unknown", "WORKER_PLATFORM_UNKNOWN", node)
        else:
            check(getattr(worker, f"rpc_{profile.split_mode}"), "RPC_MODE_CAPABILITY", node)
    if len({worker.endpoint_identity for worker in workers}) != len(workers):
        check("blocked", "DUPLICATE_PHYSICAL_WORKER")

    if prompt is None:
        check("unknown", "PROMPT_VARIANT_UNRESOLVED", condition.prompt_ref)
    else:
        if model is not None and prompt.template_sha256 != model.template_sha256:
            check("blocked", "PROMPT_TEMPLATE_MISMATCH", prompt.ref)
        if not prompt.input_tokens_exact:
            check("unknown", "TOKEN_BUDGET_UNKNOWN", prompt.ref)
        elif prompt.rendered_input_tokens + condition.max_tokens > condition.n_ctx:
            check("blocked", "CONTEXT_BUDGET_EXCEEDED", prompt.ref)
        else:
            check("valid", "TOKEN_BUDGET_CACHED", prompt.ref)
        if prompt.mode == "token_length_profile":
            if not prompt.input_tokens_exact:
                check("unknown", "TOKEN_PROFILE_NOT_PREPARED", prompt.ref)
            elif prompt.rendered_input_tokens != prompt.target_input_tokens:
                check("blocked", "TOKEN_PROFILE_TARGET_MISMATCH", prompt.ref)
    for name in ("n_threads", "n_batch"):
        if getattr(condition, name) is not None:
            if rpc:
                check("blocked", "RUNTIME_AXIS_NOT_IMPLEMENTED", name)
            elif not workers or any(worker.load_profile != "valid" for worker in workers):
                check("blocked", "WORKER_LOAD_PROFILE_UNVERIFIED", name)
    if profile and profile.rpc_gpu_layers != "all":
        check("blocked", "RPC_GPU_POLICY_NOT_IMPLEMENTED", profile.profile_id)
    # RPC uses rpc_gpu_layers. Keep the legacy omitted ordinary default inert,
    # but reject nondefault fixed values and explicit-mode variation as well as
    # axis declarations. Explicit conditions do not appear in spec.axes.
    explicit_rpc_layers = {
        item.n_gpu_layers for item in spec.explicit
        if item.execution_strategy == "model_parallel_rpc"
    }
    if rpc and (
        any(axis.name == "n_gpu_layers" for axis in spec.axes)
        or condition.n_gpu_layers != RunCondition.__dataclass_fields__["n_gpu_layers"].default
        or len(explicit_rpc_layers) > 1
    ):
        check("blocked", "ORDINARY_GPU_AXIS_NOT_APPLICABLE_TO_RPC", "n_gpu_layers")

    # Validate only existing scalar fields through its strict parser, without
    # passing new keys to the legacy ignored_config_keys path. The entire typed
    # condition (including unsupported axes) remains in the cell. This local
    # object is for pure strategy validation/counting, NEVER an execution config.
    existing = {
        key: value for key, value in condition.to_dict().items()
        if key in {"execution_strategy", "sweep_mode", "n_ctx", "concurrency", "max_tokens",
                   "n_gpu_layers", "n_threads", "n_batch", "temperature", "top_p", "seed", "requests",
                   "warmup_requests", "request_timeout_s", "persist_prompt", "response_storage_mode"}
    }
    existing.update(node_names=list(condition.worker_ids),
                    model_id=model.model_id if model else "unresolved.gguf",
                    prompt="sweep validation only")
    if profile:
        existing.pop("n_threads", None)
        existing.pop("n_batch", None)
        existing.update(rpc_coordinator_node=profile.coordinator_id,
                        rpc_split_mode=profile.split_mode, rpc_split_policy=profile.split_policy,
                        rpc_tensor_split=[dict(profile.weights_by_worker)[node] for node in profile.worker_ids]
                        if profile.split_policy == "custom" else [],
                        acknowledge_experimental_rpc=True)
    config = ExperimentConfig.from_dict(existing, strict=True)
    config.validate()
    strategy = get_strategy(condition.execution_strategy)
    participants = tuple(_Participant(name) for name in condition.worker_ids)
    try:
        strategy.validate(participants, config)
    except ValueError as exc:
        fail(str(exc))
    definitions = strategy.definitions(participants, config)
    workload = Workload(
        scenarios=len(definitions),
        logical_requests=config.requests * len(definitions),
        physical_requests=strategy.work_units(config, len(participants)),
        # Existing core warms once per selected Worker before all node scenarios.
        warmup_calls=config.warmup_requests * (1 if rpc else len(participants)),
        model_loads=1 if rpc else len(participants),
    )
    semantic_condition = condition.to_dict()
    for key in ("model_ref", "prompt_ref", "rpc_profile_ref"):
        semantic_condition.pop(key)
    prompt_identity = None
    if prompt:
        prompt_identity = {key: value for key, value in prompt.to_dict().items()
                           if key not in {"ref", "model_ref", "rendered_input_tokens", "input_token_source", "input_tokens_exact"}}
    identity = {
        "schema_version": 1, "condition": semantic_condition,
        "model": model.identity() if model else {"unresolved": condition.model_ref},
        "prompt": prompt_identity if prompt else {"unresolved": condition.prompt_ref},
        "workers": [{"worker_id": node, "endpoint_identity": workers_by_id[node].endpoint_identity,
                     "platform": workers_by_id[node].platform,
                     **({"runtime_commit": workers_by_id[node].runtime_commit}
                        if workers_by_id[node].runtime_commit else {})}
                    if node in workers_by_id else {"unresolved": node}
                    for node in condition.worker_ids],
        "rpc": profile.semantic_identity() if profile else None,
    }
    status = "blocked" if any(c.status == "blocked" for c in checks) else (
        "unknown" if any(c.status == "unknown" for c in checks) else "valid")
    return BaseCell("cell_" + fingerprint(identity), index, condition, model, prompt,
                    workers, profile, tuple(checks), workload, status)


def compile_plan(spec: SweepSpec, context: ResolutionContext) -> ResolvedPlan:
    """Return all candidate rows; duplicates/exclusions never silently disappear.

    The conservative candidate×repeat budget applies BEFORE dedupe/exclusion.
    Excluding rows cannot be used to make an unbounded Cartesian product cheap.
    """
    # Validate even objects built with direct dataclass constructors. No mutable
    # caller lists or dictionaries are retained in the plan.
    spec = SweepSpec.from_dict(spec.to_dict())
    context = ResolutionContext.from_dict(context.to_dict())
    candidate_cells = candidate_count(spec)
    if candidate_cells * spec.repeat_count > spec.budget.max_trials:
        fail("candidate trials exceed max_trials before materialization")
    cells = []
    seen = {}
    exclusions = {entry.cell_id: entry.reason for entry in spec.exclusions}
    for index, condition in enumerate(_conditions(spec)):
        cell = _cell(spec, context, condition, index)
        if cell.cell_id in seen:
            cell = replace(cell, duplicate_of=seen[cell.cell_id])
        else:
            seen[cell.cell_id] = index
        if cell.cell_id in exclusions:
            cell = replace(cell, exclusion_reason=exclusions[cell.cell_id])
        cells.append(cell)
    if set(exclusions) - set(seen):
        fail("exclusion references a cell outside this plan")
    unique = [cell for cell in cells if cell.duplicate_of is None]
    included = [cell for cell in unique if cell.exclusion_reason is None]
    counts = {}
    for key in ("scenarios", "logical_requests", "physical_requests", "warmup_calls", "model_loads"):
        counts[key] = sum(getattr(cell.workload, key) for cell in included) * spec.repeat_count
    if counts["physical_requests"] + counts["warmup_calls"] > spec.budget.max_physical_requests:
        fail("planned requests including warmup exceed max_physical_requests")
    # Capability/readiness observations affect readiness, not semantic identity.
    # Original axis/profile order and raw ratios remain in the source spec hash.
    plan_sha = fingerprint({"schema_version": 1, "spec": spec.to_dict(),
                            "cells": [cell.cell_id for cell in cells]})
    trial_inputs = [(cell, repeat) for cell in included for repeat in range(1, spec.repeat_count + 1)]
    execution = list(range(len(trial_inputs)))
    if spec.execution.order == "seeded_randomized":
        execution.sort(key=lambda index: fingerprint({
            "order_seed": spec.execution.order_seed,
            "cell_id": trial_inputs[index][0].cell_id,
            "repeat": trial_inputs[index][1],
        }))
    order = {generation: dispatch for dispatch, generation in enumerate(execution)}
    trials = tuple(Trial(
        "trial_" + fingerprint({"plan_sha256": plan_sha, "cell_id": cell.cell_id, "sweep_repeat_index": repeat}),
        cell.cell_id, repeat, index, order[index],
        "pending" if cell.status == "valid" else "blocked",
    ) for index, (cell, repeat) in enumerate(trial_inputs))
    capabilities = [CapabilityResult("blocked", "SWEEP_EXECUTION_NOT_IMPLEMENTED")]
    if spec.execution.mode == "disjoint_parallel":
        capabilities.append(CapabilityResult("blocked", "RESOURCE_RESERVATIONS_NOT_IMPLEMENTED"))
    return ResolvedPlan(
        spec, context, plan_sha, tuple(cells), trials,
        PlanCounts(candidate_cells, len(unique), len(cells) - len(unique),
                   len(unique) - len(included), len(included),
                   sum(cell.status == "valid" for cell in included),
                   sum(cell.status == "blocked" for cell in included),
                   sum(cell.status == "unknown" for cell in included), len(trials), Workload(**counts)),
        tuple(capabilities),
        resolution_state="resolved" if all(
            cell.model is not None and cell.prompt is not None
            and len(cell.workers) == len(cell.condition.worker_ids) for cell in cells
        ) else "unresolved",
    )


def verify_plan(value: str) -> ResolvedPlan:
    """Recompile imported JSON and reject altered IDs, hashes, counts or fields.

    This checks internal integrity only. A future Start API must resolve its own
    saved identities and fresh evidence; a hash is not authentication/approval.
    """
    raw = read_json(value, max_bytes=32 * 1024 * 1024)
    if "spec" not in raw or "context" not in raw:
        fail("resolved plan requires spec and resolution context")
    result = compile_plan(SweepSpec.from_dict(raw["spec"]), ResolutionContext.from_dict(raw["context"]))
    if canonical_json(raw) != result.to_json():
        fail("resolved plan integrity mismatch")
    return result
