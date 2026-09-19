"""Versioned, immutable sweep planning contracts. No I/O or execution authority.

Use from_dict/from_json at boundaries. Lists are frozen as tuples. Axis order and
Worker order are semantic; mapping key order is not. Raw prompts and credentials
are deliberately absent from these public planning objects.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, fields
from fractions import Fraction
from typing import Any

from .errors import DomainValidationError
from .identifiers import validate_model_id, validate_node_id
from .model import validate_quantization

SCHEMA_VERSION = 1
MAX_DOCUMENT_BYTES = 1_048_576
MAX_AXIS_VALUES = 64
MAX_TRIALS = 500
AXIS_NAMES = frozenset({
    "concurrency", "n_ctx", "max_tokens", "model_ref", "prompt_ref", "n_gpu_layers",
    "n_threads", "n_batch", "temperature", "top_p", "seed", "rpc_profile_ref",
})


def fail(message: str) -> None:
    raise DomainValidationError(message)


def integer(value: Any, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        fail(f"{label} must be an integer in [{low}, {high}]")
    return value


def number(value: Any, label: str, low: float, high: float) -> float:
    if type(value) not in (int, float):
        fail(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        fail(f"{label} must be a finite number")
    if not math.isfinite(result) or not low <= result <= high:
        fail(f"{label} must be a finite number in [{low}, {high}]")
    return result


def text(value: Any, label: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail(f"{label} must be nonempty text (max {limit})")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        fail(f"{label} must not contain control characters")
    return value


def ref(value: Any) -> str:
    value = text(value, "reference", 80)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        fail("invalid reference")
    return value


def digest(value: Any, length: int = 64) -> str:
    if not isinstance(value, str) or not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
        fail(f"identity must be a lowercase {length}-character hex digest")
    return value


def choice(value: Any, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        fail(f"expected one of {choices}")
    return value


def boolean(value: Any) -> bool:
    if type(value) is not bool:
        fail("expected boolean")
    return value


def sequence(value: Any, label: str, maximum: int, minimum: int = 0) -> list:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        fail(f"{label} must be a list of {minimum}..{maximum} items")
    return value


def worker_ids(value: Any) -> tuple[str, ...]:
    result = tuple(sequence(value, "worker_ids", 64))
    for item in result:
        if not isinstance(item, str):
            fail("worker ID must be text")
        validate_node_id(item)
    if len(set(result)) != len(result):
        fail("duplicate Worker ID")
    return result


def unique_refs(values: tuple, label: str, key=lambda item: item.ref) -> None:
    ids = [key(item) for item in values]
    if len(set(ids)) != len(ids):
        fail(f"duplicate {label}")


def _wire(value: Any) -> Any:
    if isinstance(value, Record):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    return value


class Record:
    def to_dict(self) -> dict[str, Any]:
        return {field.name: _wire(getattr(self, field.name)) for field in fields(self)}

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


def parse(cls, raw: Any, required: tuple[str, ...], validators: dict):
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        fail(f"{cls.__name__} must be an object")
    allowed = {field.name for field in fields(cls)}
    if set(raw) - allowed:
        fail(f"Unknown {cls.__name__} keys: {', '.join(sorted(set(raw) - allowed))}")
    if set(required) - set(raw):
        fail(f"Missing {cls.__name__} keys: {', '.join(sorted(set(required) - set(raw)))}")
    return cls(**{key: validators[key](value) for key, value in raw.items()})


def read_json(value: str, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> dict:
    if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes:
        fail("sweep document exceeds byte budget")

    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                fail(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    try:
        result = json.loads(value, object_pairs_hook=pairs,
                            parse_constant=lambda _: fail("non-finite JSON number"))
    except (ValueError, RecursionError) as exc:
        raise DomainValidationError("invalid sweep JSON") from exc
    if not isinstance(result, dict):
        fail("sweep document must be an object")
    return result


def canonical_json(value: Any) -> str:
    """v1: sorted mapping keys, ordered lists, integral floats -> ints, -0 -> 0.

    Nonintegral finite floats use Python's shortest round-trippable JSON spelling.
    A change to this rule requires a new schema version.
    """
    def normalize(item):
        if type(item) is float:
            if not math.isfinite(item):
                fail("non-finite semantic value")
            return int(item) if item.is_integer() else item
        if isinstance(item, dict):
            return {key: normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(value) for value in item]
        return item
    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def axis_value(name: str, value: Any) -> Any:
    bounds = {
        "concurrency": (1, 256), "n_ctx": (128, 16384), "max_tokens": (1, 1024),
        "n_gpu_layers": (0, 120), "n_threads": (1, 1024), "n_batch": (1, 16384),
        "seed": (-1, 2_147_483_647),
    }
    if name in bounds:
        return integer(value, name, *bounds[name])
    if name in {"temperature", "top_p"}:
        return number(value, name, 0, 2 if name == "temperature" else 1)
    if name in {"model_ref", "prompt_ref", "rpc_profile_ref"}:
        return ref(value)
    fail(f"unsupported sweep axis: {name}")


@dataclass(frozen=True)
class AxisSpec(Record):
    name: str
    values: tuple

    @classmethod
    def from_dict(cls, raw):
        obj = parse(cls, raw, ("name", "values"), {
            "name": lambda v: choice(v, tuple(sorted(AXIS_NAMES))),
            "values": lambda v: tuple(sequence(v, "axis values", MAX_AXIS_VALUES, 1)),
        })
        return cls(obj.name, tuple(axis_value(obj.name, value) for value in obj.values))


@dataclass(frozen=True)
class RunCondition(Record):
    model_ref: str
    prompt_ref: str
    worker_ids: tuple[str, ...] = ()
    execution_strategy: str = "replicated_round_robin"
    sweep_mode: str = "cumulative"
    rpc_profile_ref: str | None = None
    n_ctx: int = 4096
    concurrency: int = 4
    max_tokens: int = 128
    n_gpu_layers: int = 30
    n_threads: int | None = None
    n_batch: int | None = None
    temperature: float = 0.0
    top_p: float = 0.9
    seed: int = 42
    requests: int = 20
    warmup_requests: int = 1
    request_timeout_s: float = 600.0
    persist_prompt: bool = True
    response_storage_mode: str = "full"

    @classmethod
    def from_dict(cls, raw):
        validators = {name: (lambda v, name=name: axis_value(name, v)) for name in AXIS_NAMES}
        for name in ("n_threads", "n_batch", "rpc_profile_ref"):
            validators[name] = lambda v, name=name: None if v is None else axis_value(name, v)
        validators.update({
            "worker_ids": worker_ids,
            "execution_strategy": lambda v: choice(v, ("single_node", "replicated_round_robin", "broadcast_compare", "node_sweep", "model_parallel_rpc")),
            "sweep_mode": lambda v: choice(v, ("cumulative", "individual")),
            "requests": lambda v: integer(v, "requests", 1, 10000),
            "warmup_requests": lambda v: integer(v, "warmup_requests", 0, 10),
            "request_timeout_s": lambda v: number(v, "request_timeout_s", 0.001, 86400),
            "persist_prompt": boolean,
            "response_storage_mode": lambda v: choice(v, ("full", "hash_only", "none")),
        })
        return parse(cls, raw, ("model_ref", "prompt_ref"), validators)

    def with_values(self, values: dict) -> RunCondition:
        return self.from_dict({**self.to_dict(), **values})


@dataclass(frozen=True)
class RpcProfile(Record):
    profile_id: str
    worker_ids: tuple[str, ...]
    coordinator_id: str
    split_mode: str = "layer"
    split_policy: str = "auto"
    weights_by_worker: tuple[tuple[str, float], ...] = ()
    rpc_gpu_layers: str | int = "all"

    def to_dict(self):
        result = super().to_dict()
        result["weights_by_worker"] = dict(self.weights_by_worker)
        return result

    def semantic_identity(self) -> dict:
        result = self.to_dict()
        result.pop("profile_id")
        # Exact rational ratios avoid float rounding collisions/overflow in dedupe.
        if self.weights_by_worker:
            weights = dict(self.weights_by_worker)
            first = Fraction(str(weights[self.worker_ids[0]]))
            result["weights_by_worker"] = {
                node: str(Fraction(str(weights[node])) / first) for node in self.worker_ids
            }
        return result

    @classmethod
    def from_dict(cls, raw):
        def weights(value):
            if not isinstance(value, dict) or len(value) > 64:
                fail("weights_by_worker must be a bounded object")
            result = tuple((ref(key), number(item, "RPC weight", 0, math.inf)) for key, item in value.items())
            if any(weight <= 0 for _, weight in result):
                fail("RPC weights must be positive")
            return result
        obj = parse(cls, raw, ("profile_id", "worker_ids", "coordinator_id"), {
            "profile_id": ref, "worker_ids": worker_ids, "coordinator_id": ref,
            "split_mode": lambda v: choice(v, ("layer", "row")),
            "split_policy": lambda v: choice(v, ("auto", "equal", "custom")),
            "weights_by_worker": weights,
            "rpc_gpu_layers": lambda v: v if v == "all" else integer(v, "rpc_gpu_layers", 0, 999),
        })
        if len(obj.worker_ids) < 2 or obj.coordinator_id not in obj.worker_ids:
            fail("RPC needs >=2 Workers and a selected coordinator")
        if obj.split_policy == "custom":
            if set(dict(obj.weights_by_worker)) != set(obj.worker_ids):
                fail("RPC weights must exactly match Worker IDs")
        elif obj.weights_by_worker:
            fail("only custom RPC profiles may contain weights")
        return obj


@dataclass(frozen=True)
class ExecutionPolicy(Record):
    mode: str = "sequential"
    max_parallel_jobs: int = 1
    order: str = "as_listed"
    order_seed: int = 42
    backfill_policy: str = "strict"
    backfill_window: int = 8
    failure_policy: str = "stop"
    cleanup_policy: str = "quarantine_on_uncertainty"
    cooldown_s: float = 2.0
    model_cache_policy: str = "reload_per_cell"
    rpc_session_policy: str = "new_session_per_cell"
    retry_policy: str = "manual"

    @classmethod
    def from_dict(cls, raw):
        obj = parse(cls, raw, (), {
            "mode": lambda v: choice(v, ("sequential", "disjoint_parallel")),
            "max_parallel_jobs": lambda v: integer(v, "max_parallel_jobs", 1, 2),
            "order": lambda v: choice(v, ("as_listed", "seeded_randomized")),
            "order_seed": lambda v: integer(v, "order_seed", 0, 2_147_483_647),
            "backfill_policy": lambda v: choice(v, ("strict", "bounded")),
            "backfill_window": lambda v: integer(v, "backfill_window", 1, 64),
            "failure_policy": lambda v: choice(v, ("stop", "continue_ready")),
            "cleanup_policy": lambda v: choice(v, ("quarantine_on_uncertainty",)),
            "cooldown_s": lambda v: number(v, "cooldown_s", 0, 86400),
            "model_cache_policy": lambda v: choice(v, ("reload_per_cell",)),
            "rpc_session_policy": lambda v: choice(v, ("new_session_per_cell",)),
            "retry_policy": lambda v: choice(v, ("manual",)),
        })
        if obj.mode == "sequential" and obj.max_parallel_jobs != 1:
            fail("sequential requires max_parallel_jobs=1")
        if obj.mode == "sequential" and obj.backfill_policy != "strict":
            fail("sequential execution requires strict dispatch order")
        return obj


@dataclass(frozen=True)
class Budget(Record):
    max_trials: int = MAX_TRIALS
    max_physical_requests: int = 1_000_000

    @classmethod
    def from_dict(cls, raw):
        return parse(cls, raw, (), {
            "max_trials": lambda v: integer(v, "max_trials", 1, MAX_TRIALS),
            "max_physical_requests": lambda v: integer(v, "max_physical_requests", 1, 1_000_000),
        })


@dataclass(frozen=True)
class Exclusion(Record):
    cell_id: str
    reason: str

    @classmethod
    def from_dict(cls, raw):
        return parse(cls, raw, ("cell_id", "reason"), {
            "cell_id": lambda v: "cell_" + digest(v[5:]) if isinstance(v, str) and v.startswith("cell_") else fail("invalid cell ID"),
            "reason": lambda v: text(v, "exclusion reason", 512),
        })


@dataclass(frozen=True)
class SweepSpec(Record):
    base: RunCondition
    schema_version: int = SCHEMA_VERSION
    artifact_type: str = "sweep_spec"
    mode: str = "exploratory"
    revision: int = 1
    combination: str = "grid"
    axes: tuple[AxisSpec, ...] = ()
    explicit: tuple[RunCondition, ...] = ()
    rpc_profiles: tuple[RpcProfile, ...] = ()
    repeat_count: int = 1
    execution: ExecutionPolicy = ExecutionPolicy()
    budget: Budget = Budget()
    exclusions: tuple[Exclusion, ...] = ()

    @classmethod
    def from_dict(cls, raw):
        obj = parse(cls, raw, ("base",), {
            "base": RunCondition.from_dict,
            "schema_version": lambda v: integer(v, "schema_version", 1, 1),
            "artifact_type": lambda v: choice(v, ("sweep_spec",)),
            "mode": lambda v: choice(v, ("exploratory",)),
            "revision": lambda v: integer(v, "revision", 1, 2_147_483_647),
            "combination": lambda v: choice(v, ("grid", "one_at_a_time", "explicit")),
            "axes": lambda v: tuple(AxisSpec.from_dict(x) for x in sequence(v, "axes", len(AXIS_NAMES))),
            "explicit": lambda v: tuple(RunCondition.from_dict(x) for x in sequence(v, "explicit", MAX_TRIALS)),
            "rpc_profiles": lambda v: tuple(RpcProfile.from_dict(x) for x in sequence(v, "rpc_profiles", 64)),
            "repeat_count": lambda v: integer(v, "repeat_count", 1, MAX_TRIALS),
            "execution": ExecutionPolicy.from_dict, "budget": Budget.from_dict,
            "exclusions": lambda v: tuple(Exclusion.from_dict(x) for x in sequence(v, "exclusions", MAX_TRIALS)),
        })
        unique_refs(obj.axes, "axis", lambda item: item.name)
        unique_refs(obj.rpc_profiles, "RPC profile", lambda item: item.profile_id)
        unique_refs(obj.exclusions, "exclusion", lambda item: item.cell_id)
        if obj.combination == "explicit":
            if obj.axes or not obj.explicit:
                fail("explicit mode requires complete explicit conditions and no axes")
        elif obj.explicit:
            fail("explicit conditions require explicit mode")
        if obj.exclusions and obj.revision < 2:
            fail("exclusions require a new revision (>=2)")
        return obj

    @classmethod
    def from_json(cls, value: str):
        return cls.from_dict(read_json(value))


@dataclass(frozen=True)
class ModelReference(Record):
    ref: str
    catalog_id: str
    model_id: str
    artifact_sha256: str
    source_revision: str
    quantization: str
    template_sha256: str
    artifact_kind: str = "single_gguf"
    installed_workers: tuple[str, ...] = ()
    availability: str = "unknown"
    runtime_compatibility: str = "unknown"
    size_bytes: int | None = None
    architecture: str | None = None
    tokenizer_sha256: str | None = None

    def to_dict(self):
        value = super().to_dict()
        for key in ("size_bytes", "architecture", "tokenizer_sha256"):
            if value[key] is None:
                value.pop(key)
        return value

    def identity(self):
        return {key: value for key, value in self.to_dict().items()
                if key not in {"ref", "installed_workers", "availability", "runtime_compatibility"}}

    @classmethod
    def from_dict(cls, raw):
        def model_id(value):
            if not isinstance(value, str):
                fail("model_id must be text")
            validate_model_id(value)
            return value
        return parse(cls, raw, ("ref", "catalog_id", "model_id", "artifact_sha256", "source_revision", "quantization", "template_sha256"), {
            "ref": ref,
            "catalog_id": lambda v: model_id(v) if isinstance(v, str) and v.endswith(".gguf") else ref(v),
            "model_id": model_id,
            "artifact_sha256": digest, "source_revision": lambda v: digest(v, 40),
            "quantization": lambda v: validate_quantization(text(v, "quantization", 32)),
            "template_sha256": digest,
            "size_bytes": lambda v: integer(v, "size_bytes", 1, 1024**5),
            "architecture": lambda v: text(v, "architecture", 80),
            "tokenizer_sha256": digest,
            "artifact_kind": lambda v: choice(v, ("single_gguf", "artifact_set")),
            "installed_workers": worker_ids,
            "availability": lambda v: choice(v, ("valid", "blocked", "unknown")),
            "runtime_compatibility": lambda v: choice(v, ("valid", "blocked", "unknown")),
        })


@dataclass(frozen=True)
class PromptVariant(Record):
    ref: str
    model_ref: str
    text_sha256: str
    template_sha256: str
    mode: str = "same_text"
    rendered_input_tokens: int | None = None
    input_token_source: str = "unavailable"
    input_tokens_exact: bool = False
    target_input_tokens: int | None = None

    @classmethod
    def from_dict(cls, raw):
        obj = parse(cls, raw, ("ref", "model_ref", "text_sha256", "template_sha256"), {
            "ref": ref, "model_ref": ref, "text_sha256": digest, "template_sha256": digest,
            "mode": lambda v: choice(v, ("same_text", "token_length_profile")),
            "rendered_input_tokens": lambda v: None if v is None else integer(v, "rendered_input_tokens", 0, 1_000_000),
            "input_token_source": ref, "input_tokens_exact": boolean,
            "target_input_tokens": lambda v: None if v is None else integer(v, "target_input_tokens", 1, 16384),
        })
        if obj.input_tokens_exact and (obj.rendered_input_tokens is None or obj.input_token_source == "unavailable"):
            fail("exact token count requires count and source")
        if (obj.mode == "token_length_profile") != (obj.target_input_tokens is not None):
            fail("token_length_profile requires a target; same_text has no target")
        return obj


@dataclass(frozen=True)
class WorkerReference(Record):
    worker_id: str
    endpoint_identity: str
    platform: str
    availability: str = "unknown"
    rpc_layer: str = "unknown"
    rpc_row: str = "unknown"
    load_profile: str = "unknown"
    runtime_commit: str | None = None
    backend_verified: bool | None = None
    memory_total_mb: int | None = None
    memory_available_mb: int | None = None

    def to_dict(self):
        value = super().to_dict()
        if self.load_profile == "unknown":
            value.pop("load_profile")  # retain S01 default serialized shape
        for key in ("runtime_commit", "backend_verified", "memory_total_mb", "memory_available_mb"):
            if value[key] is None:
                value.pop(key)
        return value

    @classmethod
    def from_dict(cls, raw):
        return parse(cls, raw, ("worker_id", "endpoint_identity", "platform"), {
            "worker_id": ref, "endpoint_identity": ref,
            "runtime_commit": lambda v: digest(v, 40),
            "backend_verified": boolean,
            "memory_total_mb": lambda v: integer(v, "memory_total_mb", 1, 1024**4),
            "memory_available_mb": lambda v: integer(v, "memory_available_mb", 0, 1024**4),
            "platform": lambda v: choice(v, ("jetson", "raspberry-pi", "unknown")),
            **{key: (lambda v: choice(v, ("valid", "blocked", "unknown")))
               for key in ("availability", "rpc_layer", "rpc_row", "load_profile")},
        })


@dataclass(frozen=True)
class ModelCheck(Record):
    model_ref: str
    worker_id: str
    kind: str
    status: str
    code: str
    n_ctx: int | None = None
    profile_id: str | None = None

    def to_dict(self):
        value = super().to_dict()
        if self.profile_id is None:
            value.pop("profile_id")
        return value

    @classmethod
    def from_dict(cls, raw):
        return parse(cls, raw, ("model_ref", "worker_id", "kind", "status", "code"), {
            "model_ref": ref,
            "worker_id": ref,
            "kind": lambda v: choice(v, ("installation", "runtime", "memory")),
            "status": lambda v: choice(v, ("valid", "blocked", "unknown")),
            "code": ref,
            "n_ctx": lambda v: None if v is None else integer(v, "n_ctx", 128, 16384),
            "profile_id": lambda v: None if v is None else ref(v),
        })


@dataclass(frozen=True)
class ResolutionContext(Record):
    """Caller-supplied cached facts, never a client authorization or live probe."""
    models: tuple[ModelReference, ...]
    prompts: tuple[PromptVariant, ...]
    workers: tuple[WorkerReference, ...]
    model_checks: tuple[ModelCheck, ...] = ()

    def to_dict(self):
        value = super().to_dict()
        if not self.model_checks:
            value.pop("model_checks")
        return value

    @classmethod
    def from_dict(cls, raw):
        obj = parse(cls, raw, ("models", "prompts", "workers"), {
            "models": lambda v: tuple(ModelReference.from_dict(x) for x in sequence(v, "models", 128)),
            "prompts": lambda v: tuple(PromptVariant.from_dict(x) for x in sequence(v, "prompts", 512)),
            "workers": lambda v: tuple(WorkerReference.from_dict(x) for x in sequence(v, "workers", 64)),
            "model_checks": lambda v: tuple(ModelCheck.from_dict(x) for x in sequence(v, "model_checks", 32768)),
        })
        unique_refs(obj.models, "model reference")
        unique_refs(obj.prompts, "prompt/model reference", lambda p: (p.ref, p.model_ref))
        unique_refs(obj.workers, "Worker", lambda w: w.worker_id)
        same_text = {}
        for prompt in obj.prompts:
            previous = same_text.setdefault(prompt.ref, (prompt.mode, prompt.text_sha256))
            if prompt.mode != previous[0] or (prompt.mode == "same_text" and prompt.text_sha256 != previous[1]):
                fail("same_text prompt variants must have identical text hashes and mode")
        return obj


@dataclass(frozen=True)
class CapabilityResult(Record):
    status: str
    code: str
    subject: str = ""


@dataclass(frozen=True)
class Workload(Record):
    scenarios: int
    logical_requests: int
    physical_requests: int
    warmup_calls: int
    model_loads: int
    duration_s: None = None
    storage_bytes: None = None


@dataclass(frozen=True)
class BaseCell(Record):
    cell_id: str
    candidate_index: int
    condition: RunCondition
    model: ModelReference | None
    prompt: PromptVariant | None
    workers: tuple[WorkerReference, ...]
    rpc_profile: RpcProfile | None
    capabilities: tuple[CapabilityResult, ...]
    workload: Workload
    status: str
    duplicate_of: int | None = None
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class Trial(Record):
    trial_id: str
    cell_id: str
    sweep_repeat_index: int
    generation_order_index: int
    execution_order_index: int
    status: str


@dataclass(frozen=True)
class PlanCounts(Record):
    candidate_cells: int
    unique_cells: int
    duplicate_cells: int
    excluded_cells: int
    included_cells: int
    valid_cells: int
    blocked_cells: int
    unknown_cells: int
    trials: int
    workload: Workload


@dataclass(frozen=True)
class ResolvedPlan(Record):
    """Resolved preview; execution still requires a durable S06 supervisor claim."""
    spec: SweepSpec
    context: ResolutionContext
    plan_sha256: str
    cells: tuple[BaseCell, ...]
    trials: tuple[Trial, ...]
    counts: PlanCounts
    capabilities: tuple[CapabilityResult, ...]
    schema_version: int = SCHEMA_VERSION
    artifact_type: str = "exploratory_sweep_plan"
    resolution_state: str = "unresolved"
    executable: bool = False
