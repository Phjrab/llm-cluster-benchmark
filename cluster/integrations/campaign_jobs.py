"""Adapter between the research campaign lifecycle and durable job service."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping

from cluster.application.jobs import JobService
from cluster.benchmark.planner import strategy_work_units
from cluster.domain.experiment import ExperimentConfig


JobDocumentFactory = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CampaignJobDocumentFactory:
    """Translate one frozen campaign cell into the existing durable job schema."""

    def __init__(
        self,
        *,
        campaign_id: str,
        matrix: Mapping[str, Any],
        model_lock: Mapping[str, Any],
        prompt_lock: Mapping[str, Any],
        runtime_lock: Mapping[str, Any],
        experiment_conditions: Mapping[str, Any],
        model_ids: Mapping[str, str],
    ) -> None:
        self.campaign_id = campaign_id
        self.matrix = matrix
        self.model_lock = model_lock
        self.prompt_lock = prompt_lock
        self.runtime_lock = runtime_lock
        self.experiment_conditions = experiment_conditions
        self.model_ids = model_ids

    def __call__(
        self,
        cell: Mapping[str, Any],
        attempt: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        model_key = str(cell.get("model_lock_key") or "")
        model_id = self.model_ids.get(model_key)
        if not model_id:
            raise ValueError(f"No exact Worker model id is mapped for {model_key}")
        locked_model = next(
            (
                item
                for item in self.model_lock.get("models", [])
                if isinstance(item, Mapping) and item.get("model_key") == model_key
            ),
            None,
        )
        if not isinstance(locked_model, Mapping) or (
            locked_model.get("verification") or {}
        ).get("status") != "approved":
            raise ValueError(f"Campaign model is not approved: {model_key}")
        locked_filename = str((locked_model.get("binary") or {}).get("filename") or "")
        if PurePosixPath(model_id).name != locked_filename:
            raise ValueError(
                f"Worker model id does not match locked GGUF filename for {model_key}"
            )
        prompts = {
            str(item.get("prompt_id")): str(item.get("text") or "")
            for item in self.prompt_lock.get("prompts", [])
        }
        prompt_id = str(cell.get("prompt_id") or "")
        prompt = prompts.get(prompt_id)
        if not prompt:
            raise ValueError(f"Prompt lock entry is missing: {prompt_id}")
        profiles = self.matrix.get("parameter_profiles") or {}
        profile = profiles.get(cell.get("parameter_profile")) or {}
        platform_profile_id = profile.get("platform_profile")
        platform_profile = next(
            (
                value
                for value in (self.experiment_conditions.get("platform_profiles") or {}).values()
                if isinstance(value, Mapping) and value.get("profile_id") == platform_profile_id
            ),
            None,
        )
        if not isinstance(platform_profile, Mapping):
            raise ValueError(f"Unknown locked platform profile: {platform_profile_id}")
        fixed = self.experiment_conditions.get("fixed_profile") or {}
        benchmark = self.experiment_conditions.get("benchmark_profile") or {}
        digest = hashlib.sha256(
            f"{self.campaign_id}\0{cell.get('campaign_cell_id')}\0{attempt.get('attempt_id')}".encode(
                "utf-8"
            )
        ).hexdigest()[:20]
        suite_id = f"suite_campaign_{digest}"
        job_id = f"job_campaign_{digest}"
        config = ExperimentConfig(
            experiment_id=self.campaign_id.replace(".", "-")[:80],
            name=f"formal campaign {self.campaign_id}",
            node_names=[str(item) for item in cell.get("node_set") or []],
            model_id=model_id,
            n_ctx=int(fixed["n_ctx"]),
            n_gpu_layers=int(platform_profile["n_gpu_layers"]),
            requests=int(benchmark["requests_per_scenario"]),
            concurrency=int(benchmark["logical_concurrency"]),
            max_tokens=int(fixed["max_tokens"]),
            temperature=float(fixed["temperature"]),
            top_p=float(fixed["top_p"]),
            seed=int(fixed["seed"]),
            warmup_requests=int(benchmark["warmup_requests_per_node"]),
            prompt=prompt,
            persist_prompt=bool(benchmark["persist_prompt"]),
            require_uniform_config=bool(benchmark["require_uniform_config"]),
            request_timeout_s=float(fixed["request_timeout_s"]),
            execution_strategy=str(cell["strategy"]),
            suite_id=suite_id,
            model_index=1,
            model_count=1,
            experiment_type="formal",
            campaign_id=self.campaign_id,
            campaign_cell_id=str(cell["campaign_cell_id"]),
            campaign_attempt_id=str(attempt["attempt_id"]),
            repeat_index=int(cell["repeat_index"]),
            order_index=int(cell["order_index"]),
            experiment_lock_id=str(self.experiment_conditions["lock_id"]),
            experiment_lock_sha256=str(self.experiment_conditions["lock_sha256"]),
            model_lock_entry=model_key,
            prompt_set_version=int(self.prompt_lock["prompt_set_version"]),
            runtime_lock_version=int(self.runtime_lock["lock_version"]),
            condition_profile_id=str(fixed["profile_id"]),
            measurement_quality_policy=str(cell["measurement_quality_policy"]),
        )
        config.validate()
        work_units = strategy_work_units(config, len(config.node_names))
        started_at = _utc_now()
        return {
            "schema_version": 1,
            "artifact_type": "experiment_job",
            "id": suite_id,
            "job_id": job_id,
            "suite_id": suite_id,
            "experiment_id": config.experiment_id,
            "name": config.name,
            "status": "queued",
            "phase": "queued",
            "completed": 0,
            "total": work_units,
            "model_completed": 0,
            "model_total": work_units,
            "strategy": str(config.execution_strategy),
            "started_at": started_at,
            "nodes": list(config.node_names),
            "model_ids": [model_id],
            "current_model": model_id,
            "model_index": 0,
            "model_count": 1,
            "completed_models": 0,
            "summaries": [],
            "errors": [],
            "latest": None,
            "error": "",
            "continue_on_model_error": False,
            "model_cooldown_s": 0.0,
            "config": asdict(config),
            "cancel_requested": False,
            "created_at": started_at,
            "updated_at": started_at,
            "campaign_id": self.campaign_id,
            "campaign_cell_id": cell["campaign_cell_id"],
            "campaign_attempt_id": attempt["attempt_id"],
        }


def _public_result(job: Mapping[str, Any]) -> dict[str, Any]:
    raw_status = str(job.get("status") or "failed")
    status = "failed" if raw_status == "orphaned" else raw_status
    summary = job.get("summary") if isinstance(job.get("summary"), Mapping) else {}
    summaries = summary.get("summaries") if isinstance(summary, Mapping) else []
    latest = summaries[-1] if isinstance(summaries, list) and summaries else {}
    models = summary.get("models") if isinstance(summary, Mapping) else []
    cleanup_failed = any(
        isinstance(model, Mapping) and model.get("cleanup_status") == "failed"
        for model in (models if isinstance(models, list) else [])
    )
    return {
        "status": status,
        "backend_job_id": job.get("job_id"),
        "run_id": job.get("current_run_id") or (
            latest.get("run_id") if isinstance(latest, Mapping) else None
        ),
        "finished_at": job.get("finished_at"),
        "cleanup_status": "failed" if cleanup_failed else "completed",
        "measurement_quality": (
            summary.get("measurement_quality") if isinstance(summary, Mapping) else None
        ),
        "failure_code": (
            "DURABLE_JOB_ORPHANED"
            if raw_status == "orphaned"
            else "DURABLE_JOB_FAILED"
            if status == "failed"
            else None
        ),
    }


class DurableJobRunBackend:
    """Run one campaign cell through the existing independent child job path."""

    def __init__(self, service: JobService, factory: JobDocumentFactory) -> None:
        self.service = service
        self.factory = factory

    def start(
        self,
        cell: Mapping[str, Any],
        attempt: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        job = self.service.start(dict(self.factory(cell, attempt)))
        result = _public_result(job)
        if result["status"] == "queued":
            result["status"] = "queued"
        return result

    def inspect(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        job_id = str(attempt.get("backend_job_id") or "")
        if not job_id:
            raise ValueError("campaign attempt has no durable job id")
        self.service.recover()
        return _public_result(self.service.repository.read(job_id))

    def cancel(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]:
        job_id = str(attempt.get("backend_job_id") or "")
        active = self.service.active()
        if not active or active.get("job_id") != job_id:
            return {"status": "cancelled", "backend_job_id": job_id}
        return _public_result(self.service.cancel())


__all__ = [
    "CampaignJobDocumentFactory",
    "DurableJobRunBackend",
    "JobDocumentFactory",
]
