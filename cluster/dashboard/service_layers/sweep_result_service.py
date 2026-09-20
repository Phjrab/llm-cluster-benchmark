"""Read-only exploratory sweep result assembly and reproducible exports.

The independent statistical unit is a completed run.  Request records remain
nested evidence and are never promoted to repeats.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Mapping, Sequence

from cluster.research.publication import describe

from .errors import DashboardServiceError
from .result_service import normalize_response_storage


RUN_METRICS = (
    "cluster_tokens_per_s", "effective_user_tokens_per_s", "requests_per_s",
    "ttft_p50_s", "e2e_p50_s", "success_rate", "generated_tokens_per_j",
)
CONFIG_KEYS = ("n_ctx", "max_tokens", "n_gpu_layers", "n_threads", "n_batch")
CSV_COLUMNS = (
    "sweep_id", "cell_id", "trial_id", "repeat_index", "attempt_id", "run_id",
    "status", "representative", "model_id", "model_sha256", "template_sha256",
    "context_capacity", "actual_prompt_tokens", "concurrency", "gpu_offload",
    "execution_strategy", "rpc_profile", "condition_mismatch", "finish_reasons",
    "cluster_tokens_per_s", "effective_user_tokens_per_s", "requests_per_s",
    "ttft_p50_s", "e2e_p50_s", "success_rate", "generated_tokens_per_j",
    "energy_quality", "parallel_label", "overlapping_run_ids", "failure_code",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _effective(summary: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    loaded = summary.get("actual_model_config")
    rows = loaded if isinstance(loaded, list) else [loaded] if isinstance(loaded, Mapping) else []
    for key in CONFIG_KEYS:
        values = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            source = row.get("effective_config") or row.get("factory_config") or row
            if isinstance(source, Mapping) and source.get(key) is not None:
                values.append(source.get(key))
        if values:
            result[key] = values[0] if all(value == values[0] for value in values) else values
    return result


def _metric(summary: Mapping[str, Any], name: str) -> float | None:
    if name == "generated_tokens_per_j":
        instrumentation = summary.get("measurement_instrumentation") or {}
        overall = instrumentation.get("overall") if isinstance(instrumentation, Mapping) else {}
        return _number((overall or {}).get(name))
    return _number(summary.get(name))


def _energy(summary: Mapping[str, Any]) -> dict[str, Any]:
    instrumentation = summary.get("measurement_instrumentation") or {}
    overall = instrumentation.get("overall") if isinstance(instrumentation, Mapping) else {}
    availability = overall.get("availability") if isinstance(overall, Mapping) else {}
    energy_availability = availability.get("energy_j") if isinstance(availability, Mapping) else {}
    coverage = overall.get("energy_coverage") if isinstance(overall, Mapping) else {}
    value = _metric(summary, "generated_tokens_per_j")
    unavailable = list(overall.get("unavailable_node_reasons") or []) if isinstance(overall, Mapping) else []
    incomplete_coverage = (
        isinstance(coverage, Mapping) and coverage.get("complete") is False
    )
    explicitly_unavailable = (
        isinstance(energy_availability, Mapping)
        and energy_availability.get("available") is False
    )
    if incomplete_coverage or explicitly_unavailable:
        value = None
    quality = (
        "partial"
        if unavailable or incomplete_coverage or explicitly_unavailable
        else summary.get("measurement_quality")
        or ("unknown" if value is None else "available")
    )
    return {
        "generated_tokens_per_j": value,
        "quality": quality,
        "available": value is not None and (not isinstance(energy_availability, Mapping) or energy_availability.get("available") is not False),
        "reason": (energy_availability or {}).get("reason") if isinstance(energy_availability, Mapping) else None,
        "unavailable_nodes": unavailable,
        "coverage": dict(coverage) if isinstance(coverage, Mapping) else {},
    }


def _time(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _formula_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + text
    return text


class SweepResultService:
    """Join a sweep manifest to immutable run artifacts without rewriting either."""

    def __init__(self, run_repository: Any, *, seed: int = 20260919, bootstrap_resamples: int = 10_000) -> None:
        self.repository = run_repository
        self.seed = seed
        self.bootstrap_resamples = bootstrap_resamples

    @staticmethod
    def _representative(trial: Mapping[str, Any]) -> str | None:
        attempts = [item for item in trial.get("attempts") or [] if isinstance(item, Mapping)]
        official = str(trial.get("official_attempt_id") or "")
        if official and any(str(item.get("attempt_id")) == official for item in attempts):
            return official
        completed = [item for item in attempts if item.get("status") == "completed" and item.get("run_id")]
        chosen = completed[-1] if completed else attempts[-1] if attempts else None
        return str(chosen.get("attempt_id")) if chosen else None

    def _run(self, run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        try:
            summary = self.repository.read_summary(run_id)
            responses = [normalize_response_storage(row) for row in self.repository.read_responses(run_id)]
            measurements = self.repository.read_measurements(run_id)
        except FileNotFoundError:
            return {}, [], []
        except Exception as exc:
            raise DashboardServiceError(500, "Sweep run evidence is corrupted") from exc
        return dict(summary), responses, measurements

    @staticmethod
    def _request_evidence(responses: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        measured = [row for row in responses if not row.get("warmup")]
        inputs = sorted({int(row["input_tokens"]) for row in measured if type(row.get("input_tokens")) is int})
        outputs = [int(row["generated_tokens"]) for row in measured if type(row.get("generated_tokens")) is int]
        return {
            "request_count": len(measured),
            "actual_input_tokens": inputs,
            "actual_output_tokens": outputs,
            "finish_reasons": sorted({str(row.get("finish_reason")) for row in measured if row.get("finish_reason")}),
            "early_eos_count": sum(row.get("finish_reason") not in (None, "length") for row in measured),
            "input_tokens_exact": bool(measured) and all(row.get("input_tokens_exact") is True for row in measured),
            "output_tokens_exact": bool(measured) and all(row.get("output_tokens_exact") is True for row in measured),
            "input_token_sources": sorted({str(row.get("input_token_source")) for row in measured if row.get("input_token_source")}),
            "prefill_semantics": "proxy unless the measurement source explicitly states direct",
            "rtt_semantics": "unavailable unless a dedicated RTT source is present",
        }

    def assemble(self, manifest: Mapping[str, Any], plan: Any) -> dict[str, Any]:
        cells = {cell.cell_id: cell for cell in plan.cells}
        trials: list[dict[str, Any]] = []
        completed_rows: list[dict[str, Any]] = []
        run_windows: dict[str, tuple[float | None, float | None]] = {}
        for trial in manifest.get("trials") or []:
            cell = cells.get(str(trial.get("cell_id") or ""))
            representative_id = self._representative(trial)
            attempts: list[dict[str, Any]] = []
            for raw in trial.get("attempts") or []:
                attempt = {key: raw.get(key) for key in (
                    "attempt_id", "status", "backend_job_id", "run_id", "failure_code",
                    "cleanup_status", "retry_of_attempt_id", "retry_reason", "started_at", "finished_at",
                )}
                attempt["representative"] = attempt["attempt_id"] == representative_id
                summary: dict[str, Any] = {}
                responses: list[dict[str, Any]] = []
                measurements: list[dict[str, Any]] = []
                if attempt.get("run_id"):
                    summary, responses, measurements = self._run(str(attempt["run_id"]))
                requested = cell.condition.to_dict() if cell else {}
                effective = _effective(summary)
                mismatches = [
                    key for key in CONFIG_KEYS
                    if key in effective and requested.get(key) is not None and effective[key] != requested.get(key)
                ]
                evidence = self._request_evidence(responses)
                metrics = {name: _metric(summary, name) for name in RUN_METRICS}
                attempt.update({
                    "evidence_status": "available" if summary else "unavailable",
                    "summary_status": summary.get("status"),
                    "requested_config": {key: requested.get(key) for key in CONFIG_KEYS if requested.get(key) is not None},
                    "effective_config": effective,
                    "condition_mismatch": bool(mismatches),
                    "mismatch_fields": mismatches,
                    "metrics": metrics,
                    "energy": _energy(summary),
                    "request_evidence": evidence,
                    "responses": responses,
                    "measurements": measurements,
                    "measurement_count": len(measurements),
                    "failure": summary.get("failure"),
                    "warnings": list(summary.get("warnings") or []),
                })
                if summary and attempt["representative"] and attempt.get("status") == "completed":
                    completed_rows.append({"cell_id": trial.get("cell_id"), "attempt": attempt})
                if summary and attempt.get("run_id"):
                    run_windows[str(attempt["run_id"])] = (_time(summary.get("started_at")), _time(summary.get("finished_at")))
                attempts.append(attempt)
            condition = cell.condition.to_dict() if cell else {}
            trials.append({
                "trial_id": trial.get("trial_id"), "cell_id": trial.get("cell_id"),
                "repeat_index": trial.get("sweep_repeat_index"), "status": trial.get("status"),
                "official_attempt_id": trial.get("official_attempt_id"), "representative_attempt_id": representative_id,
                "failure_code": trial.get("failure_code"), "attempts": attempts,
                "condition": condition,
                "model_identity": cell.model.identity() if cell and cell.model else None,
                "prompt_identity": cell.prompt.to_dict() if cell and cell.prompt else None,
                "rpc_profile": cell.rpc_profile.to_dict() if cell and cell.rpc_profile else None,
                "workload": cell.workload.to_dict() if cell else None,
            })

        overlap: dict[str, list[str]] = {run_id: [] for run_id in run_windows}
        ids = sorted(run_windows)
        for index, left in enumerate(ids):
            left_start, left_end = run_windows[left]
            for right in ids[index + 1:]:
                right_start, right_end = run_windows[right]
                if None not in (left_start, left_end, right_start, right_end) and max(left_start, right_start) < min(left_end, right_end):
                    overlap[left].append(right)
                    overlap[right].append(left)
        parallel = manifest.get("scheduling", {}).get("mode") == "disjoint_parallel"
        for trial in trials:
            for attempt in trial["attempts"]:
                attempt["parallel_context"] = {
                    "label": "parallel exploratory" if parallel else "sequential exploratory",
                    "overlapping_run_ids": overlap.get(str(attempt.get("run_id")), []),
                    "isolation": "shared controller/network/storage are not isolated" if parallel else "no parallel isolation claim",
                }

        aggregates = []
        for cell_id in sorted(cells):
            samples = [row["attempt"] for row in completed_rows if row["cell_id"] == cell_id]
            metrics = {}
            for name in RUN_METRICS:
                values = [item["metrics"][name] for item in samples if item["metrics"][name] is not None]
                stats = describe(values, seed=self.seed, bootstrap_resamples=self.bootstrap_resamples)
                if len(values) < 2:
                    stats["ci95"] = [None, None]
                    stats["ci_status"] = "unavailable_single_repeat" if values else "unavailable_no_samples"
                else:
                    stats["ci_status"] = "available_run_level"
                metrics[name] = stats
            aggregates.append({"cell_id": cell_id, "independent_run_count": len(samples), "metrics": metrics})

        tokenizer_ids = sorted({
            cell.model.tokenizer_sha256 or cell.model.template_sha256
            for cell in cells.values() if cell.model
        })
        strategies = sorted({cell.condition.execution_strategy for cell in cells.values()})
        warnings = []
        if len(tokenizer_ids) > 1:
            warnings.append("Cross-model token throughput uses different tokenizer/template identities.")
        if len(strategies) > 1 or any(name in strategies for name in ("broadcast_compare", "model_parallel_rpc")):
            warnings.append("RPC, replicated, and broadcast throughput have different request/token meanings.")
        return {
            "schema_version": 1, "artifact_type": "exploratory_sweep_results",
            "sweep_id": manifest.get("sweep_id"), "plan_sha256": manifest.get("plan_sha256"),
            "status": manifest.get("status"), "coverage": manifest.get("coverage"),
            "selection_policy": "official attempt; otherwise latest completed; otherwise latest attempt",
            "statistical_unit": "independent completed run per repeat",
            "analysis": {"method": "existing percentile bootstrap mean CI", "seed": self.seed, "bootstrap_resamples": self.bootstrap_resamples, "formal_comparison_eligible": False},
            "baseline_policy": "only exact actual runs with the same model artifact and all non-selected conditions; no synthetic RPC baseline",
            "cache_policy": plan.spec.execution.model_cache_policy,
            "axis_contract": {"available": ["context_capacity", "actual_prompt_tokens", "concurrency", "model", "gpu_offload", "rpc_profile"], "fixed_condition_validation_required": True},
            "comparison_warnings": warnings,
            "trials": trials, "aggregates": aggregates,
        }

    def export_json(self, results: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1, "artifact_type": "exploratory_sweep_result_export",
            "formal_approved": False,
            "result_index": {
                "sweep_id": results.get("sweep_id"), "plan_sha256": results.get("plan_sha256"),
                "selection_policy": results.get("selection_policy"), "statistical_unit": results.get("statistical_unit"),
                "run_ids": sorted({str(a.get("run_id")) for t in results.get("trials", []) for a in t.get("attempts", []) if a.get("run_id")}),
            },
            "plan": plan, "results": results,
        }

    def export_csv(self, results: Mapping[str, Any]) -> str:
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for trial in results.get("trials") or []:
            condition = trial.get("condition") or {}
            model = trial.get("model_identity") or {}
            prompt = trial.get("prompt_identity") or {}
            for attempt in trial.get("attempts") or [{}]:
                evidence = attempt.get("request_evidence") or {}
                parallel = attempt.get("parallel_context") or {}
                metrics = attempt.get("metrics") or {}
                row = {
                    "sweep_id": results.get("sweep_id"), "cell_id": trial.get("cell_id"),
                    "trial_id": trial.get("trial_id"), "repeat_index": trial.get("repeat_index"),
                    "attempt_id": attempt.get("attempt_id"), "run_id": attempt.get("run_id"),
                    "status": attempt.get("status") or trial.get("status"), "representative": attempt.get("representative", False),
                    "model_id": model.get("model_id"), "model_sha256": model.get("artifact_sha256"),
                    "template_sha256": prompt.get("template_sha256"), "context_capacity": condition.get("n_ctx"),
                    "actual_prompt_tokens": "|".join(map(str, evidence.get("actual_input_tokens") or [])),
                    "concurrency": condition.get("concurrency"), "gpu_offload": condition.get("n_gpu_layers"),
                    "execution_strategy": condition.get("execution_strategy"),
                    "rpc_profile": (trial.get("rpc_profile") or {}).get("profile_id"),
                    "condition_mismatch": attempt.get("condition_mismatch", False),
                    "finish_reasons": "|".join(evidence.get("finish_reasons") or []),
                    **{name: metrics.get(name) for name in RUN_METRICS},
                    "energy_quality": (attempt.get("energy") or {}).get("quality"),
                    "parallel_label": parallel.get("label"),
                    "overlapping_run_ids": "|".join(parallel.get("overlapping_run_ids") or []),
                    "failure_code": attempt.get("failure_code") or trial.get("failure_code"),
                }
                writer.writerow({key: _formula_safe(value) for key, value in row.items()})
        return buffer.getvalue()


__all__ = ["CSV_COLUMNS", "RUN_METRICS", "SweepResultService"]
