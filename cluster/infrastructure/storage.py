"""Filesystem storage repositories with legacy-compatible file formats.

This module is deliberately the persistence boundary.  It owns directory
creation, atomic replacement, permissions, corrupted JSON signalling, and the
legacy CSV/JSON filenames.  Domain and application logic should exchange
plain mappings or typed values rather than opening files directly.
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Protocol, Sequence

from cluster.domain.identifiers import (
    validate_experiment_id,
    validate_node_id,
    validate_run_id,
    validate_suite_id,
)


JsonObject = Dict[str, Any]


class StorageCorruptionError(ValueError):
    """A persisted document exists but is not a valid JSON object."""


class InventoryRepository(Protocol):
    def read_rows(self) -> List[Dict[str, str]]: ...

    def write_rows(self, rows: Sequence[Mapping[str, Any]]) -> None: ...


class SettingsRepository(Protocol):
    def read(self) -> JsonObject: ...

    def write(self, settings: Mapping[str, Any]) -> None: ...


class EnvironmentReportRepository(Protocol):
    def read(self, node_name: str) -> JsonObject: ...

    def write(self, node_name: str, report: Mapping[str, Any]) -> None: ...

    def delete(self, node_name: str) -> None: ...


class ExperimentRepository(Protocol):
    def read(self, experiment_id: str) -> JsonObject: ...

    def write(self, experiment_id: str, definition: Mapping[str, Any]) -> None: ...

    def list(self) -> List[JsonObject]: ...


class RunRepository(Protocol):
    def create(self, run_id: str, config: Mapping[str, Any]) -> Path: ...

    def append_event(self, run_id: str, event: Mapping[str, Any]) -> None: ...

    def write_requests(self, run_id: str, records: Sequence[Mapping[str, Any]]) -> None: ...

    def append_response(self, run_id: str, response: Mapping[str, Any]) -> None: ...

    def read_responses(self, run_id: str) -> List[JsonObject]: ...

    def write_summary(self, run_id: str, summary: Mapping[str, Any]) -> None: ...

    def read_summary(self, run_id: str) -> JsonObject: ...

    def list_summaries(self, limit: int = 100) -> List[JsonObject]: ...

    def delete(self, run_id: str) -> Path: ...


class SuiteRepository(Protocol):
    def read(self, suite_id: str) -> JsonObject: ...

    def write(self, suite_id: str, summary: Mapping[str, Any]) -> None: ...

    def list(self, limit: int = 100) -> List[JsonObject]: ...

    def delete(self, suite_id: str) -> Path: ...


class JobRepository(Protocol):
    def read(self, job_id: str) -> JsonObject: ...

    def write(self, job_id: str, job: Mapping[str, Any]) -> None: ...

    def list(self, limit: int = 100) -> List[JsonObject]: ...

    def update(self, job_id: str, mutate: Callable[[JsonObject], None]) -> JsonObject: ...

    def append_event(self, job_id: str, event: Mapping[str, Any]) -> None: ...

    def read_events(self, job_id: str, limit: int = 100) -> List[JsonObject]: ...


def _existing_or_default_mode(path: Path, default_mode: int) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return default_mode


def _ensure_directory(path: Path, mode: Optional[int] = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if mode is not None:
        path.chmod(mode)


def atomic_write_text(path: Path, text: str, *, default_mode: int = 0o600) -> None:
    """Durably replace one text file without exposing a partial document."""
    _ensure_directory(path.parent)
    mode = _existing_or_default_mode(path, default_mode)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def read_json_object(path: Path) -> JsonObject:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StorageCorruptionError(f"Invalid JSON document: {path}") from exc
    except UnicodeDecodeError as exc:
        raise StorageCorruptionError(f"Invalid text encoding: {path}") from exc
    if not isinstance(raw, dict):
        raise StorageCorruptionError(f"JSON document must be an object: {path}")
    return raw


def write_json_object(path: Path, value: Mapping[str, Any], *, default_mode: int = 0o600) -> None:
    atomic_write_text(
        path,
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        default_mode=default_mode,
    )


class FilesystemInventoryRepository:
    """Legacy CSV inventory storage preserving the existing 10-column schema."""

    fieldnames = (
        "name",
        "role",
        "host",
        "user",
        "ssh_port",
        "api_port",
        "project_dir",
        "enabled",
        "identity_file",
        "platform",
    )

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read_rows(self) -> List[Dict[str, str]]:
        with self.path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = set(self.fieldnames[:8]).difference(reader.fieldnames or [])
            if missing:
                raise StorageCorruptionError(
                    "Inventory is missing columns: " + ", ".join(sorted(missing))
                )
            return [dict(row) for row in reader if (row.get("name") or "").strip()]

    def write_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        output = []
        for row in rows:
            normalized = {field: "" for field in self.fieldnames}
            for field in self.fieldnames:
                value = row.get(field, "")
                normalized[field] = "true" if field == "enabled" and value is True else (
                    "false" if field == "enabled" and value is False else str(value)
                )
            output.append(normalized)

        # csv.writer handles quoting semantics identically to the prior writer.
        from io import StringIO

        buffer = StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(self.fieldnames))
        writer.writeheader()
        writer.writerows(output)
        atomic_write_text(self.path, buffer.getvalue(), default_mode=0o600)


class FilesystemSettingsRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> JsonObject:
        return read_json_object(self.path)

    def write(self, settings: Mapping[str, Any]) -> None:
        write_json_object(self.path, settings, default_mode=0o600)


class FilesystemEnvironmentReportRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, node_name: str) -> Path:
        return self.directory / f"{validate_node_id(node_name)}.json"

    def read(self, node_name: str) -> JsonObject:
        return read_json_object(self._path(node_name))

    def write(self, node_name: str, report: Mapping[str, Any]) -> None:
        _ensure_directory(self.directory, mode=0o700)
        write_json_object(self._path(node_name), report, default_mode=0o600)

    def delete(self, node_name: str) -> None:
        try:
            self._path(node_name).unlink()
        except FileNotFoundError:
            pass


class _JsonDirectoryRepository:
    def __init__(
        self,
        directory: Path,
        *,
        default_mode: int = 0o644,
        directory_mode: Optional[int] = None,
    ) -> None:
        self.directory = Path(directory)
        self.default_mode = default_mode
        self.directory_mode = directory_mode

    def _read(self, identifier: str, validator: Any) -> JsonObject:
        return read_json_object(self.directory / f"{validator(identifier)}.json")

    def _write(self, identifier: str, value: Mapping[str, Any], validator: Any) -> None:
        _ensure_directory(self.directory, mode=self.directory_mode)
        write_json_object(
            self.directory / f"{validator(identifier)}.json",
            value,
            default_mode=self.default_mode,
        )

    def _list(self, limit: int) -> List[JsonObject]:
        if not self.directory.exists():
            return []
        values: List[JsonObject] = []
        paths = sorted(self.directory.glob("*.json"), reverse=True)
        for path in paths if limit <= 0 else paths[:limit]:
            try:
                values.append(read_json_object(path))
            except (OSError, StorageCorruptionError):
                continue
        return values


class FilesystemExperimentRepository(_JsonDirectoryRepository):
    def __init__(self, directory: Path) -> None:
        # Experiment definitions can contain research prompts.
        super().__init__(directory, default_mode=0o600, directory_mode=0o700)

    def read(self, experiment_id: str) -> JsonObject:
        return self._read(experiment_id, validate_experiment_id)

    def write(self, experiment_id: str, definition: Mapping[str, Any]) -> None:
        self._write(experiment_id, definition, validate_experiment_id)

    def list(self) -> List[JsonObject]:
        return self._list(0)


class FilesystemSuiteRepository(_JsonDirectoryRepository):
    def __init__(self, directory: Path) -> None:
        # Suite summaries can contain model and failure evidence.
        super().__init__(directory, default_mode=0o600, directory_mode=0o700)

    def read(self, suite_id: str) -> JsonObject:
        return self._read(suite_id, validate_suite_id)

    def write(self, suite_id: str, summary: Mapping[str, Any]) -> None:
        self._write(suite_id, summary, validate_suite_id)

    def list(self, limit: int = 100) -> List[JsonObject]:
        return self._list(limit)

    def delete(self, suite_id: str) -> Path:
        source = self.directory / f"{validate_suite_id(suite_id)}.json"
        if source.is_symlink():
            raise StorageCorruptionError("Suite artifact must not be a symbolic link")
        if not source.is_file():
            raise FileNotFoundError(source)
        trash = self.directory / "_trash"
        _ensure_directory(trash, mode=0o700)
        destination = trash / f"{source.stem}-{uuid.uuid4().hex}.json"
        os.replace(source, destination)
        destination.chmod(0o600)
        return destination


class FilesystemJobRepository(_JsonDirectoryRepository):
    """Private, process-safe durable registry and event journal for jobs."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory, default_mode=0o600, directory_mode=0o700)
        self._thread_lock = threading.RLock()

    def _event_path(self, job_id: str) -> Path:
        return self.directory / f"{validate_experiment_id(job_id)}.events.jsonl"

    @contextmanager
    def _locked(self, job_id: str) -> Iterator[None]:
        _ensure_directory(self.directory, mode=0o700)
        lock_path = self.directory / f".{validate_experiment_id(job_id)}.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
        try:
            with self._thread_lock:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def read(self, job_id: str) -> JsonObject:
        return self._read(job_id, validate_experiment_id)

    def write(self, job_id: str, job: Mapping[str, Any]) -> None:
        with self._locked(job_id):
            self._write(job_id, job, validate_experiment_id)

    def list(self, limit: int = 100) -> List[JsonObject]:
        return self._list(limit)

    def update(self, job_id: str, mutate: Callable[[JsonObject], None]) -> JsonObject:
        with self._locked(job_id):
            current = self._read(job_id, validate_experiment_id)
            mutate(current)
            self._write(job_id, current, validate_experiment_id)
            return current

    def append_event(self, job_id: str, event: Mapping[str, Any]) -> None:
        with self._locked(job_id):
            path = self._event_path(job_id)
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def read_events(self, job_id: str, limit: int = 100) -> List[JsonObject]:
        try:
            lines = self._event_path(job_id).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events: List[JsonObject] = []
        selected = lines if limit <= 0 else lines[-limit:]
        for line in selected:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
        return events


class FilesystemRunRepository:
    """Run-directory persistence preserving config/events/CSV/summary layout."""

    def __init__(self, results_dir: Path) -> None:
        self.results_dir = Path(results_dir)
        self._event_lock = threading.Lock()

    def _run_dir(self, run_id: str) -> Path:
        return self.results_dir / validate_run_id(run_id)

    def create(self, run_id: str, config: Mapping[str, Any]) -> Path:
        run_dir = self._run_dir(run_id)
        _ensure_directory(self.results_dir, mode=0o700)
        run_dir.mkdir(parents=False, exist_ok=False, mode=0o700)
        run_dir.chmod(0o700)
        write_json_object(run_dir / "config.json", config, default_mode=0o600)
        return run_dir

    def append_event(self, run_id: str, event: Mapping[str, Any]) -> None:
        path = self._run_dir(run_id) / "events.jsonl"
        with self._event_lock:
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(event), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def append_response(self, run_id: str, response: Mapping[str, Any]) -> None:
        """Durably journal a completed request before final CSV aggregation."""
        path = self._run_dir(run_id) / "responses.jsonl"
        with self._event_lock:
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(response), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def read_responses(self, run_id: str) -> List[JsonObject]:
        path = self._run_dir(run_id) / "responses.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        recovered: List[JsonObject] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                recovered.append(value)
        return recovered

    def write_requests(self, run_id: str, records: Sequence[Mapping[str, Any]]) -> None:
        if not records:
            return
        path = self._run_dir(run_id) / "requests.csv"
        # This legacy CSV is intentionally metric-only. Raw prompts/responses and
        # structured failures live in responses.jsonl without changing its schema.
        fieldnames = [
            "request_id", "logical_request_id", "scenario_id", "replica_index",
            "node", "assigned_node", "node_host", "started_at", "ok", "ttft_s",
            "e2e_s", "server_ttft_s", "server_generation_s", "generated_tokens",
            "tokens_per_s", "output_chars", "output_sha256", "error", "warmup",
        ]
        from io import StringIO

        buffer = StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
        atomic_write_text(path, buffer.getvalue(), default_mode=0o600)

    def write_summary(self, run_id: str, summary: Mapping[str, Any]) -> None:
        write_json_object(self._run_dir(run_id) / "summary.json", summary, default_mode=0o600)

    def read_summary(self, run_id: str) -> JsonObject:
        return read_json_object(self._run_dir(run_id) / "summary.json")

    def list_summaries(self, limit: int = 100) -> List[JsonObject]:
        if not self.results_dir.exists():
            return []
        values: List[JsonObject] = []
        paths = sorted(self.results_dir.glob("*/summary.json"), reverse=True)
        for path in paths[:limit]:
            try:
                values.append(read_json_object(path))
            except (OSError, StorageCorruptionError):
                continue
        return values

    def delete(self, run_id: str) -> Path:
        """Remove a run from active results by atomically moving it to private trash."""
        source = self._run_dir(run_id)
        if source.is_symlink():
            raise StorageCorruptionError("Run directory must not be a symbolic link")
        if not source.is_dir():
            raise FileNotFoundError(source)
        trash = self.results_dir / "_trash"
        _ensure_directory(trash, mode=0o700)
        destination = trash / f"{validate_run_id(run_id)}-{uuid.uuid4().hex}"
        os.replace(source, destination)
        destination.chmod(0o700)
        return destination


__all__ = [
    "EnvironmentReportRepository",
    "ExperimentRepository",
    "FilesystemEnvironmentReportRepository",
    "FilesystemExperimentRepository",
    "FilesystemInventoryRepository",
    "FilesystemJobRepository",
    "FilesystemRunRepository",
    "FilesystemSettingsRepository",
    "FilesystemSuiteRepository",
    "InventoryRepository",
    "JobRepository",
    "RunRepository",
    "SettingsRepository",
    "StorageCorruptionError",
    "SuiteRepository",
    "atomic_write_text",
    "read_json_object",
    "write_json_object",
]
