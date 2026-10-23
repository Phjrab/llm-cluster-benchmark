"""PREPARE_ONLY adapter: emits existing Sweep payloads, never dispatches jobs."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from cluster.application.sweep_planner import compile_plan
from cluster.domain.sweep import ResolutionContext, SweepSpec, WorkerReference, fingerprint
from cluster.integrations.legacy_inventory_runtime import load_nodes
from .bank import compose, long_document_variants, model_mapping, question_bank, sha

BLOCKS = ("B0", "B1", "B2", "B3", "B4a", "B4b", "B5", "B6-smoke", "B6", "B7-language", "B7-reasoning", "B7-quantization")
FIXED = ("EDU01", "EYE04", "DOC01", "EVID04")
DEFAULT_MODELS = {"B0": ["M01"], "B1": ["M01", "M03", "M04", "M05"],
                  "B2": ["M01"], "B6-smoke": ["R01"], "B6": ["R01"], "B7-reasoning": ["M07"]}


def inventory_workers(path: Path, selected: list[str]) -> list[WorkerReference]:
    nodes = {n.name: n for n in load_nodes(path, require_legacy_head=False)}
    if not selected or len(selected) > 4 or len(set(selected)) != len(selected):
        raise ValueError("SELECT_1_TO_4_DISTINCT_JETSON_WORKERS")
    result = []
    for name in selected:
        node = nodes.get(name)
        if node is None or node.role != "worker" or node.platform != "jetson" or not node.enabled:
            raise ValueError("ENABLED_JETSON_WORKER_REQUIRED")
        endpoint = "endpoint-" + sha(f"{node.host}:{node.api_port}")
        if any(w.endpoint_identity == endpoint for w in result):
            raise ValueError("DUPLICATE_PHYSICAL_ENDPOINT")
        # Inventory is not a live health/runtime/memory observation.
        result.append(WorkerReference(name, endpoint, "jetson"))
    return result


def prepare(block: str, workers: list[WorkerReference], *, models=None, prompt_ids=None,
            category=None, stress=False, persist_prompt=True, response_storage_mode="full",
            public_questions=None) -> dict:
    if public_questions is not None and block != "B2":
        raise ValueError("PUBLIC_DATASET_REQUIRES_B2")
    if stress and block != "B3":
        raise ValueError("STRESS_REQUIRES_B3")
    if block not in BLOCKS:
        raise ValueError("SELECT_ONE_BLOCK")
    if block == "B7-quantization":
        return {"schema_version": 1, "mode": "PREPARE_ONLY", "block": block, "status": "BLOCKED_SAME_BASE_MODEL_SECOND_QUANT_IDENTITY_REQUIRED", "packages": [], "counts": {"cells": 0, "runs": 0, "logical_requests": 0, "physical_requests": 0, "warmup_calls": 0, "model_loads": 0}}
    mapping = model_mapping()
    models = list(models if models is not None else DEFAULT_MODELS.get(block, ["M03", "M05"]))
    if not models or len(set(models)) != len(models) or any(m not in mapping for m in models):
        raise ValueError("UNKNOWN_OR_DUPLICATE_MODEL")
    if any(m.startswith("R") != block.startswith("B6") for m in models):
        raise ValueError("LARGE_MODELS_REQUIRE_B6")
    if ("M07" in models) != (block == "B7-reasoning"):
        raise ValueError("REASONING_REQUIRES_SEPARATE_COHORT")
    if block == "B7-reasoning" and models != ["M07"]:
        raise ValueError("REASONING_COHORT_IS_M07_ONLY")
    if not 1 <= len(workers) <= 4 or len({w.endpoint_identity for w in workers}) != len(workers):
        raise ValueError("INVALID_WORKER_MAPPING")
    if any(w.platform != "jetson" for w in workers):
        raise ValueError("JETSON_ONLY")
    bank = question_bank()
    if block == "B4b":
        bank.update(long_document_variants())
    if block == "B2":
        if not public_questions:
            return {"schema_version": 1, "mode": "PREPARE_ONLY", "block": block,
                    "status": "BLOCKED_LOCAL_DATASET_SUBSET_REQUIRED", "packages": [],
                    "counts": {"cells": 0, "runs": 0, "logical_requests": 0, "physical_requests": 0,
                               "warmup_calls": 0, "model_loads": 0},
                    "planned_subset_sizes": {"calibration_per_dataset": 20, "evaluation_per_dataset": 200}}
        if len({q["prompt_id"] for q in public_questions}) != len(public_questions):
            raise ValueError("DUPLICATE_PUBLIC_QUESTION")
        if any(sha(q["text"]) != q["text_sha256"] or q.get("clinical_use") is not False or not q.get("dataset_ref") for q in public_questions):
            raise ValueError("PUBLIC_QUESTION_PROVENANCE_REQUIRED")
        bank = {q["prompt_id"]: q for q in public_questions}
    defaults = {
        "B0": ["DOC02"], "B1": [k for k, v in bank.items() if v["language"] == "ko"],
        "B2": list(bank), "B3": list(FIXED), "B4a": ["DOC01"],
        "B4b": list(long_document_variants()), "B5": list(FIXED),
        "B6-smoke": ["DOC02"], "B6": ["EDU01", "EYE04", "DOC01"],
        "B7-language": [k for k, v in bank.items() if v.get("paired_prompt_id")] +
                       [v["paired_prompt_id"] for v in bank.values() if v.get("paired_prompt_id")],
        "B7-reasoning": ["EVID04"],
    }
    ids = list(prompt_ids if prompt_ids is not None else defaults[block])
    if not ids or len(set(ids)) != len(ids) or any(p not in bank for p in ids):
        raise ValueError("UNKNOWN_OR_DUPLICATE_PROMPT")
    if category:
        ids = [p for p in ids if bank[p]["category"] == category]
    if not ids:
        raise ValueError("NO_QUESTIONS_SELECTED")
    if block not in {"B7-language", "B2"} and any(bank[p]["language"] != "ko" for p in ids):
        raise ValueError("LANGUAGE_COHORT_MISMATCH")
    worker_ids = [w.worker_id for w in workers]
    if block.startswith("B6") and len(workers) < 2:
        raise ValueError("RPC_REQUIRES_TWO_WORKERS")
    repeats = 3 if block in {"B3", "B4a", "B4b", "B5"} else 2 if block == "B6" else 1
    packages = []
    for model in models:
        conditions, prompts, metadata, profiles = [], [], {}, {}
        for pid in ids:
            q = bank[pid]
            soft = mapping[model].startswith("qwen3-")
            text = compose(q, qwen3_soft_switch=soft)
            prompts.append({"ref": pid, "model_ref": model, "text": text, "mode": "same_text"})
            metadata[pid] = {"prompt_id": pid, "question_group": q.get("paired_prompt_id", pid),
                             "scoring_key": q.get("base_prompt_id", pid), "language": q["language"],
                             "category": q["category"], "dataset_ref": q.get("dataset_ref"), "raw_text_sha256": q["text_sha256"],
                             "prompt_sha256": sha(text), "rendered_prompt_sha256": None,
                             "actual_input_tokens": None, "template_sha256": None,
                             "thinking_control": "soft_no_think_unverified" if soft else "not_applicable",
                             "nominal_target_tokens": q.get("nominal_target_tokens"),
                             "evidence_position": q.get("evidence_position")}
            base = {"model_ref": model, "prompt_ref": pid, "worker_ids": worker_ids[:1],
                    "execution_strategy": "single_node", "n_ctx": 4096, "n_gpu_layers": 30,
                    "n_threads": 6, "n_batch": 256, "concurrency": 1, "requests": 1,
                    "warmup_requests": 1, "max_tokens": q["max_output_tokens"],
                    "temperature": 0, "top_p": 0.9, "seed": 42, "request_timeout_s": 600,
                    "persist_prompt": persist_prompt, "response_storage_mode": response_storage_mode}
            if block in {"B0", "B1"}:
                for worker in worker_ids:
                    conditions.append({**base, "worker_ids": [worker], **({"n_ctx": 2048, "max_tokens": 128} if block == "B0" else {})})
            elif block == "B3":
                for n in range(1, len(workers) + 1):
                    for factor in ([1, 2] if stress else [1]):
                        conditions.append({**base, "worker_ids": worker_ids[:n], "execution_strategy": "replicated_round_robin",
                                           "concurrency": n * factor, "requests": 20, "max_tokens": 256})
            elif block == "B4a":
                conditions.extend({**base, "n_ctx": ctx, "max_tokens": 256} for ctx in [2048, 4096, 8192])
            elif block == "B4b":
                conditions.append({**base, "n_ctx": 8192, "max_tokens": 256})
            elif block == "B5":
                conditions.extend({**base, "max_tokens": limit} for limit in [128, 256, 512])
            elif block.startswith("B6"):
                for n in ([2] if block == "B6-smoke" else [n for n in [2, 4] if n <= len(workers)]):
                    ref = f"rpc-{n}"
                    profiles[ref] = {"profile_id": ref, "worker_ids": worker_ids[:n], "coordinator_id": worker_ids[0],
                                     "split_mode": "layer", "split_policy": "auto", "rpc_gpu_layers": "all"}
                    conditions.append({k: v for k, v in {**base, "worker_ids": worker_ids[:n],
                                       "execution_strategy": "model_parallel_rpc", "rpc_profile_ref": ref,
                                       "n_ctx": 2048, "max_tokens": 128 if block == "B6-smoke" else 256}.items()
                                       if k not in {"n_threads", "n_batch"}})
            elif block == "B7-reasoning":
                conditions.append({**base, "temperature": 0.6, "top_p": 0.95, "max_tokens": 1024})
            else:
                conditions.append(base)
        # Split BEFORE materializing a Sweep. Never relax product budgets.
        size = 500 // repeats
        for offset in range(0, len(conditions), size):
            rows = conditions[offset:offset + size]
            refs = {c["prompt_ref"] for c in rows}
            request = {"spec": {"base": rows[0], "combination": "explicit", "explicit": rows,
                        "rpc_profiles": list(profiles.values()), "repeat_count": repeats,
                        "execution": {"mode": "sequential", "max_parallel_jobs": 1, "cooldown_s": 30,
                                      "order": "seeded_randomized", "order_seed": 20261023}},
                       "model_selections": {model: mapping[model]}, "prompts": [p for p in prompts if p["ref"] in refs]}
            # Validate against exactly the production schemas; no client-supplied runtime proof.
            from cluster.dashboard.schemas import SweepPreviewPayload
            SweepPreviewPayload.model_validate(request)
            spec = SweepSpec.from_dict(request["spec"])
            plan = compile_plan(spec, ResolutionContext((), (), tuple(workers)))
            ident = fingerprint(request)
            packages.append({"id": f"medical-{block}-{model}-{ident[:12]}", "request_sha256": ident,
                             "request": request, "counts": plan.counts.to_dict(),
                             "readiness": "UNKNOWN_REQUIRES_SERVER_CATALOG_AND_WORKER_EVIDENCE",
                             "questions": {p: metadata[p] for p in refs},
                             "model_key": model, "model_id": mapping[model]})
    counts = defaultdict(int)
    for item in packages:
        counts["cells"] += item["counts"]["candidate_cells"]
        counts["runs"] += item["counts"]["trials"]
        for key in ("scenarios", "logical_requests", "physical_requests", "warmup_calls", "model_loads"):
            counts[key] += item["counts"]["workload"][key]
    result = {"schema_version": 1, "mode": "PREPARE_ONLY", "block": block,
              "clinical_review": "clinical_review_pending", "formal_approval": "PENDING",
              "models": models, "workers": worker_ids, "packages": packages, "counts": dict(counts),
              "parallel_exploratory": False, "stress_2N": stress,
              "n_threads_policy": "requested_6_effective_requires_evidence", "kv_cache_policy": "runtime_default_UNKNOWN",
              "model_cache_policy": "reload_per_cell", "rpc_session_policy": "new_session_per_cell",
              "unique_question_groups": len({bank[p].get("paired_prompt_id", p) for p in ids}),
              "diagnostics_mode": "lifecycle", "auto_execute": False}
    result["bundle_sha256"] = fingerprint(result)
    return result


def write_bundle(bundle: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / "manifest.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in bundle["packages"]:
        (output / (item["id"] + ".request.json")).write_text(json.dumps(item["request"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
