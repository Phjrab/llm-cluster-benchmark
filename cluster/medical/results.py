"""Offline, additive medical index. Never changes a run or computes new energy."""
from __future__ import annotations

import json
import random
import types
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from cluster.application.sweep_planner import verify_plan
from cluster.domain.sweep import SweepSpec, fingerprint
from cluster.dashboard.service_layers.diagnostic_views import run_diagnostics
from .evaluator import evaluate, rubrics, DIMENSIONS, final_text
from cluster.domain.runtime_profile import require_applied_profile
from cluster.infrastructure.request_diagnostics import clean_resources

METRICS = ("success_rate", "ttft_p50_s", "ttft_p95_s", "e2e_p50_s", "e2e_p95_s",
           "cluster_tokens_per_s", "logical_requests_per_s", "physical_requests_per_s", "wall_s")
NODE_METRICS = ("mean_temperature_c", "peak_temperature_c", "power_provider", "power_mode", "jetson_clocks",
                "bytes_sent", "bytes_received", "effective_bandwidth_bytes_s", "energy_coverage", "energy_j")


def read_json(path: Path, limit=32 * 1024 * 1024):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("ARTIFACT_SIZE_LIMIT")
    return json.loads(raw)


def verify_bundle(bundle):
    value = dict(bundle)
    given = value.pop("bundle_sha256", None)
    if given != fingerprint(value) or bundle.get("mode") != "PREPARE_ONLY":
        raise ValueError("BUNDLE_IDENTITY_MISMATCH")
    for package in bundle["packages"]:
        if package["request_sha256"] != fingerprint(package["request"]):
            raise ValueError("PACKAGE_IDENTITY_MISMATCH")


def bind_plan(package, raw_plan):
    plan = verify_plan(json.dumps(raw_plan))
    if plan.spec != SweepSpec.from_dict(package["request"]["spec"]):
        raise ValueError("PRESET_PLAN_SPEC_MISMATCH")
    for cell in plan.cells:
        q = package["questions"][cell.condition.prompt_ref]
        if (cell.model is None or cell.prompt is None or cell.model.model_id != package["model_id"]
                or cell.prompt.text_sha256 != q["prompt_sha256"]):
            raise ValueError("PLAN_MODEL_PROMPT_MISMATCH")
    return plan


def index_runs(bundle, package_id, raw_plan, run_dirs, *, extra_keys=None):
    verify_bundle(bundle)
    package = next(p for p in bundle["packages"] if p["id"] == package_id)
    plan = bind_plan(package, raw_plan)
    keys = rubrics()
    keys.update(extra_keys or {})
    output = {"schema_version": 1, "block": bundle["block"], "package_id": package_id,
              "plan_sha256": plan.plan_sha256, "status": "NOT_RUN" if not run_dirs else "INDEXED",
              "clinical_review": "clinical_review_pending", "formal_approval": "PENDING",
              "planned_trials": len(plan.trials), "runs": [], "requests": [],
              "quality_unit": "prompt_id / paired question group; replicas and repeats are correlated",
              "performance_unit": "independent job, sequential unless explicitly labeled",
              "rpc_speedup": None, "rpc_speedup_reason": "no validated single-node baseline joined"}
    trials = {t.trial_id: t for t in plan.trials}
    cells = {c.cell_id: c for c in plan.cells}
    seen_attempts, seen_runs = set(), set()
    for directory in map(Path, run_dirs):
        config = read_json(directory / "config.json")
        summary = read_json(directory / "summary.json") if (directory / "summary.json").exists() else {}
        run_id = directory.name
        trace = config.get("sweep") or {}
        trial = trials.get(trace.get("trial_id"))
        if (trial is None or trace.get("plan_sha256") != plan.plan_sha256
                or trace.get("cell_id") != trial.cell_id or not trace.get("attempt_id")
                or not trace.get("sweep_id") or summary.get("run_id", run_id) != run_id
                or summary.get("config_fingerprint_sha256", config.get("config_fingerprint_sha256")) != config.get("config_fingerprint_sha256")):
            raise ValueError("RUN_PLAN_IDENTITY_MISMATCH")
        if run_id in seen_runs or trace["attempt_id"] in seen_attempts:
            raise ValueError("DUPLICATE_RUN_OR_ATTEMPT")
        seen_runs.add(run_id); seen_attempts.add(trace["attempt_id"])
        cell = cells[trial.cell_id]
        q = package["questions"][cell.condition.prompt_ref]
        if (trace.get("model_sha256") != cell.model.artifact_sha256 or config.get("model_id") != cell.model.model_id
                or trace.get("prompt_sha256") != q["prompt_sha256"]
                or trace.get("template_sha256") != cell.prompt.template_sha256
                or trace.get("sweep_repeat_index") != trial.sweep_repeat_index
                or config.get("node_names") != list(cell.condition.worker_ids)):
            raise ValueError("RUN_MODEL_PROMPT_NODE_MISMATCH")
        for k, v in cell.condition.to_dict().items():
            if k not in {"model_ref", "prompt_ref", "rpc_profile_ref", "worker_ids"} and config.get(k) != v:
                raise ValueError("RUN_REQUESTED_CONDITION_MISMATCH")
        load_issues = []
        actual_loads = summary.get("actual_model_config") or []
        for worker_id in cell.condition.worker_ids:
            observed = next((x for x in actual_loads if x.get("node") == worker_id), None)
            if observed is None:
                load_issues.append("load_profile_missing:" + worker_id)
                continue
            try:
                require_applied_profile(observed, types.SimpleNamespace(**config))
            except ValueError:
                load_issues.append("load_profile_unverified_or_mismatch:" + worker_id)
        records = []
        path = directory / "responses.jsonl"
        if path.exists():
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("RESPONSES_SIZE_LIMIT")
            with path.open(encoding="utf-8") as stream:
                records = [json.loads(line) for line in stream if line.strip()]
        physical = set()
        run_rows = []
        for row in records:
            identity = row.get("diagnostic_identity")
            if row.get("sweep") != trace or row.get("model_id") != config["model_id"] or row.get("node") not in cell.condition.worker_ids:
                raise ValueError("REQUEST_RUN_IDENTITY_MISMATCH")
            pk = tuple(row.get(k) for k in ("scenario_id", "request_id", "logical_request_id", "node", "replica_index"))
            if pk in physical:
                raise ValueError("DUPLICATE_PHYSICAL_REQUEST")
            physical.add(pk)
            if identity and (any(identity.get(k) != row.get(k) for k in ("scenario_id", "request_id", "logical_request_id", "node", "replica_index"))
                             or any(identity.get(k) != trace.get(k) for k in ("attempt_id", "trial_id", "cell_id", "sweep_id"))
                             or identity.get("run_id") != run_id or identity.get("warmup") is not False):
                raise ValueError("DIAGNOSTIC_REQUEST_IDENTITY_MISMATCH")
            mismatch = []
            unknown = list(load_issues)
            for name, wanted in (("n_ctx", cell.condition.n_ctx), ("max_tokens", cell.condition.max_tokens)):
                value = row.get("effective_" + name)
                if value is None: unknown.append("effective_" + name)
                elif value != wanted: mismatch.append("effective_" + name)
            for name, wanted in (("prompt_sha256", q["prompt_sha256"]), ("model_sha256", cell.model.artifact_sha256), ("template_hash", cell.prompt.template_sha256)):
                value = row.get(name)
                if value is None: unknown.append(name)
                elif value != wanted: raise ValueError("REQUEST_ARTIFACT_IDENTITY_MISMATCH")
            if not row.get("rendered_prompt_sha256") or row.get("input_tokens_exact") is not True:
                unknown.append("rendered_input_evidence")
            if q["thinking_control"] == "soft_no_think_unverified":
                unknown.append("effective_non_thinking")
            key = keys[q["scoring_key"]]
            if q.get("dataset_ref") and key.get("reference_text_sha256") != q["raw_text_sha256"]:
                raise ValueError("DATASET_KEY_INPUT_MISMATCH")
            score = evaluate(key, row)
            item = {"run_id": run_id, "sweep_id": trace["sweep_id"], "trial_id": trial.trial_id,
                    "attempt_id": trace["attempt_id"], "cell_id": cell.cell_id, "model_id": config["model_id"],
                    **{k: row.get(k) for k in ("request_id", "logical_request_id", "scenario_id", "node", "replica_index", "ok", "error_code",
                        "ttft_s", "e2e_s", "generated_tokens", "token_count_source", "output_tokens_exact", "finish_reason", "input_tokens", "input_tokens_exact", "rendered_prompt_sha256", "rendered_prompt_hash_source", "template_hash")},
                    **q, "rendered_prompt_sha256": row.get("rendered_prompt_sha256"),
                    "actual_input_tokens": row.get("input_tokens") if row.get("input_tokens_exact") is True else None,
                    "template_sha256": row.get("template_hash"), "output_budget": cell.condition.max_tokens,
                    "truncated": True if row.get("finish_reason") == "length" else False if row.get("finish_reason") == "stop" else None,
                    "final_answer_present": bool(final_text(row.get("response", ""))[0]) if row.get("response_storage_mode") == "full" else None,
                    "condition_mismatch": mismatch, "unknown_evidence": unknown,
                    "controlled_sample": not mismatch and not unknown, "evaluation": score,
                    "response_storage_mode": row.get("response_storage_mode"), "persist_prompt": config.get("persist_prompt", True)}
            # This index is local/private. Never recover hidden prompt/answer text from the bank.
            if row.get("response_storage_mode") == "full" and "response" in row:
                item["response"] = row["response"]
            if config.get("persist_prompt", True) and "prompt" in row:
                item["prompt"] = row["prompt"]
            run_rows.append(item)
        if len(records) > cell.workload.physical_requests:
            raise ValueError("UNEXPECTED_PHYSICAL_REQUEST_COUNT")
        instrumentation = summary.get("measurement_instrumentation") or {}
        diagnostics = run_diagnostics(directory)
        run = {"run_id": run_id, "trial_id": trial.trial_id, "attempt_id": trace["attempt_id"], "cell_id": cell.cell_id,
               "model": cell.model.to_dict(), "runtime_commits": sorted({w.runtime_commit for w in cell.workers if w.runtime_commit}),
               "condition": cell.condition.to_dict(), "rpc_profile": cell.rpc_profile.to_dict() if cell.rpc_profile else None,
               "status": summary.get("status", "UNKNOWN_INCOMPLETE"), "error_code": summary.get("error_code"),
               "observed_requests": len(records), "expected_requests": cell.workload.physical_requests,
               "unobserved_requests": cell.workload.physical_requests - len(records),
               "metrics": {k: summary.get(k) for k in METRICS},
               "controlled_sample": bool(run_rows) and all(r["controlled_sample"] for r in run_rows),
               "energy": instrumentation.get("overall"),
               "nodes": {name: {k: values.get(k) for k in NODE_METRICS} for name, values in instrumentation.get("nodes", {}).items()},
               "resource_observations": resource_samples(directory, run_id, cell.condition.worker_ids),
               "load_profile_issues": load_issues,
               "diagnostics": diagnostics, "artifact_directory": str(directory.resolve())}
        output["runs"].append(run); output["requests"].extend(run_rows)
    output["remaining_trials"] = len(set(trials) - {r["trial_id"] for r in output["runs"]})
    output["comparison_groups"] = comparison_groups(output)
    return output


def report(index):
    lines = ["# Medical text benchmark — offline comparison", "", f"Status: {index['status']}. Clinical review PENDING; formal approval PENDING.", "",
             "No clinical safety/quality ranking is inferred. Missing values are N/A. Original failures and attempts remain separate.", "",
             "|Run|Model / quant|Nodes|Context|Output cap|Status|Observed / expected|TTFT p50|E2E p50|Tokens/s|Success|", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for run in index["runs"]:
        c, m = run["condition"], run["metrics"]
        show = lambda v: "N/A" if v is None else str(v)
        lines.append("|" + "|".join(map(str, [run["run_id"], f"{run['model']['model_id']} / {run['model']['quantization']}",
            ",".join(c["worker_ids"]), c["n_ctx"], c["max_tokens"], run["status"], f"{run['observed_requests']}/{run['expected_requests']}",
            show(m["ttft_p50_s"]), show(m["e2e_p50_s"]), show(m["cluster_tokens_per_s"]), show(m["success_rate"])])) + "|")
    lines += ["", "## Quality observations", "", "Denominator includes failed/missing requests; privacy-unscorable and human-review items have no automated accuracy."]
    for run in index["runs"]:
        rows = [r for r in index["requests"] if r["run_id"] == run["run_id"]]
        automatic = rows and all(r["evaluation"]["kind"] in {"structured_exact", "numeric_exact", "mcqa"} for r in rows)
        score = (sum(r["evaluation"]["correct"] is True for r in rows) / run["expected_requests"]
                 if automatic and all(r["evaluation"]["status"] != "unscorable_privacy" for r in rows) else None)
        lines += [f"- {run['run_id']}: request-level draft accuracy {score if score is not None else 'N/A'}; {dict(Counter(r['evaluation']['status'] for r in rows))}; missing={run['unobserved_requests']}."]
    lines += ["", "## Comparable independent jobs", "", "These groups retain prompt, sampling, runtime, node set and context. Unverified settings are not pooled.", ""]
    for group in index.get("comparison_groups", []):
        lines.append(f"- {group['model_id']} / {group['workers']} / {group['prompt_ref']}: jobs={group['independent_jobs']}, controlled={group['controlled_jobs']}, median throughput={group['cluster_tokens_per_s_median']}, draft objective accuracy={group['objective_accuracy']}, memory/temperature remain per-run observations.")
    lines += ["", "## Comparison limits", "", "Compare identical prompt/model artifact/quantization/template/runtime/sampling conditions. Each run is the performance replicate. Node repetitions and translated pairs do not add independent medical questions.",
              "Context capacity and actual template input tokens are separate index fields. Unknown or mismatched effective settings are excluded from controlled comparison. Qwen3 soft non-thinking needs runtime evidence; max_tokens is only a ceiling.",
              "Per-run memory/process lifecycle evidence, temperatures, network, cleanup and existing energy coverage are in index.json. No power estimate is synthesized. Missing job ownership evidence leaves cleanup UNKNOWN.",
              "RPC speedup versus single-node: N/A until a valid single-node baseline exists. This report does not infer feasibility from summed nominal RAM.",
              "No confidence interval or model ranking is justified by a single job/question. Failures, capped output, parsing and privacy gaps must be reviewed before grouping.", ""]
    return "\n".join(lines)


def blinded_export(index, *, seed=42):
    eligible = [r for r in index["requests"] if r.get("response_storage_mode") == "full" and "response" in r]
    random.Random(seed).shuffle(eligible)
    reviews, mapping = [], []
    for n, row in enumerate(eligible, 1):
        ident = f"review-{n:06d}"
        reviews.append({"review_id": ident, "prompt": row.get("prompt"), "response": row["response"],
                        "scores_0_to_2": {k: None for k in DIMENSIONS}, "serious_safety_error": None,
                        "refusal": None, "reviewer_id": None, "clinical_review": "clinical_review_pending"})
        mapping.append({"review_id": ident, **{k: row[k] for k in ("run_id", "trial_id", "attempt_id", "request_id", "node", "model_id", "prompt_id", "question_group")}})
    return reviews, mapping


def write_index(index, output: Path):
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "REPORT.md").write_text(report(index), encoding="utf-8")
    reviews, mapping = blinded_export(index)
    (output / "blinded-review.json").write_text(json.dumps(reviews, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "evaluator-only").mkdir(mode=0o700)
    (output / "evaluator-only/review-map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resource_samples(directory, run_id, worker_ids):
    """Existing measurement samples only; preserve sample timestamps and gaps."""
    path = directory / "measurements.jsonl"
    output = []
    if not path.exists():
        return {"status": "UNKNOWN", "samples": []}
    if path.stat().st_size > 64 * 1024 * 1024:
        return {"status": "SIZE_LIMIT", "samples": []}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("record_type") != "telemetry_sample" or row.get("sample_kind") != "measurement":
                continue
            if row.get("run_id") != run_id or row.get("node") not in worker_ids:
                raise ValueError("RESOURCE_RUN_IDENTITY_MISMATCH")
            output.append({"node": row["node"], "sampled_at": row.get("sampled_at"),
                           "resources": clean_resources(row.get("resource_diagnostics"))})
            if len(output) >= 4096:
                return {"status": "BOUNDED_FIRST_4096", "samples": output}
    return {"status": "RECORDED" if output else "UNKNOWN", "samples": output}


def comparison_groups(index):
    groups = defaultdict(list)
    for run in index["runs"]:
        key = fingerprint({"model": run["model"], "runtime": run["runtime_commits"], "condition": run["condition"]})
        groups[key].append(run)
    result = []
    for key, runs in groups.items():
        valid = [r for r in runs if r["controlled_sample"]]
        throughput = [r["metrics"]["cluster_tokens_per_s"] for r in valid if r["metrics"].get("cluster_tokens_per_s") is not None]
        rows = [q for q in index["requests"] if q["run_id"] in {r["run_id"] for r in runs}]
        automatic = rows and all(q["evaluation"]["kind"] in {"mcqa", "numeric_exact", "structured_exact"} and q["evaluation"]["status"] != "unscorable_privacy" for q in rows)
        total = sum(r["expected_requests"] for r in runs)
        result.append({"group_sha256": key, "model_id": runs[0]["model"]["model_id"],
                       "workers": runs[0]["condition"]["worker_ids"], "prompt_ref": runs[0]["condition"]["prompt_ref"],
                       "independent_jobs": len(runs), "controlled_jobs": len(valid),
                       "cluster_tokens_per_s_median": statistics.median(throughput) if throughput else None,
                       "objective_accuracy": sum(q["evaluation"]["correct"] is True for q in rows) / total if automatic and total else None,
                       "unique_question_groups": len({q["question_group"] for q in rows}),
                       "expected_request_denominator": total, "performance_ci": None})
    return result


def combine_indexes(indexes):
    """Explicit offline join for cross-model/node reports, never rescore source runs."""
    if not indexes:
        raise ValueError("INDEX_REQUIRED")
    result = {**indexes[0], "package_id": "combined", "block": "explicit_selection", "runs": [], "requests": []}
    seen = set()
    for index in indexes:
        if index.get("schema_version") != 1 or index.get("clinical_review") != "clinical_review_pending":
            raise ValueError("INDEX_SCHEMA_MISMATCH")
        for run in index["runs"]:
            if run["run_id"] in seen:
                raise ValueError("DUPLICATE_RUN")
            seen.add(run["run_id"])
        result["runs"].extend(index["runs"]); result["requests"].extend(index["requests"])
    result["plan_sha256"] = None
    result["source_packages"] = [i["package_id"] for i in indexes]
    result["planned_trials"] = sum(i["planned_trials"] for i in indexes)
    result["remaining_trials"] = sum(i["remaining_trials"] for i in indexes)
    result["status"] = "INDEXED" if result["runs"] else "NOT_RUN"
    result["comparison_groups"] = comparison_groups(result)
    return result
