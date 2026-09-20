"""Campaign control and cross-run research projections."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any, Callable, Mapping, Sequence

from cluster.dashboard.research_views import (
    campaign_detail,
    campaign_overview,
    compare_payload,
    research_readiness,
)
from cluster.dashboard.service_layers.errors import DashboardServiceError
from cluster.research.campaign import CampaignRepository, CampaignRunner, CampaignStateError


class ResearchService:
    """Serve research views without owning Dashboard globals or HTTP types."""

    def __init__(
        self,
        *,
        campaigns_dir: Path,
        read_runs: Callable[..., list[dict[str, Any]]],
        read_research_document: Callable[[str], dict[str, Any]],
        status_snapshot: Callable[[], list[dict[str, Any]]],
        read_environment: Callable[[], list[dict[str, Any]]],
        controller_commit: Callable[[], str],
        repository_factory: Callable[[Path], CampaignRepository] = CampaignRepository,
        runner_factory: Callable[[str], CampaignRunner] | None = None,
        drive_interval_s: float = 0.25,
    ) -> None:
        self._campaigns_dir = campaigns_dir
        self._read_runs = read_runs
        self._read_research_document = read_research_document
        self._status_snapshot = status_snapshot
        self._read_environment = read_environment
        self._controller_commit = controller_commit
        self._repository_factory = repository_factory
        self._runner_factory = runner_factory
        self._drive_interval_s = max(float(drive_interval_s), 0.05)
        self._workers: dict[str, threading.Thread] = {}
        self._worker_lock = threading.Lock()
        self._stop = threading.Event()

    def _repository(self) -> CampaignRepository:
        return self._repository_factory(self._campaigns_dir)

    def _runner(self, campaign_id: str) -> CampaignRunner:
        if self._runner_factory is None:
            raise DashboardServiceError(503, "Campaign control adapter is unavailable")
        return self._runner_factory(campaign_id)

    def _assert_execution_gate(self) -> None:
        matrix = self._read_research_document("formal_experiment_matrix.json")
        gate = matrix.get("execution_gate") if isinstance(matrix, Mapping) else {}
        if not isinstance(gate, Mapping) or gate.get("formal_execution_allowed") is not True:
            gate_detail = gate if isinstance(gate, Mapping) else {}
            raise DashboardServiceError(
                409,
                {
                    "code": "FORMAL_EXECUTION_GATE_CLOSED",
                    "blocking_phases": list(gate_detail.get("blocking_phases") or []),
                    "blocking_requirements": list(
                        gate_detail.get("blocking_requirements") or []
                    ),
                    "reason": gate_detail.get("reason")
                    or "Formal execution is not authorized",
                },
            )

    def _manifest(self, campaign_id: str) -> dict[str, Any]:
        try:
            return self._repository().read(campaign_id)
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(404, "Campaign not found") from exc

    def _drive(self, campaign_id: str, runner: CampaignRunner) -> None:
        try:
            while not self._stop.is_set():
                state = runner.tick(campaign_id)
                if state.get("status") in {"paused", "completed", "failed", "cancelled"}:
                    return
                self._stop.wait(self._drive_interval_s)
        except Exception as exc:
            try:
                self._repository().append_event(
                    campaign_id,
                    {
                        "type": "campaign_control_error",
                        "at": datetime.now(timezone.utc).isoformat(),
                        "error_type": type(exc).__name__,
                    },
                )
            except Exception:
                pass
        finally:
            with self._worker_lock:
                current = self._workers.get(campaign_id)
                if current is threading.current_thread():
                    self._workers.pop(campaign_id, None)

    def _ensure_worker(self, campaign_id: str) -> None:
        with self._worker_lock:
            current = self._workers.get(campaign_id)
            if current is not None and current.is_alive():
                return
            runner = self._runner(campaign_id)
            worker = threading.Thread(
                target=self._drive,
                args=(campaign_id, runner),
                name=f"formal-campaign-{campaign_id}",
                daemon=True,
            )
            self._workers[campaign_id] = worker
            worker.start()

    def recover_active_campaigns(self) -> dict[str, Any]:
        """Resume only previously running campaigns, subject to the formal gate."""

        self._stop.clear()
        try:
            self._assert_execution_gate()
        except DashboardServiceError as exc:
            if exc.status_code == 409:
                return {"recovered": [], "gate_blocked": True}
            raise
        recovered = []
        for manifest in self._repository().list():
            if manifest.get("status") != "running":
                continue
            campaign_id = str(manifest.get("campaign_id") or "")
            if not campaign_id:
                continue
            self._ensure_worker(campaign_id)
            recovered.append(campaign_id)
        return {"recovered": recovered, "gate_blocked": False}

    def shutdown(self) -> None:
        self._stop.set()

    def campaigns(self) -> dict[str, Any]:
        manifests = self._repository().list()
        return {
            "schema_version": 1,
            "campaigns": [campaign_overview(manifest) for manifest in manifests],
        }

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        repository = self._repository()
        try:
            manifest = repository.read(campaign_id)
            events = repository.read_events(campaign_id)
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(404, "Campaign not found") from exc
        return campaign_detail(manifest, events)

    def start_campaign(self, campaign_id: str) -> dict[str, Any]:
        self._assert_execution_gate()
        manifest = self._manifest(campaign_id)
        if manifest.get("status") not in {"ready", "running"}:
            raise DashboardServiceError(409, "Only a ready or running campaign can be started")
        self._ensure_worker(campaign_id)
        return self.campaign(campaign_id)

    def pause_campaign(self, campaign_id: str) -> dict[str, Any]:
        self._manifest(campaign_id)
        try:
            self._runner(campaign_id).pause(campaign_id)
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(409, str(exc)) from exc
        return self.campaign(campaign_id)

    def resume_campaign(self, campaign_id: str) -> dict[str, Any]:
        self._assert_execution_gate()
        self._manifest(campaign_id)
        try:
            self._runner(campaign_id).resume(campaign_id)
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(409, str(exc)) from exc
        self._ensure_worker(campaign_id)
        return self.campaign(campaign_id)

    def cancel_campaign(self, campaign_id: str) -> dict[str, Any]:
        self._manifest(campaign_id)
        try:
            self._runner(campaign_id).cancel(campaign_id)
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(409, str(exc)) from exc
        self._ensure_worker(campaign_id)
        return self.campaign(campaign_id)

    def retry_campaign_cell(
        self, campaign_id: str, campaign_cell_id: str, *, reason: str
    ) -> dict[str, Any]:
        self._assert_execution_gate()
        self._manifest(campaign_id)
        try:
            self._runner(campaign_id).retry_cell(
                campaign_id, campaign_cell_id, reason=reason
            )
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(409, str(exc)) from exc
        self._ensure_worker(campaign_id)
        return self.campaign(campaign_id)

    def compare_runs(self) -> dict[str, Any]:
        return compare_payload(self._read_runs(limit=10_000))

    def readiness(self) -> dict[str, Any]:
        return research_readiness(
            model_lock=self._read_research_document("model_lock.json"),
            runtime_lock=self._read_research_document("runtime_lock.json"),
            matrix=self._read_research_document("formal_experiment_matrix.json"),
            live_status=self._status_snapshot(),
            environment=self._read_environment(),
            controller_commit=self._controller_commit(),
        )


__all__ = ["ResearchService"]
