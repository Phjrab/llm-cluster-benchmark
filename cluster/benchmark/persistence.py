"""Benchmark run persistence and progress event boundary."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from cluster.domain.experiment import ExperimentConfig, config_fingerprint
from cluster.domain.events import EventChannel
from cluster.infrastructure.storage import FilesystemRunRepository

from .transport import utc_now
from .instrumentation import request_measurement

ProgressCallback = Callable[[Dict[str, Any]], None]


class RunPersistence:
    def __init__(
        self,
        results_root: Path,
        run_id: str,
        config: ExperimentConfig,
        progress: Optional[ProgressCallback] = None,
    ) -> None:
        self.run_id = run_id
        self.config = config
        self.progress = progress
        self.repository = FilesystemRunRepository(results_root)
        persisted_config = asdict(config)
        persisted_config["config_fingerprint_sha256"] = config_fingerprint(config)
        if not config.persist_prompt:
            persisted_config.pop("prompt", None)
            persisted_config["prompt_sha256"] = hashlib.sha256(
                config.prompt.encode("utf-8")
            ).hexdigest()
            persisted_config["prompt_chars"] = len(config.prompt)
        self.run_dir = self.repository.create(run_id, persisted_config)

    def emit(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        if event_type == "request_completed" and isinstance(payload.get("result"), Mapping):
            raw_result = payload["result"]
            self.repository.append_response(
                self.run_id,
                self._response_record(raw_result),
            )
            self.repository.append_measurement(
                self.run_id,
                request_measurement(self.run_id, raw_result),
            )
            payload = {
                **payload,
                "result": self._event_result(raw_result),
            }
        payload = self.safe_artifact(payload)
        event = {
            "type": event_type,
            "at": utc_now(),
            "channel": EventChannel.EXPERIMENT.value,
            "run_id": self.run_id,
            "suite_id": self.config.suite_id,
            "experiment_id": self.config.experiment_id,
            "model_id": self.config.model_id,
            "model_index": self.config.model_index,
            "model_count": self.config.model_count,
            **payload,
        }
        if self.config.campaign_id:
            event.update(
                {
                    "campaign_id": self.config.campaign_id,
                    "campaign_cell_id": self.config.campaign_cell_id,
                    "campaign_attempt_id": self.config.campaign_attempt_id,
                    "repeat_index": self.config.repeat_index,
                    "order_index": self.config.order_index,
                }
            )
        elif self.config.pilot_id:
            event.update(
                {
                    "pilot_id": self.config.pilot_id,
                    "pilot_cell_id": self.config.pilot_cell_id,
                    "pilot_repeat_index": self.config.pilot_repeat_index,
                    "pilot_order_index": self.config.pilot_order_index,
                }
            )
        self.repository.append_event(self.run_id, event)
        if self.progress:
            self.progress(event)
        return event

    def _response_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Apply the configured response policy to one durable request record."""
        response = str(record.get("response") or "")
        prompt_sha256 = hashlib.sha256(self.config.prompt.encode("utf-8")).hexdigest()
        output_sha256 = str(record.get("output_sha256") or "")
        if "response" in record:
            output_sha256 = hashlib.sha256(response.encode("utf-8")).hexdigest()
        mode = self.config.response_storage_mode
        storage_status = {
            "full": "stored",
            "hash_only": "hash_only",
            "none": "not_persisted",
        }[mode]
        value: Dict[str, Any] = {
            "schema_version": 2,
            "request_id": record.get("request_id"),
            "logical_request_id": record.get("logical_request_id"),
            "scenario_id": record.get("scenario_id"),
            "replica_index": record.get("replica_index"),
            "model_id": self.config.model_id,
            "node": record.get("node"),
            "assigned_node": record.get("assigned_node"),
            "started_at": record.get("started_at"),
            "ok": bool(record.get("ok")),
            "ttft_s": record.get("ttft_s"),
            "e2e_s": record.get("e2e_s"),
            "server_generation_s": record.get("server_generation_s"),
            "generated_tokens": record.get("generated_tokens"),
            "token_count_source": record.get("token_count_source", "unavailable"),
            "tokens_per_s": record.get("tokens_per_s"),
            "response_storage_status": storage_status,
            "response_storage_mode": mode,
            "error": self._redact_sensitive(record.get("error", ""), response),
            "error_code": record.get("error_code", ""),
            "failure": self._redact_sensitive(record.get("failure"), response),
            "inference_path": record.get("inference_path"),
            "fallback_reason_code": record.get("fallback_reason_code"),
            "chat_template_hash": record.get("chat_template_hash"),
            "template_hash": record.get("template_hash"),
            "controller_executor_queue_wait_s": record.get("controller_executor_queue_wait_s"),
            "worker_inference_lock_wait_s": record.get("worker_inference_lock_wait_s"),
            "prompt_eval_s": record.get("prompt_eval_s"),
            "inference_slots": record.get("inference_slots", 1),
        }
        if mode in {"full", "hash_only"}:
            value["output_chars"] = (
                len(response) if "response" in record else record.get("output_chars")
            )
            value["output_sha256"] = output_sha256
        if mode == "full":
            value["response"] = response
        if self.config.persist_prompt:
            value["prompt"] = self.config.prompt
        else:
            value["prompt_sha256"] = prompt_sha256
            value["prompt_chars"] = len(self.config.prompt)
            if self.config.prompt_set_version:
                value["prompt_set_version"] = self.config.prompt_set_version
        return value

    def _redact_sensitive(self, value: Any, response: str = "") -> Any:
        sensitive = tuple(
            item for item in (self.config.prompt, response) if isinstance(item, str) and item
        )
        if isinstance(value, str):
            redacted = value
            for item in sensitive:
                redacted = redacted.replace(item, "[REDACTED]")
            return redacted
        if isinstance(value, Mapping):
            return {key: self._redact_sensitive(item, response) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_sensitive(item, response) for item in value]
        if isinstance(value, tuple):
            return [self._redact_sensitive(item, response) for item in value]
        return value

    def _event_result(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        response = str(record.get("response") or "")
        value = dict(record)
        for key in ("response", "output", "text", "prompt"):
            value.pop(key, None)
        value["response_storage_status"] = {
            "full": "stored",
            "hash_only": "hash_only",
            "none": "not_persisted",
        }[self.config.response_storage_mode]
        if self.config.response_storage_mode == "none":
            value.pop("output_chars", None)
            value.pop("output_sha256", None)
        return self._redact_sensitive(value, response)

    def safe_artifact(self, value: Any) -> Any:
        """Remove the configured prompt from logs, events, errors, and summaries."""
        return self._redact_sensitive(value)

    def recover_records(self) -> list[Dict[str, Any]]:
        """Expose already-durable request results for crash recovery tooling."""
        return self.repository.read_responses(self.run_id)

    def append_measurement(self, measurement: Mapping[str, Any]) -> None:
        self.repository.append_measurement(self.run_id, measurement)

    def complete(
        self, records: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]
    ) -> None:
        safe_records = []
        for record in records:
            safe_record = self._redact_sensitive(
                record, str(record.get("response") or "")
            )
            if self.config.response_storage_mode == "none":
                safe_record["output_chars"] = None
                safe_record["output_sha256"] = ""
            safe_records.append(safe_record)
        self.repository.write_requests(self.run_id, safe_records)
        self.repository.write_summary(self.run_id, self.safe_artifact(summary))

    def write_summary(self, summary: Mapping[str, Any]) -> None:
        self.repository.write_summary(self.run_id, self.safe_artifact(summary))


__all__ = ["ProgressCallback", "RunPersistence"]
