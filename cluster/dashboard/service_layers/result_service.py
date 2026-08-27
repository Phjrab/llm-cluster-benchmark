"""Durable run reading and recoverable deletion service."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from cluster.application.jobs import NONTERMINAL_JOB_STATES
from cluster.dashboard.service_layers.errors import DashboardServiceError
from cluster.domain.events import EventChannel
from cluster.infrastructure.storage import StorageCorruptionError


def _valid_run_id(run_id: str) -> bool:
    return bool(run_id) and run_id.replace("_", "").isalnum()


_STORAGE_STATUSES = {"stored", "hash_only", "not_persisted", "legacy_missing"}


def normalize_response_storage(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize legacy records and enforce the persisted disclosure boundary."""
    value = dict(record)
    explicit = str(value.get("response_storage_status") or "")
    if explicit in _STORAGE_STATUSES:
        status = explicit
    elif any(key in value for key in ("response", "output", "text")):
        status = "stored"
    else:
        status = "legacy_missing"
    value["response_storage_status"] = status
    if status != "stored":
        for key in ("response", "output", "text"):
            value.pop(key, None)
    if status == "not_persisted":
        for key in ("output_chars", "output_sha256"):
            value.pop(key, None)
    return value


class ResultService:
    """Read and soft-delete run artifacts through injected repositories."""

    def __init__(
        self,
        *,
        run_repository: Callable[[], Any],
        suite_repository: Callable[[], Any],
        read_suites: Callable[..., list[dict[str, Any]]],
        with_suite_metadata: Callable[[dict[str, Any], Mapping[str, dict[str, Any]]], dict[str, Any]],
        active_experiment: Callable[[], dict[str, Any] | None],
        publish_event: Callable[..., None],
        utc_now: Callable[[], str],
    ) -> None:
        self._run_repository = run_repository
        self._suite_repository = suite_repository
        self._read_suites = read_suites
        self._with_suite_metadata = with_suite_metadata
        self._active_experiment = active_experiment
        self._publish_event = publish_event
        self._utc_now = utc_now

    @staticmethod
    def _check_id(run_id: str) -> None:
        if not _valid_run_id(run_id):
            raise DashboardServiceError(400, "Invalid run id")

    def run(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        try:
            summary = self._run_repository().read_summary(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(500, "Run summary is corrupted") from exc
        suites = {str(suite["suite_id"]): suite for suite in self._read_suites(limit=0)}
        return self._with_suite_metadata(summary, suites)

    def responses(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        repository = self._run_repository()
        try:
            summary = repository.read_summary(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(500, "Run summary is corrupted") from exc
        responses = [
            normalize_response_storage(item)
            for item in repository.read_responses(run_id)
        ]
        mode = str(summary.get("response_storage_mode") or "")
        if responses:
            statuses = {str(item["response_storage_status"]) for item in responses}
            overall_status = statuses.pop() if len(statuses) == 1 else "mixed"
        else:
            overall_status = {
                "full": "stored",
                "hash_only": "hash_only",
                "none": "not_persisted",
            }.get(mode, "legacy_missing")
        return {
            "run_id": run_id,
            "response_storage_mode": mode or None,
            "response_storage_status": overall_status,
            "responses": responses,
        }

    def measurements(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        repository = self._run_repository()
        try:
            repository.read_summary(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(500, "Run summary is corrupted") from exc
        return {
            "run_id": run_id,
            "schema_version": 1,
            "measurements": repository.read_measurements(run_id),
        }

    def delete(self, run_id: str) -> dict[str, Any]:
        self._check_id(run_id)
        repository = self._run_repository()
        try:
            summary = repository.read_summary(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(500, "Run summary is corrupted") from exc

        active = self._active_experiment()
        suite_id = str(summary.get("suite_id") or "")
        if active and active.get("status") in NONTERMINAL_JOB_STATES:
            if suite_id and suite_id == str(active.get("suite_id") or ""):
                raise DashboardServiceError(409, "A run in the active model suite cannot be deleted")
            if run_id == str((active.get("latest") or {}).get("run_id") or ""):
                raise DashboardServiceError(409, "The active run cannot be deleted")

        try:
            repository.delete(run_id)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Run not found") from exc
        except StorageCorruptionError as exc:
            raise DashboardServiceError(409, "Run storage identity is unsafe") from exc
        except OSError as exc:
            raise DashboardServiceError(500, "Run could not be moved to private trash") from exc

        suite_removed = False
        if suite_id:
            suites = self._suite_repository()
            try:
                suite = suites.read(suite_id)
            except (FileNotFoundError, StorageCorruptionError):
                suite = None
            if suite is not None:
                remaining = [
                    item for item in (suite.get("summaries") or [])
                    if str(item.get("run_id") or "") != run_id
                ]
                if not remaining:
                    models = []
                    for model in suite.get("models") or []:
                        record = dict(model)
                        record["status"] = "deleted"
                        record["run_id"] = None
                        models.append(record)
                    deleted_ids = list(suite.get("deleted_run_ids") or [])
                    if run_id not in deleted_ids:
                        deleted_ids.append(run_id)
                    suite.update({
                        "status": "deleted",
                        "summaries": [],
                        "models": models,
                        "completed_models": 0,
                        "deleted_run_ids": deleted_ids,
                        "updated_at": self._utc_now(),
                    })
                    suites.write(suite_id, suite)
                else:
                    deleted_index = int(summary.get("model_index") or 0)
                    models = []
                    for model in suite.get("models") or []:
                        record = dict(model)
                        if int(record.get("model_index") or 0) == deleted_index:
                            record["status"] = "deleted"
                            record["run_id"] = None
                        models.append(record)
                    deleted_ids = list(suite.get("deleted_run_ids") or [])
                    if run_id not in deleted_ids:
                        deleted_ids.append(run_id)
                    suite.update(
                        {
                            "status": "partial",
                            "summaries": remaining,
                            "models": models,
                            "completed_models": sum(item.get("status") == "completed" for item in remaining),
                            "deleted_run_ids": deleted_ids,
                            "updated_at": self._utc_now(),
                        }
                    )
                    suites.write(suite_id, suite)

        self._publish_event(
            "results_changed",
            channel=EventChannel.EXPERIMENT,
            operation="deleted",
            run_id=run_id,
            suite_id=suite_id or None,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "suite_id": suite_id or None,
            "suite_removed": suite_removed,
            "recoverable": True,
        }

    def trash(self) -> dict[str, Any]:
        return {"trash": self._run_repository().list_trash()}

    def restore(self, trash_id: str) -> dict[str, Any]:
        repository = self._run_repository()
        try:
            destination = repository.restore(trash_id)
            summary = repository.read_summary(destination.name)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Trash entry not found") from exc
        except FileExistsError as exc:
            raise DashboardServiceError(409, "An active result with the same run id already exists") from exc
        except (StorageCorruptionError, ValueError) as exc:
            raise DashboardServiceError(409, "Trash entry identity is unsafe or corrupted") from exc
        except OSError as exc:
            raise DashboardServiceError(500, "Run could not be restored from private trash") from exc

        run_id = str(summary.get("run_id") or destination.name)
        suite_id = str(summary.get("suite_id") or "")
        suite_restored = False
        if suite_id:
            suites = self._suite_repository()
            try:
                suite = suites.read(suite_id)
            except (FileNotFoundError, StorageCorruptionError):
                suite = None
            if suite is not None:
                summaries = [
                    dict(item) for item in (suite.get("summaries") or [])
                    if str(item.get("run_id") or "") != run_id
                ]
                summaries.append(dict(summary))
                summaries.sort(key=lambda item: int(item.get("model_index") or 0))
                restored_index = int(summary.get("model_index") or 0)
                models = []
                for model in suite.get("models") or []:
                    record = dict(model)
                    if int(record.get("model_index") or 0) == restored_index:
                        record["status"] = str(summary.get("status") or "completed")
                        record["run_id"] = run_id
                    models.append(record)
                deleted_ids = [
                    value for value in (suite.get("deleted_run_ids") or []) if value != run_id
                ]
                suite.update({
                    "status": "completed" if models and all(item.get("status") == "completed" for item in models) else "partial",
                    "summaries": summaries,
                    "models": models,
                    "completed_models": sum(item.get("status") == "completed" for item in models),
                    "deleted_run_ids": deleted_ids,
                    "updated_at": self._utc_now(),
                })
                suites.write(suite_id, suite)
                suite_restored = True

        self._publish_event(
            "results_changed",
            channel=EventChannel.EXPERIMENT,
            operation="restored",
            run_id=run_id,
            suite_id=suite_id or None,
        )
        return {
            "ok": True,
            "run_id": run_id,
            "suite_id": suite_id or None,
            "suite_restored": suite_restored,
        }

    def purge(self, trash_id: str, *, confirmed: bool, archive_sha256: str) -> dict[str, Any]:
        if confirmed is not True:
            raise DashboardServiceError(400, "Permanent deletion requires explicit confirmation")
        try:
            self._run_repository().purge(trash_id, archive_sha256=archive_sha256)
        except FileNotFoundError as exc:
            raise DashboardServiceError(404, "Trash entry not found") from exc
        except PermissionError as exc:
            raise DashboardServiceError(409, "Formal campaign results are retention-protected") from exc
        except ValueError as exc:
            raise DashboardServiceError(409, str(exc)) from exc
        except (StorageCorruptionError, OSError) as exc:
            raise DashboardServiceError(500, "Trash entry could not be permanently deleted") from exc
        self._publish_event(
            "results_changed",
            channel=EventChannel.EXPERIMENT,
            operation="purged",
            trash_id=trash_id,
        )
        return {"ok": True, "trash_id": trash_id, "permanent": True}


__all__ = ["ResultService", "normalize_response_storage"]
