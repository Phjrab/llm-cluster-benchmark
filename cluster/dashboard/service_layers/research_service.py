"""Read-only campaign and cross-run research projections."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cluster.dashboard.research_views import (
    campaign_detail,
    campaign_overview,
    compare_payload,
    research_readiness,
)
from cluster.dashboard.service_layers.errors import DashboardServiceError
from cluster.research.campaign import CampaignRepository, CampaignStateError


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
    ) -> None:
        self._campaigns_dir = campaigns_dir
        self._read_runs = read_runs
        self._read_research_document = read_research_document
        self._status_snapshot = status_snapshot
        self._read_environment = read_environment
        self._controller_commit = controller_commit
        self._repository_factory = repository_factory

    def campaigns(self) -> dict[str, Any]:
        manifests = self._repository_factory(self._campaigns_dir).list()
        return {
            "schema_version": 1,
            "campaigns": [campaign_overview(manifest) for manifest in manifests],
        }

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        repository = self._repository_factory(self._campaigns_dir)
        try:
            manifest = repository.read(campaign_id)
            events = repository.read_events(campaign_id)
        except (CampaignStateError, ValueError) as exc:
            raise DashboardServiceError(404, "Campaign not found") from exc
        return campaign_detail(manifest, events)

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
