"""Live, read-only registration of study-owned Sweep manifests.

The study runner remains the sole lifecycle owner. No prompts, manifests or run
artifacts are copied into Dashboard storage, and discovery cannot dispatch jobs.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from cluster.application.sweep_runner import SweepRepository, SweepStateError
from cluster.infrastructure.storage import read_json_object
from .errors import DashboardServiceError


class StudySweeps:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def registrations(self) -> dict[str, tuple[SweepRepository, dict[str, Any], str]]:
        found = {}
        if self.directory.is_symlink() or not self.directory.is_dir():
            return found
        for study in sorted(self.directory.iterdir()):
            if study.is_symlink() or not study.is_dir():
                continue
            sweeps = study / "sweeps"
            if sweeps.is_symlink() or not sweeps.is_dir():
                continue
            repository = SweepRepository(sweeps)
            for target in sorted(sweeps.iterdir()):
                if target.is_symlink() or not target.is_dir():
                    continue
                try:
                    manifest = repository.read(target.name)
                except (SweepStateError, OSError, ValueError):
                    continue
                sweep_id = manifest["sweep_id"]
                if sweep_id != target.name:
                    continue
                if sweep_id in found:
                    raise DashboardServiceError(409, "Study sweep ID is ambiguous")
                found[sweep_id] = (repository, manifest, study.name)
        return found

    @staticmethod
    def energy_policy(repository: SweepRepository) -> str | None:
        path = repository.directory.parent / "execution_policy.json"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            value = read_json_object(path).get("energy")
        except (OSError, ValueError):
            raise DashboardServiceError(409, "Study execution policy is unreadable") from None
        return value if isinstance(value, str) and value.upper().startswith("N/A") else None

    @staticmethod
    def public(manifest: dict[str, Any], study: str) -> dict[str, Any]:
        def timestamp(key: str) -> float:
            try:
                return datetime.fromisoformat(manifest[key].replace("Z", "+00:00")).timestamp()
            except (KeyError, TypeError, ValueError):
                return 0.0
        return {
            "schema_version": 1, "artifact_type": "study_sweep_registration",
            "sweep_id": manifest["sweep_id"], "status": manifest["status"],
            "plan_revision": manifest["revision"], "plan_sha256": manifest["plan_sha256"],
            "study_id": study, "read_only": True, "management": "study_runner",
            "created_at_unix": timestamp("created_at"),
            "updated_at_unix": timestamp("updated_at"),
        }
