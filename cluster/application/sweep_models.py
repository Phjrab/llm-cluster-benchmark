"""Read-only adapter from cached Model Library evidence to sweep identities.

Callers provide server-owned catalog and inventory snapshots. This module does
not read files, contact Workers, download models, tokenize prompts or dispatch
jobs. A resolved preview is not execution approval.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from cluster.application.model_service import (
    ModelPreflightError,
    WorkerModelInventory,
    model_license_fingerprint,
    validate_model_preflight,
)
from cluster.application.sweep_planner import candidate_count, compile_plan, verify_plan
from cluster.domain.experiment import ExperimentConfig
from cluster.domain.identifiers import validate_model_id
from cluster.domain.model import ModelCatalogEntry, ModelVerificationStatus, estimate_memory_fit
from cluster.domain.sweep import (
    ModelCheck,
    ModelReference,
    PromptVariant,
    Record,
    ResolutionContext,
    ResolvedPlan,
    SweepSpec,
    WorkerReference,
    digest,
    fail,
    ref,
)


@dataclass(frozen=True)
class ModelCandidate(Record):
    model_ref: str
    catalog_id: str
    installed_workers: tuple[str, ...]
    downloadable: bool
    identity_resolved: bool
    runtime_verified_workers: tuple[str, ...]
    reason_codes: tuple[str, ...]
    formal_approval: str = "not_assessed"


@dataclass(frozen=True)
class CatalogSweepPreview(Record):
    plan: ResolvedPlan
    candidates: tuple[ModelCandidate, ...]


def _license_accepted(
    entry: ModelCatalogEntry, acceptances: Mapping[str, Mapping]
) -> bool:
    record = acceptances.get(entry.id, {})
    return record.get("fingerprint") == model_license_fingerprint(entry)


def _runtime_status(entry: ModelCatalogEntry, worker: WorkerReference) -> tuple[str, str]:
    if (
        entry.verification_status == ModelVerificationStatus.UNSUPPORTED
        or worker.backend_verified is False
    ):
        return "blocked", "MODEL_BACKEND_UNSUPPORTED"
    if (
        worker.backend_verified is True
        and worker.runtime_commit
        and worker.runtime_commit in entry.verified_llama_cpp_commits
        and worker.platform in entry.verified_platforms
        and entry.verification_status
        in {ModelVerificationStatus.VERIFIED, ModelVerificationStatus.RECOMMENDED}
    ):
        return "valid", "MODEL_RUNTIME_CACHED_VERIFIED"
    return "unknown", "MODEL_RUNTIME_NOT_VERIFIED"


def _memory_status(
    entry: ModelCatalogEntry, worker: WorkerReference, n_ctx: int
) -> tuple[str, str]:
    if entry.context_length_advertised and n_ctx > entry.context_length_advertised:
        return "blocked", "MODEL_CONTEXT_LIMIT_EXCEEDED"
    if worker.memory_available_mb == 0:
        return "blocked", "MODEL_MEMORY_ESTIMATE_EXCEEDED"
    if (
        worker.memory_total_mb is None
        or worker.memory_available_mb is None
        or entry.kv_cache_bytes_per_token is None
    ):
        return "unknown", "MODEL_MEMORY_ESTIMATE_UNKNOWN"
    fit = estimate_memory_fit(
        entry,
        memory_total_mb=worker.memory_total_mb,
        memory_available_mb=worker.memory_available_mb,
        context_length=n_ctx,
    )
    if fit.fits is True:
        return "valid", "MODEL_MEMORY_ESTIMATE_FITS"
    if fit.fits is False:
        return "blocked", "MODEL_MEMORY_ESTIMATE_EXCEEDED"
    return "unknown", "MODEL_MEMORY_ESTIMATE_UNKNOWN"


def _rpc_memory_status(
    entry: ModelCatalogEntry,
    worker: WorkerReference,
    n_ctx: int,
    share: float | None,
) -> tuple[str, str]:
    """Conservative participant check using a profile's requested weight share.

    The mapped GGUF bytes are apportioned by the requested ratio. KV, compute,
    backend/RPC buffers and the OS reserve remain per-participant because the
    pinned runtime does not expose a trustworthy predicted placement. Auto
    placement therefore stays unknown until the native runtime reports it.
    """
    if entry.context_length_advertised and n_ctx > entry.context_length_advertised:
        return "blocked", "MODEL_CONTEXT_LIMIT_EXCEEDED"
    conservative_auto = share is None
    if conservative_auto:
        # A participant that can fit the whole model plus every per-device
        # reserve is safe regardless of the runtime's eventual auto ratio.
        share = 1.0
    if worker.memory_available_mb == 0:
        return "blocked", "RPC_PARTICIPANT_MEMORY_ESTIMATE_EXCEEDED"
    if (
        worker.memory_total_mb is None
        or worker.memory_available_mb is None
        or entry.kv_cache_bytes_per_token is None
        or entry.size_bytes is None
    ):
        return "unknown", "RPC_PARTICIPANT_MEMORY_ESTIMATE_UNKNOWN"
    fit = estimate_memory_fit(
        entry,
        memory_total_mb=worker.memory_total_mb,
        memory_available_mb=worker.memory_available_mb,
        context_length=n_ctx,
        observed_size_bytes=max(1, math.ceil(entry.size_bytes * share)),
    )
    if fit.fits is True:
        return (
            "valid",
            "RPC_PARTICIPANT_MEMORY_CONSERVATIVE_FITS"
            if conservative_auto
            else "RPC_PARTICIPANT_MEMORY_ESTIMATE_FITS",
        )
    if fit.fits is False:
        if conservative_auto:
            return "unknown", "RPC_MEMORY_AUTO_PLACEMENT_UNKNOWN"
        return "blocked", "RPC_PARTICIPANT_MEMORY_ESTIMATE_EXCEEDED"
    return "unknown", "RPC_PARTICIPANT_MEMORY_ESTIMATE_UNKNOWN"


def preview_catalog_sweep(
    spec: SweepSpec,
    *,
    selections: Mapping[str, str],
    catalog: Sequence[ModelCatalogEntry],
    inventories: Sequence[WorkerModelInventory],
    workers: Sequence[WorkerReference],
    prompts: Sequence[PromptVariant],
    acceptances: Mapping[str, Mapping] | None = None,
    gated_access: bool = False,
) -> CatalogSweepPreview:
    """Resolve alias-to-catalog selections without trusting supplied hashes.

    Missing and catalog-only selections remain visible. Installed files,
    runtime smoke evidence, memory estimates and formal approval remain distinct.
    """
    spec = SweepSpec.from_dict(spec.to_dict())
    if candidate_count(spec) * spec.repeat_count > spec.budget.max_trials:
        fail("candidate trials exceed max_trials before model resolution")
    if not isinstance(selections, Mapping) or not 1 <= len(selections) <= 128:
        fail("model selections must contain 1..128 references")
    if acceptances is not None and not isinstance(acceptances, Mapping):
        fail("model acceptances must be a mapping")
    for model_id, record in (acceptances or {}).items():
        validate_model_id(model_id)
        if not isinstance(record, Mapping):
            fail("model acceptance records must be mappings")
    if (
        len(workers) > 64
        or len(inventories) > 64
        or len(catalog) > 4096
        or type(gated_access) is not bool
    ):
        fail("invalid cached model snapshot bounds")

    workers = tuple(WorkerReference.from_dict(worker.to_dict()) for worker in workers)
    if len({worker.worker_id for worker in workers}) != len(workers):
        fail("duplicate cached Worker")
    catalog_by_id = {entry.id: entry for entry in catalog}
    inventory_by_id = {inventory.node: inventory for inventory in inventories}
    if len(catalog_by_id) != len(catalog) or len(inventory_by_id) != len(inventories):
        fail("duplicate catalog or inventory identity")
    for inventory in inventories:
        if len(inventory.by_id()) != len(inventory.models):
            fail("duplicate installed model identity")

    contexts = {spec.base.n_ctx, *(item.n_ctx for item in spec.explicit)}
    for axis in spec.axes:
        if axis.name == "n_ctx":
            contexts.update(axis.values)
    if len(selections) * len(workers) * (len(contexts) + 2) > 32768:
        fail("model evidence exceeds bounded preview budget")

    models: list[ModelReference] = []
    candidates: list[ModelCandidate] = []
    checks: list[ModelCheck] = []
    for model_ref, catalog_id in selections.items():
        ref(model_ref)
        validate_model_id(catalog_id)
        entry = catalog_by_id.get(catalog_id)
        reasons: list[str] = []
        installed: list[str] = []
        runtime_verified: list[str] = []
        observed = []
        if entry is None:
            candidates.append(
                ModelCandidate(
                    model_ref,
                    catalog_id,
                    (),
                    False,
                    False,
                    (),
                    ("CATALOG_MODEL_UNRESOLVED",),
                )
            )
            for worker in workers:
                checks.append(
                    ModelCheck(
                        model_ref,
                        worker.worker_id,
                        "installation",
                        "blocked",
                        "CATALOG_MODEL_UNRESOLVED",
                    )
                )
            continue

        accepted = (
            not entry.requires_license_acceptance
            or _license_accepted(entry, acceptances or {})
        )
        downloadable = entry.download_eligibility_for(
            license_accepted=accepted, gated_access=gated_access
        )["eligible"]
        locked = bool(entry.identity_locked and entry.quantization and entry.architecture)
        try:
            digest(entry.download_revision, 40)
            digest(entry.sha256)
        except ValueError:
            locked = False
        if not locked:
            reasons.append("MODEL_PINNED_IDENTITY_REQUIRED")
        if entry.multipart:
            reasons.append("ARTIFACT_SET_LOADER_NOT_IMPLEMENTED")

        for worker in workers:
            status, code = "valid", "MODEL_INSTALLED_IDENTITY_MATCH"
            inventory = inventory_by_id.get(worker.worker_id)
            installed_model = inventory.by_id().get(catalog_id) if inventory else None
            try:
                validate_model_preflight(
                    node_names=[worker.worker_id],
                    inventories=inventory_by_id,
                    model_ids=[catalog_id],
                    execution_strategy="replicated_round_robin",
                    rpc_coordinator_node=None,
                    catalog=catalog_by_id,
                )
            except ModelPreflightError as exc:
                status, code = "blocked", "MODEL_PREFLIGHT_" + exc.code.value
            if status == "valid" and not locked:
                status, code = "blocked", "MODEL_PINNED_IDENTITY_REQUIRED"
            if status == "valid" and not accepted:
                status, code = "blocked", "MODEL_LICENSE_NOT_ACCEPTED"
            if status == "valid" and entry.multipart:
                status, code = "blocked", "ARTIFACT_SET_LOADER_NOT_IMPLEMENTED"
            if status == "valid" and installed_model.size_bytes != entry.size_bytes:
                status, code = "blocked", "MODEL_SIZE_MISMATCH"
            if status == "valid" and (
                not installed_model.metadata_inspected
                or installed_model.metadata_contract != "gguf-metadata-v1"
            ):
                status, code = "unknown", "MODEL_METADATA_UNVERIFIED"
            if status == "valid" and installed_model.architecture != entry.architecture:
                status, code = "blocked", "MODEL_ARCHITECTURE_MISMATCH"
            if status == "valid":
                try:
                    digest(installed_model.chat_template_hash)
                    digest(installed_model.tokenizer_metadata_hash)
                except ValueError:
                    status, code = "unknown", "MODEL_TEMPLATE_IDENTITY_UNAVAILABLE"
            if status == "valid":
                installed.append(worker.worker_id)
                observed.append(installed_model)
            checks.append(
                ModelCheck(model_ref, worker.worker_id, "installation", status, code)
            )
            if status != "valid":
                reasons.append(code)

            runtime_status, runtime_code = _runtime_status(entry, worker)
            checks.append(
                ModelCheck(
                    model_ref,
                    worker.worker_id,
                    "runtime",
                    runtime_status,
                    runtime_code,
                )
            )
            if runtime_status == "valid" and status == "valid":
                runtime_verified.append(worker.worker_id)
            for n_ctx in sorted(contexts):
                memory_status, memory_code = _memory_status(entry, worker, n_ctx)
                checks.append(
                    ModelCheck(
                        model_ref,
                        worker.worker_id,
                        "memory",
                        memory_status,
                        memory_code,
                        n_ctx,
                    )
                )

        workers_by_id = {worker.worker_id: worker for worker in workers}
        for profile in spec.rpc_profiles:
            if profile.split_policy == "equal":
                shares = {node: 1.0 / len(profile.worker_ids) for node in profile.worker_ids}
            elif profile.split_policy == "custom":
                weights = dict(profile.weights_by_worker)
                total = sum(weights.values())
                shares = {node: weights[node] / total for node in profile.worker_ids}
            else:
                shares = {node: None for node in profile.worker_ids}
            for worker_id in profile.worker_ids:
                worker = workers_by_id.get(worker_id)
                if worker is None:
                    continue
                for n_ctx in sorted(contexts):
                    status, code = _rpc_memory_status(
                        entry, worker, n_ctx, shares[worker_id]
                    )
                    checks.append(
                        ModelCheck(
                            model_ref,
                            worker_id,
                            "memory",
                            status,
                            code,
                            n_ctx,
                            profile.profile_id,
                        )
                    )

        templates = {
            (model.chat_template_hash, model.tokenizer_metadata_hash)
            for model in observed
        }
        resolved = locked and not entry.multipart and len(templates) == 1
        if len(templates) > 1:
            reasons.append("MODEL_TEMPLATE_EVIDENCE_CONFLICT")
            for worker in workers:
                checks.append(
                    ModelCheck(
                        model_ref,
                        worker.worker_id,
                        "installation",
                        "blocked",
                        "MODEL_TEMPLATE_EVIDENCE_CONFLICT",
                    )
                )
        if resolved:
            template_sha256, tokenizer_sha256 = next(iter(templates))
            models.append(
                ModelReference.from_dict(
                    {
                        "ref": model_ref,
                        "catalog_id": entry.id,
                        "model_id": entry.id,
                        "artifact_sha256": entry.sha256,
                        "source_revision": entry.download_revision,
                        "quantization": entry.quantization,
                        "template_sha256": template_sha256,
                        "size_bytes": entry.size_bytes,
                        "architecture": entry.architecture,
                        "tokenizer_sha256": tokenizer_sha256,
                        "installed_workers": installed,
                        "availability": "valid",
                        "runtime_compatibility": "unknown",
                    }
                )
            )
        elif not reasons:
            reasons.append("MODEL_TEMPLATE_IDENTITY_UNAVAILABLE")
        candidates.append(
            ModelCandidate(
                model_ref,
                catalog_id,
                tuple(installed),
                downloadable,
                resolved,
                tuple(runtime_verified),
                tuple(dict.fromkeys(reasons)),
            )
        )

    context = ResolutionContext(
        tuple(models), tuple(prompts), workers, tuple(checks)
    )
    return CatalogSweepPreview(compile_plan(spec, context), tuple(candidates))


def build_cell_config(
    plan: ResolvedPlan,
    *,
    trial_id: str,
    sweep_id: str,
    attempt_id: str,
    prompt_text: str,
) -> ExperimentConfig:
    """Build one model-bound child without dispatch or nested model expansion.

    S06/S07 must re-resolve fresh evidence, reserve resources and authorize Start.
    """
    plan = verify_plan(plan.to_json())
    trial = next((item for item in plan.trials if item.trial_id == trial_id), None)
    if trial is None:
        fail("trial is not in this plan")
    cell = next(item for item in plan.cells if item.cell_id == trial.cell_id)
    if cell.status != "valid" or cell.exclusion_reason:
        fail("cell is not ready for a concrete child")
    if (
        not isinstance(prompt_text, str)
        or hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        != cell.prompt.text_sha256
    ):
        fail("private prompt variant identity mismatch")

    config_values = cell.condition.to_dict()
    for key in ("model_ref", "prompt_ref", "rpc_profile_ref", "worker_ids"):
        config_values.pop(key)
    trace = {
        "sweep_id": sweep_id,
        "plan_sha256": plan.plan_sha256,
        "cell_id": cell.cell_id,
        "trial_id": trial_id,
        "attempt_id": attempt_id,
        "sweep_repeat_index": trial.sweep_repeat_index,
        "model_sha256": cell.model.artifact_sha256,
        "template_sha256": cell.prompt.template_sha256,
        "prompt_sha256": cell.prompt.text_sha256,
        "prompt_mode": cell.prompt.mode,
        "model_identity": cell.model.identity(),
    }
    if cell.rpc_profile is not None:
        trace["rpc_profile"] = cell.rpc_profile.to_dict()
    if cell.prompt.target_input_tokens is not None:
        trace["target_input_tokens"] = cell.prompt.target_input_tokens
    config_values.update(
        model_id=cell.model.model_id,
        node_names=list(cell.condition.worker_ids),
        prompt=prompt_text,
        sweep=trace,
    )
    if cell.rpc_profile is not None:
        profile = cell.rpc_profile
        config_values.update(
            rpc_coordinator_node=profile.coordinator_id,
            rpc_split_mode=profile.split_mode,
            rpc_split_policy=profile.split_policy,
            rpc_tensor_split=(
                [dict(profile.weights_by_worker)[node] for node in profile.worker_ids]
                if profile.split_policy == "custom"
                else []
            ),
            rpc_gpu_layers=profile.rpc_gpu_layers,
            acknowledge_experimental_rpc=True,
        )
    config = ExperimentConfig.from_dict(config_values, strict=True)
    config.validate()
    return config
