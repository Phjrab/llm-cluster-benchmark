"""Repository-root discovery and runtime path compatibility adapter.

The domain layer intentionally has no environment or discovery behavior.  This
module is the single compatibility boundary for the legacy ``.run/cluster``
layout and optional path overrides used by the dashboard and CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from cluster.domain.layout import ProjectLayout


@dataclass(frozen=True)
class RuntimePaths:
    """Resolved controller paths without creating files or directories."""

    layout: ProjectLayout
    runtime_dir: Path
    inventory_path: Path
    results_dir: Path

    @property
    def settings_path(self) -> Path:
        return self.runtime_dir / "settings.json"

    @property
    def environment_dir(self) -> Path:
        return self.runtime_dir / "environment"

    @property
    def experiments_dir(self) -> Path:
        return self.runtime_dir / "experiments"

    @property
    def worker_token_path(self) -> Path:
        return self.runtime_dir / "worker.token"

    @property
    def dashboard_token_path(self) -> Path:
        return self.runtime_dir / "dashboard.token"

    @property
    def jobs_dir(self) -> Path:
        return self.runtime_dir / "jobs"


def repository_root() -> Path:
    """Return this checkout's root from one explicit integration boundary."""
    return Path(__file__).resolve().parents[2]


def default_project_layout() -> ProjectLayout:
    return ProjectLayout(repository_root())


def resolve_runtime_paths(env: Optional[Mapping[str, str]] = None) -> RuntimePaths:
    """Resolve legacy-compatible paths, honoring documented runtime overrides."""
    values = os.environ if env is None else env
    layout = default_project_layout()
    runtime_dir = Path(values.get("CLUSTER_RUNTIME_DIR") or layout.runtime_dir)
    inventory_path = Path(values.get("CLUSTER_INVENTORY") or runtime_dir / "nodes.local.csv")
    results_dir = Path(values.get("CLUSTER_RESULTS_DIR") or runtime_dir / "results")
    return RuntimePaths(
        layout=layout,
        runtime_dir=runtime_dir,
        inventory_path=inventory_path,
        results_dir=results_dir,
    )


__all__ = ["RuntimePaths", "default_project_layout", "repository_root", "resolve_runtime_paths"]
