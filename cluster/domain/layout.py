"""Pure project path derivation without creating or reading files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import DomainValidationError
from .identifiers import (
    validate_experiment_id,
    validate_model_id,
    validate_run_id,
    validate_suite_id,
)


@dataclass(frozen=True)
class ProjectLayout:
    root: Path

    def __post_init__(self) -> None:
        root = Path(self.root)
        raw = str(root)
        broad = {"/home", "/opt", "/srv", "/tmp", "/Users", "/var"}
        is_user_home = len(root.parts) < 4 and len(root.parts) >= 2 and root.parts[1] in {"home", "Users"}
        if (
            not root.is_absolute()
            or ".." in root.parts
            or root == Path(root.anchor)
            or raw in broad
            or is_user_home
            or any(ord(character) < 32 or ord(character) == 127 for character in raw)
        ):
            raise DomainValidationError("Project root must be a dedicated absolute path without traversal")
        object.__setattr__(self, "root", root)

    @property
    def cluster_dir(self) -> Path:
        return self.root / "cluster"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def runtime_dir(self) -> Path:
        """Legacy-compatible runtime path; migration occurs in Phase 02."""
        return self.root / ".run" / "cluster"

    @property
    def controller_runtime_dir(self) -> Path:
        return self.root / ".run" / "controller"

    @property
    def inventory_path(self) -> Path:
        return self.runtime_dir / "nodes.local.csv"

    @property
    def settings_path(self) -> Path:
        return self.runtime_dir / "settings.json"

    @property
    def worker_token_path(self) -> Path:
        return self.runtime_dir / "worker.token"

    @property
    def dashboard_token_path(self) -> Path:
        return self.runtime_dir / "dashboard.token"

    @property
    def results_dir(self) -> Path:
        return self.runtime_dir / "results"

    @property
    def experiments_dir(self) -> Path:
        return self.runtime_dir / "experiments"

    @property
    def environment_dir(self) -> Path:
        return self.runtime_dir / "environment"

    @property
    def jobs_dir(self) -> Path:
        return self.runtime_dir / "jobs"

    @property
    def suites_dir(self) -> Path:
        return self.results_dir / "_suites"

    def run_dir(self, run_id: str) -> Path:
        return self.results_dir / validate_run_id(run_id)

    def experiment_path(self, experiment_id: str) -> Path:
        return self.experiments_dir / f"{validate_experiment_id(experiment_id)}.json"

    def suite_path(self, suite_id: str) -> Path:
        return self.suites_dir / f"{validate_suite_id(suite_id)}.json"

    def model_path(self, model_id: str) -> Path:
        return self.models_dir.joinpath(*validate_model_id(model_id).split("/"))
