"""Benchmark run persistence and progress event boundary."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

from cluster.domain.experiment import ExperimentConfig
from cluster.infrastructure.storage import FilesystemRunRepository

from .transport import utc_now

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
        if not config.persist_prompt:
            persisted_config.pop("prompt", None)
            persisted_config["prompt_sha256"] = hashlib.sha256(
                config.prompt.encode("utf-8")
            ).hexdigest()
        self.run_dir = self.repository.create(run_id, persisted_config)

    def emit(self, event_type: str, **payload: Any) -> Dict[str, Any]:
        if event_type == "request_completed" and isinstance(payload.get("result"), Mapping):
            self.repository.append_response(
                self.run_id,
                self._response_record(payload["result"]),
            )
        event = {
            "type": event_type,
            "at": utc_now(),
            "run_id": self.run_id,
            "suite_id": self.config.suite_id,
            "model_id": self.config.model_id,
            "model_index": self.config.model_index,
            "model_count": self.config.model_count,
            **payload,
        }
        self.repository.append_event(self.run_id, event)
        if self.progress:
            self.progress(event)
        return event

    def _response_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the durable raw-response record without changing requests.csv."""
        response = str(record.get("response") or "")
        prompt_sha256 = hashlib.sha256(self.config.prompt.encode("utf-8")).hexdigest()
        value: Dict[str, Any] = {
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
            "tokens_per_s": record.get("tokens_per_s"),
            "output_sha256": record.get("output_sha256"),
            "response": response,
            "error": record.get("error", ""),
            "error_code": record.get("error_code", ""),
            "failure": record.get("failure"),
        }
        if self.config.persist_prompt:
            value["prompt"] = self.config.prompt
        else:
            value["prompt_sha256"] = prompt_sha256
        return value

    def recover_records(self) -> list[Dict[str, Any]]:
        """Expose already-durable request results for crash recovery tooling."""
        return self.repository.read_responses(self.run_id)

    def complete(
        self, records: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]
    ) -> None:
        self.repository.write_requests(self.run_id, records)
        self.repository.write_summary(self.run_id, summary)

    def write_summary(self, summary: Mapping[str, Any]) -> None:
        self.repository.write_summary(self.run_id, summary)


__all__ = ["ProgressCallback", "RunPersistence"]
