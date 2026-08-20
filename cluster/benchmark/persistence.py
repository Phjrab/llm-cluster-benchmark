"""Benchmark run persistence and progress event boundary."""

from __future__ import annotations

from dataclasses import asdict
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
        self.run_dir = self.repository.create(run_id, asdict(config))

    def emit(self, event_type: str, **payload: Any) -> Dict[str, Any]:
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

    def complete(
        self, records: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]
    ) -> None:
        self.repository.write_requests(self.run_id, records)
        self.repository.write_summary(self.run_id, summary)

    def write_summary(self, summary: Mapping[str, Any]) -> None:
        self.repository.write_summary(self.run_id, summary)


__all__ = ["ProgressCallback", "RunPersistence"]
