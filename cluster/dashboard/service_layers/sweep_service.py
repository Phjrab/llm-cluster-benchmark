"""Typed Dashboard application service for durable exploratory sweeps."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from cluster.application.sweep_planner import verify_plan
from cluster.application.sweep_runner import SweepRepository, SweepStateError, SweepSupervisor
from cluster.domain.sweep import ResolvedPlan
from cluster.infrastructure.storage import atomic_write_text, read_json_object

from .errors import DashboardServiceError


Resolver = Callable[[Mapping[str, Any], bool], Any]
SupervisorFactory = Callable[
    [Callable[[ResolvedPlan, Any], str], Callable[[ResolvedPlan, Any], Mapping[str, Any]]],
    SweepSupervisor,
]
TERMINAL = frozenset({"completed", "partial", "failed", "cancelled"})


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class SweepDraftRepository:
    """Private saved-plan metadata, prompt input and API idempotency claims."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    @staticmethod
    def validate_id(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 128
            or not value[0].isalnum()
            or any(not (char.isalnum() or char in "_.-") for char in value)
        ):
            raise DashboardServiceError(400, "Invalid sweep ID")
        return value

    def _path(self, sweep_id: str) -> Path:
        return self.directory / f"{self.validate_id(sweep_id)}.json"

    def _prompt_path(self, sweep_id: str) -> Path:
        return self.directory / f".{self.validate_id(sweep_id)}.prompts.json"

    @contextmanager
    def locked(self, sweep_id: str) -> Iterator[None]:
        if self.directory.is_symlink():
            raise DashboardServiceError(503, "Sweep draft storage is unavailable")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        fd = os.open(
            self.directory / f".{self.validate_id(sweep_id)}.lock",
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def create(self, sweep_id: str, document: Mapping[str, Any], prompts: Mapping[str, str]) -> dict[str, Any]:
        with self.locked(sweep_id):
            path = self._path(sweep_id)
            if path.exists():
                existing = self.read(sweep_id)
                if existing.get("plan_sha256") == document.get("plan_sha256"):
                    return existing
                raise DashboardServiceError(409, "Sweep ID already identifies another plan")
            value = dict(document)
            atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n", default_mode=0o600)
            atomic_write_text(
                self._prompt_path(sweep_id),
                json.dumps({"prompts": dict(prompts)}, ensure_ascii=False) + "\n",
                default_mode=0o600,
            )
            return value

    def read(self, sweep_id: str) -> dict[str, Any]:
        path = self._path(sweep_id)
        if path.is_symlink() or not path.is_file():
            raise DashboardServiceError(404, "Sweep not found")
        try:
            value = read_json_object(path)
        except (OSError, ValueError) as exc:
            raise DashboardServiceError(503, "Sweep draft is unavailable") from exc
        if value.get("sweep_id") != sweep_id or value.get("artifact_type") != "sweep_api_draft":
            raise DashboardServiceError(503, "Sweep draft is invalid")
        return value

    def list(self, *, offset: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        if self.directory.is_symlink():
            raise DashboardServiceError(503, "Sweep draft storage is unavailable")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        values = []
        for path in sorted(self.directory.glob("*.json"), reverse=True):
            if path.name.startswith(".") or path.is_symlink():
                continue
            try:
                value = read_json_object(path)
            except (OSError, ValueError):
                continue
            if value.get("artifact_type") == "sweep_api_draft":
                values.append(value)
        return values[max(0, offset): max(0, offset) + max(1, min(limit, 100))]

    def update(self, sweep_id: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        with self.locked(sweep_id):
            value = self.read(sweep_id)
            mutate(value)
            atomic_write_text(
                self._path(sweep_id),
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                default_mode=0o600,
            )
            return value

    def prompts(self, sweep_id: str) -> dict[str, str]:
        path = self._prompt_path(sweep_id)
        if path.is_symlink() or not path.is_file():
            raise DashboardServiceError(409, "Private prompt input is unavailable")
        try:
            raw = read_json_object(path).get("prompts")
        except (OSError, ValueError) as exc:
            raise DashboardServiceError(503, "Private prompt input is unavailable") from exc
        if not isinstance(raw, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in raw.items()):
            raise DashboardServiceError(503, "Private prompt input is invalid")
        return dict(raw)

    def scrub_prompts(self, sweep_id: str) -> None:
        path = self._prompt_path(sweep_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class SweepService:
    """Application lifecycle boundary used by thin FastAPI routes."""

    def __init__(
        self,
        *,
        drafts: SweepDraftRepository,
        runs: SweepRepository,
        resolver: Resolver,
        supervisor_factory: SupervisorFactory,
    ) -> None:
        self.drafts = drafts
        self.runs = runs
        self.resolver = resolver
        self.supervisor_factory = supervisor_factory

    @staticmethod
    def _request(payload: Any) -> dict[str, Any]:
        raw = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        raw.pop("sweep_id", None)
        return raw

    @staticmethod
    def _public_request(request: Mapping[str, Any]) -> dict[str, Any]:
        value = json.loads(json.dumps(request))
        prompts = value.get("prompts") or []
        value["prompts"] = [
            {
                "ref": item["ref"],
                "model_ref": item.get("model_ref"),
                "mode": item.get("mode", "same_text"),
                "target_input_tokens": item.get("target_input_tokens"),
                "text_sha256": hashlib.sha256(str(item["text"]).encode()).hexdigest(),
                "text_bytes": len(str(item["text"]).encode()),
            }
            for item in prompts
        ]
        return value

    def _resolve(self, request: Mapping[str, Any], *, refresh: bool) -> tuple[ResolvedPlan, list[dict[str, Any]]]:
        try:
            result = self.resolver(request, refresh)
            plan = result.plan if hasattr(result, "plan") else result
            candidates = [item.to_dict() for item in getattr(result, "candidates", ())]
            if not isinstance(plan, ResolvedPlan):
                raise TypeError("resolver returned no plan")
            plan = verify_plan(plan.to_json())
        except DashboardServiceError:
            raise
        except (TypeError, ValueError) as exc:
            raise DashboardServiceError(400, "Sweep input could not be resolved") from exc
        return plan, candidates

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "artifact_type": "sweep_api_capabilities",
            "lifecycle": [
                "preview", "save-draft", "list", "get", "start", "pause", "resume",
                "cancel", "retry-trial", "events", "results", "export-plan",
            ],
            "max_trials": 500,
            "max_parallel_jobs": 2,
            "preview_uses_cached_evidence": True,
            "fresh_readiness_route": True,
            "automatic_retry": False,
            "formal_gate_client_bypass": False,
        }

    def preview(self, payload: Any, *, refresh: bool = False) -> dict[str, Any]:
        plan, candidates = self._resolve(self._request(payload), refresh=refresh)
        return {"plan": plan.to_dict(), "candidates": candidates, "evidence_mode": "fresh" if refresh else "cached"}

    def save(self, payload: Any) -> dict[str, Any]:
        sweep_id = str(payload.sweep_id)
        request = self._request(payload)
        plan, candidates = self._resolve(request, refresh=False)
        prompts = {
            f"{item.get('model_ref') or '*'}::{item['ref']}": item["text"]
            for item in request["prompts"]
        }
        now = time.time()
        document = {
            "schema_version": 1,
            "artifact_type": "sweep_api_draft",
            "sweep_id": sweep_id,
            "status": "draft",
            "plan_revision": plan.spec.revision,
            "plan_sha256": plan.plan_sha256,
            "plan": plan.to_dict(),
            "resolution_request": self._public_request(request),
            "candidate_count": len(candidates),
            "created_at_unix": now,
            "updated_at_unix": now,
            "operations": {},
        }
        saved = self.drafts.create(sweep_id, document, prompts)
        return self._public_draft(saved)

    @staticmethod
    def _public_draft(value: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value.get(key)
            for key in (
                "schema_version", "artifact_type", "sweep_id", "status", "plan_revision",
                "plan_sha256", "resolution_request", "candidate_count", "created_at_unix",
                "updated_at_unix",
            )
        }

    def _input_request(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        public = json.loads(json.dumps(draft["resolution_request"]))
        texts = self.drafts.prompts(str(draft["sweep_id"]))
        for prompt in public.get("prompts") or []:
            key = f"{prompt.get('model_ref') or '*'}::{prompt.get('ref') or ''}"
            prompt["text"] = texts.get(key, "")
            prompt.pop("text_sha256", None)
            prompt.pop("text_bytes", None)
        return public

    @staticmethod
    def _check_plan(draft: Mapping[str, Any], payload: Any) -> None:
        if int(payload.plan_revision) != int(draft["plan_revision"]):
            raise DashboardServiceError(409, "Sweep plan revision is stale")
        if str(payload.plan_sha256) != str(draft["plan_sha256"]):
            raise DashboardServiceError(409, "Sweep plan hash is stale")

    @staticmethod
    def _saved_plan(draft: Mapping[str, Any]) -> ResolvedPlan:
        try:
            return verify_plan(json.dumps(draft["plan"], ensure_ascii=False))
        except (KeyError, TypeError, ValueError) as exc:
            raise DashboardServiceError(409, "Saved sweep plan failed integrity verification") from exc

    def _claim_operation(self, sweep_id: str, operation: str, payload: Any) -> tuple[dict[str, Any], bool]:
        raw = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
        fingerprint = _fingerprint(raw)
        key = f"{operation}:{raw['idempotency_key']}"
        replay = False
        def claim(value: dict[str, Any]) -> None:
            nonlocal replay
            operations = value.setdefault("operations", {})
            existing = operations.get(key)
            if existing:
                if existing.get("fingerprint") != fingerprint:
                    raise DashboardServiceError(409, "Idempotency key was used with another payload")
                replay = True
                return
            if len(operations) >= 1000:
                raise DashboardServiceError(429, "Sweep idempotency history is full")
            operations[key] = {"fingerprint": fingerprint, "state": "claimed"}
            value["updated_at_unix"] = time.time()
        return self.drafts.update(sweep_id, claim), replay

    def _complete_operation(self, sweep_id: str, operation: str, key: str) -> None:
        def complete(value: dict[str, Any]) -> None:
            record = value.get("operations", {}).get(f"{operation}:{key}")
            if record:
                record["state"] = "completed"
                value["updated_at_unix"] = time.time()
        self.drafts.update(sweep_id, complete)

    def _supervisor(
        self, sweep_id: str, *, approved_plan: ResolvedPlan | None = None
    ) -> SweepSupervisor:
        prompts = self.drafts.prompts(sweep_id)
        approved_available = approved_plan is not None
        def provide(plan: ResolvedPlan, trial: Any) -> str:
            cell = next(item for item in plan.cells if item.cell_id == trial.cell_id)
            try:
                exact = f"{cell.condition.model_ref}::{cell.condition.prompt_ref}"
                return prompts[exact] if exact in prompts else prompts[f"*::{cell.condition.prompt_ref}"]
            except KeyError as exc:
                raise SweepStateError("private prompt variant is missing") from exc
        def drift(plan: ResolvedPlan, _trial: Any) -> Mapping[str, Any]:
            nonlocal approved_available
            if approved_available and approved_plan is not None:
                approved_available = False
                if approved_plan.to_json() == plan.to_json():
                    return {"blocking_issues": []}
            try:
                draft = self.drafts.read(sweep_id)
                fresh, _ = self._resolve(self._input_request(draft), refresh=True)
            except Exception as exc:
                return {"blocking_issues": [{"code": "PREFLIGHT_UNAVAILABLE", "error_type": type(exc).__name__}]}
            if fresh.to_json() != plan.to_json():
                return {"blocking_issues": [{"code": "SAVED_PLAN_DRIFT", "observed_plan_sha256": fresh.plan_sha256}]}
            return {"blocking_issues": []}
        return self.supervisor_factory(provide, drift)

    def _tick(self, sweep_id: str) -> dict[str, Any]:
        try:
            existing = self.runs.read(sweep_id)
        except SweepStateError:
            existing = None
        if existing is not None and existing.get("status") in TERMINAL:
            if self._scrub_required(sweep_id):
                self.drafts.scrub_prompts(sweep_id)
            return existing
        try:
            value = self._supervisor(sweep_id).tick(sweep_id)
        except (SweepStateError, ValueError) as exc:
            raise DashboardServiceError(409, "Sweep lifecycle could not advance") from exc
        if value.get("status") in TERMINAL:
            if self._scrub_required(sweep_id):
                self.drafts.scrub_prompts(sweep_id)
        return value

    def _scrub_required(self, sweep_id: str) -> bool:
        draft = self.drafts.read(sweep_id)
        try:
            plan = self._saved_plan(draft)
        except DashboardServiceError:
            return True
        return any(not cell.condition.persist_prompt for cell in plan.cells)

    def start(self, sweep_id: str, payload: Any) -> dict[str, Any]:
        draft, replay = self._claim_operation(sweep_id, "start", payload)
        self._check_plan(draft, payload)
        saved_plan = self._saved_plan(draft)
        if replay:
            try:
                manifest = self.runs.read(sweep_id)
            except SweepStateError:
                manifest = None
            if manifest is not None:
                return {"sweep": manifest, "idempotent_replay": True}
        fresh_plan, _ = self._resolve(self._input_request(draft), refresh=True)
        if fresh_plan.plan_sha256 != saved_plan.plan_sha256 or fresh_plan.to_json() != saved_plan.to_json():
            raise DashboardServiceError(409, "Saved sweep plan is stale against fresh preflight")
        runner = self._supervisor(sweep_id, approved_plan=fresh_plan)
        try:
            manifest = runner.create(sweep_id, saved_plan)
        except SweepStateError:
            try:
                manifest = self.runs.read(sweep_id)
            except SweepStateError as exc:
                raise DashboardServiceError(409, "Sweep Start could not be recovered") from exc
            if manifest.get("plan_sha256") != saved_plan.plan_sha256:
                raise DashboardServiceError(409, "Sweep ID already has another running plan")
        if manifest.get("status") not in TERMINAL:
            try:
                manifest = runner.tick(sweep_id)
            except (SweepStateError, ValueError) as exc:
                try:
                    manifest = self.runs.read(sweep_id)
                except SweepStateError:
                    raise DashboardServiceError(409, "Sweep lifecycle could not advance") from exc
                if manifest.get("plan_sha256") != saved_plan.plan_sha256:
                    raise DashboardServiceError(409, "Sweep lifecycle could not advance") from exc
                replay = True
        if manifest.get("status") in TERMINAL and self._scrub_required(sweep_id):
            self.drafts.scrub_prompts(sweep_id)
        self.drafts.update(sweep_id, lambda value: value.update({"status": "started", "updated_at_unix": time.time()}))
        self._complete_operation(sweep_id, "start", payload.idempotency_key)
        return {"sweep": manifest, "idempotent_replay": replay}

    def _lifecycle(self, sweep_id: str, operation: str, payload: Any, action: Callable[[SweepSupervisor], dict[str, Any]]) -> dict[str, Any]:
        draft, replay = self._claim_operation(sweep_id, operation, payload)
        self._check_plan(draft, payload)
        if replay:
            return {"sweep": self.get(sweep_id)["sweep"], "idempotent_replay": True}
        try:
            value = action(self._supervisor(sweep_id))
        except (SweepStateError, ValueError) as exc:
            raise DashboardServiceError(409, "Sweep lifecycle request conflicts with current state") from exc
        self._complete_operation(sweep_id, operation, payload.idempotency_key)
        return {"sweep": value, "idempotent_replay": False}

    def pause(self, sweep_id: str, payload: Any) -> dict[str, Any]:
        return self._lifecycle(sweep_id, "pause", payload, lambda runner: runner.pause(sweep_id, reason=payload.reason))

    def resume(self, sweep_id: str, payload: Any) -> dict[str, Any]:
        draft = self.drafts.read(sweep_id)
        self._check_plan(draft, payload)
        fresh, _ = self._resolve(self._input_request(draft), refresh=True)
        if fresh.to_json() != self._saved_plan(draft).to_json():
            raise DashboardServiceError(409, "Saved sweep plan is stale against fresh preflight")
        return self._lifecycle(sweep_id, "resume", payload, lambda runner: runner.resume(sweep_id))

    def cancel(self, sweep_id: str, payload: Any) -> dict[str, Any]:
        return self._lifecycle(sweep_id, "cancel", payload, lambda runner: runner.cancel(sweep_id, reason=payload.reason))

    def retry(self, sweep_id: str, trial_id: str, payload: Any) -> dict[str, Any]:
        draft = self.drafts.read(sweep_id)
        self._check_plan(draft, payload)
        fresh, _ = self._resolve(self._input_request(draft), refresh=True)
        if fresh.to_json() != self._saved_plan(draft).to_json():
            raise DashboardServiceError(409, "Saved sweep plan is stale against fresh preflight")
        return self._lifecycle(
            sweep_id, f"retry:{trial_id}", payload,
            lambda runner: runner.retry(sweep_id, trial_id, reason=payload.reason),
        )

    def get(self, sweep_id: str) -> dict[str, Any]:
        draft = self.drafts.read(sweep_id)
        try:
            sweep = self._tick(sweep_id)
        except DashboardServiceError as exc:
            if exc.status_code != 409 or draft.get("status") != "draft":
                raise
            sweep = None
        return {"draft": self._public_draft(draft), "sweep": sweep}

    def list(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > 100:
            raise DashboardServiceError(400, "Invalid sweep page bounds")
        values = self.drafts.list(offset=offset, limit=limit)
        return {"sweeps": [self._public_draft(item) for item in values], "offset": offset, "limit": min(limit, 100)}

    def events(self, sweep_id: str, *, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        self.drafts.read(sweep_id)
        if cursor < 0 or limit < 1 or limit > 200:
            raise DashboardServiceError(400, "Invalid event cursor or page size")
        values = self.runs.read_events(sweep_id)
        if cursor > len(values):
            raise DashboardServiceError(
                409,
                {"code": "EVENT_CURSOR_AHEAD", "recovery_cursor": len(values)},
            )
        page = values[cursor:cursor + limit]
        return {"sweep_id": sweep_id, "events": page, "cursor": cursor, "next_cursor": cursor + len(page), "has_more": cursor + len(page) < len(values)}

    def event_stream(self, sweep_id: str, *, cursor: int = 0) -> Iterator[str]:
        self.drafts.read(sweep_id)
        position = max(0, cursor)
        while True:
            page = self.events(sweep_id, cursor=position, limit=100)
            if page["events"]:
                for event in page["events"]:
                    position += 1
                    yield f"id: {position}\nevent: sweep\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                yield f": cursor={position} keepalive\n\n"
                time.sleep(1.0)

    def results(self, sweep_id: str) -> dict[str, Any]:
        try:
            value = self.runs.read(sweep_id)
        except SweepStateError as exc:
            raise DashboardServiceError(409, "Sweep has not started") from exc
        plan = verify_plan(json.dumps(value["plan_snapshot"], ensure_ascii=False))
        cells = {cell.cell_id: cell for cell in plan.cells}
        trials = []
        for trial in value.get("trials") or []:
            cell = cells.get(str(trial.get("cell_id") or ""))
            failure = None
            if trial.get("failure_code"):
                failure = {
                    "stage": "sweep_attempt",
                    "code": trial.get("failure_code"),
                    "trial_id": trial.get("trial_id"),
                    "cell_id": trial.get("cell_id"),
                    "nodes": list(cell.condition.worker_ids) if cell else [],
                    "model_id": cell.model.model_id if cell and cell.model else None,
                    "condition": {
                        "n_ctx": cell.condition.n_ctx,
                        "concurrency": cell.condition.concurrency,
                        "execution_strategy": cell.condition.execution_strategy,
                    } if cell else None,
                    "solutions": [
                        "Review the saved plan evidence and the selected trial event journal before manual retry."
                    ],
                }
            trials.append({
                "trial_id": trial.get("trial_id"), "cell_id": trial.get("cell_id"),
                "status": trial.get("status"), "official_attempt_id": trial.get("official_attempt_id"),
                "failure_code": trial.get("failure_code"),
                "failure": failure,
                "attempts": [
                    {key: attempt.get(key) for key in (
                        "attempt_id", "status", "backend_job_id", "run_id", "failure_code",
                        "cleanup_status", "retry_of_attempt_id", "retry_reason",
                    )}
                    for attempt in trial.get("attempts") or []
                ],
            })
        return {"sweep_id": sweep_id, "plan_sha256": value.get("plan_sha256"), "status": value.get("status"), "coverage": value.get("coverage"), "trials": trials}

    def export_plan(self, sweep_id: str) -> dict[str, Any]:
        draft = self.drafts.read(sweep_id)
        return self._saved_plan(draft).to_dict()


__all__ = ["SweepDraftRepository", "SweepService"]
