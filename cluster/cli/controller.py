"""Portable lifecycle command for the local Dashboard/Controller process."""

from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import fcntl
import psutil

from cluster.domain.controller import ControllerConfig, ControllerPlatform
from cluster.integrations.runtime_layout import repository_root, resolve_runtime_paths


COMMAND_NAME = "llm-cluster"
PROJECT_NAME = "llm-cluster-benchmark"
ALLOWED_ACTIONS = frozenset({"start", "stop", "restart", "status", "logs"})
LOCK_TIMEOUT_SECONDS = 30.0
START_TIMEOUT_SECONDS = 20.0
TERM_TIMEOUT_SECONDS = 10.0
KILL_TIMEOUT_SECONDS = 3.0
LOG_LINES = 200
_SPAWNED_CHILDREN: dict[int, subprocess.Popen[bytes]] = {}


class LauncherError(RuntimeError):
    """A safe and actionable Controller lifecycle failure."""


@dataclass(frozen=True)
class ProcessSpec:
    """The one fixed local process that this command is allowed to manage."""

    module: str
    host: str
    port: int
    health_path: str
    pid_file: Path
    identity_file: Path
    log_file: Path
    python_bin: Path
    project_root: Path

    @property
    def argv(self) -> tuple[str, ...]:
        return (
            str(self.python_bin),
            "-m",
            "uvicorn",
            f"{self.module}:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--no-access-log",
        )

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


@dataclass(frozen=True)
class ProcessIdentity:
    """Recorded process identity; PID alone is never enough to signal it."""

    pid: int
    executable: str
    cwd: str
    argv: tuple[str, ...]
    started_at: float
    user: str

    def as_record(self) -> dict[str, object]:
        return {
            "version": 1,
            "pid": self.pid,
            "executable": self.executable,
            "cwd": self.cwd,
            "argv": list(self.argv),
            "started_at": self.started_at,
            "user": self.user,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }


def _runtime_spec() -> ProcessSpec:
    root = repository_root()
    paths = resolve_runtime_paths()
    runtime_dir = paths.layout.controller_runtime_dir
    return ProcessSpec(
        module="cluster.dashboard.app",
        host="127.0.0.1",
        port=8080,
        health_path="/dashboard/health",
        pid_file=runtime_dir / "dashboard.pid",
        identity_file=runtime_dir / "dashboard.identity.json",
        log_file=runtime_dir / "dashboard.log",
        python_bin=root / ".venv" / "bin" / "python",
        project_root=root,
    )


def _controller_config(spec: ProcessSpec) -> ControllerConfig:
    paths = resolve_runtime_paths()
    return ControllerConfig(
        host=spec.host,
        runtime_dir=paths.layout.controller_runtime_dir,
        results_dir=paths.results_dir,
        platform=ControllerPlatform.MACOS,
    )


def _usage() -> str:
    return f"Usage: {COMMAND_NAME} start|stop|restart|status|logs"


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _prepare_runtime(spec: ProcessSpec) -> None:
    spec.pid_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    spec.pid_file.parent.chmod(0o700)
    for path in (spec.pid_file, spec.identity_file, spec.log_file):
        if path.exists() and path.is_file():
            path.chmod(0o600)


def _acquire_lock(spec: ProcessSpec) -> int:
    lock_path = spec.pid_file.parent / f"{COMMAND_NAME}.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.fchmod(descriptor, 0o600)
    os.set_inheritable(descriptor, False)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise LauncherError("another llm-cluster operation is still running")
            time.sleep(0.1)


def _read_pid(path: Path) -> Optional[int]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not value.isascii() or not value.isdigit() or int(value) <= 1:
        raise LauncherError(f"refusing malformed PID file: {path}")
    return int(value)


def _read_identity(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise LauncherError(f"refusing invalid identity file: {path}") from exc
    if not isinstance(value, dict):
        raise LauncherError(f"refusing invalid identity file: {path}")
    return value


def _clear_records(spec: ProcessSpec) -> None:
    for path in (spec.pid_file, spec.identity_file):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _process_identity(process: psutil.Process) -> ProcessIdentity:
    try:
        return ProcessIdentity(
            pid=process.pid,
            executable=str(Path(process.exe()).resolve()),
            cwd=str(Path(process.cwd()).resolve()),
            argv=tuple(process.cmdline()),
            started_at=process.create_time(),
            user=process.username(),
        )
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
        raise LauncherError(f"cannot inspect dashboard process {process.pid}: {exc}") from exc


def _expected_identity_matches(spec: ProcessSpec, identity: ProcessIdentity) -> bool:
    try:
        expected_executable = str(spec.python_bin.resolve(strict=True))
        current_executable = str(Path(psutil.Process().exe()).resolve())
    except OSError:
        return False
    if not identity.argv:
        return False
    try:
        argv_executable = str(Path(identity.argv[0]).resolve(strict=True))
    except OSError:
        return False
    return (
        identity.executable in {expected_executable, current_executable}
        and argv_executable == identity.executable
        and identity.cwd == str(spec.project_root.resolve())
        and identity.argv[1:] == spec.argv[1:]
        and identity.user == psutil.Process().username()
    )


def _record_matches(record: Mapping[str, Any], identity: ProcessIdentity) -> bool:
    return (
        record.get("version") == 1
        and record.get("pid") == identity.pid
        and record.get("executable") == identity.executable
        and record.get("cwd") == identity.cwd
        and record.get("argv") == list(identity.argv)
        and isinstance(record.get("started_at"), (int, float))
        and abs(float(record["started_at"]) - identity.started_at) < 0.01
        and record.get("user") == identity.user
    )


def _write_records(spec: ProcessSpec, identity: ProcessIdentity) -> None:
    _atomic_write(spec.pid_file, f"{identity.pid}\n")
    _atomic_write(spec.identity_file, json.dumps(identity.as_record(), indent=2, sort_keys=True) + "\n")


def _locate(spec: ProcessSpec) -> Optional[tuple[psutil.Process, ProcessIdentity]]:
    pid = _read_pid(spec.pid_file)
    if pid is None:
        if spec.identity_file.exists():
            raise LauncherError("identity record exists without PID; refusing to manage an unknown process")
        return None
    record = _read_identity(spec.identity_file)
    if record is None:
        raise LauncherError("PID exists without identity record; refusing to signal it")
    try:
        process = psutil.Process(pid)
        identity = _process_identity(process)
    except psutil.NoSuchProcess:
        _clear_records(spec)
        return None
    if not _expected_identity_matches(spec, identity):
        raise LauncherError("recorded PID is not this Controller dashboard; no signal was sent")
    if not _record_matches(record, identity):
        raise LauncherError("dashboard identity record does not match; no signal was sent")
    return process, identity


def _health_ok(spec: ProcessSpec) -> bool:
    request = urllib.request.Request(f"http://127.0.0.1:{spec.port}{spec.health_path}")
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return isinstance(payload, dict) and payload.get("ok") is True and payload.get("service") == "cluster-dashboard"


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _assert_same_process(process: psutil.Process, identity: ProcessIdentity, spec: ProcessSpec) -> None:
    observed = _process_identity(process)
    if observed != identity or not _expected_identity_matches(spec, observed):
        raise LauncherError("dashboard process identity changed; no signal was sent")


def _terminate(spec: ProcessSpec, process: psutil.Process, identity: ProcessIdentity) -> None:
    _assert_same_process(process, identity, spec)
    process.terminate()
    try:
        process.wait(timeout=TERM_TIMEOUT_SECONDS)
    except psutil.TimeoutExpired:
        _assert_same_process(process, identity, spec)
        process.kill()
        try:
            process.wait(timeout=KILL_TIMEOUT_SECONDS)
        except psutil.TimeoutExpired as exc:
            raise LauncherError(f"dashboard PID {identity.pid} did not stop") from exc
    child = _SPAWNED_CHILDREN.pop(identity.pid, None)
    if child is not None:
        child.wait(timeout=0)
    _clear_records(spec)


def _terminate_failed_child(child: subprocess.Popen[bytes]) -> None:
    """Clean up only the ``Popen`` child created by this start attempt."""
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=TERM_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    child.kill()
    try:
        child.wait(timeout=KILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise LauncherError(f"failed dashboard child PID {child.pid} remained alive") from exc


def _start(spec: ProcessSpec) -> tuple[ProcessIdentity, bool]:
    _prepare_runtime(spec)
    existing = _locate(spec)
    if existing is not None:
        _, identity = existing
        if not _health_ok(spec):
            raise LauncherError(f"dashboard PID {identity.pid} is unhealthy; use restart")
        print(f"[{PROJECT_NAME}] dashboard already running (PID={identity.pid})")
        return identity, False
    if _port_is_open(spec.port):
        raise LauncherError(f"port {spec.port} is in use by an untracked process; nothing was changed")
    if not spec.python_bin.is_file() or not os.access(spec.python_bin, os.X_OK):
        raise LauncherError(f"Controller virtual environment is missing: {spec.python_bin}. Run ./scripts/setup-controller")

    descriptor = os.open(spec.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "ab", buffering=0) as log_handle:
        child = subprocess.Popen(
            spec.argv,
            cwd=spec.project_root,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    _SPAWNED_CHILDREN[child.pid] = child

    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    error = "process identity was not available"
    while time.monotonic() < deadline:
        try:
            process = psutil.Process(child.pid)
            identity = _process_identity(process)
            if _expected_identity_matches(spec, identity):
                _write_records(spec, identity)
                if _health_ok(spec):
                    print(f"[{PROJECT_NAME}] dashboard started (PID={identity.pid})")
                    return identity, True
            else:
                error = (
                    "process identity differs from the fixed Controller service "
                    f"(exe={identity.executable}, cwd={identity.cwd}, argv={identity.argv})"
                )
        except (LauncherError, psutil.NoSuchProcess) as exc:
            error = str(exc)
        if child.poll() is not None:
            error = f"process exited with status {child.returncode}"
            break
        time.sleep(0.2)

    try:
        _terminate_failed_child(child)
    finally:
        _SPAWNED_CHILDREN.pop(child.pid, None)
        _clear_records(spec)
    raise LauncherError(f"dashboard failed to start: {error}; see {spec.log_file}")


def _stop(spec: ProcessSpec) -> Optional[ProcessIdentity]:
    existing = _locate(spec)
    if existing is None:
        print(f"[{PROJECT_NAME}] dashboard already stopped")
        return None
    process, identity = existing
    _terminate(spec, process, identity)
    print(f"[{PROJECT_NAME}] dashboard stopped (PID={identity.pid})")
    return identity


def _tail(path: Path, limit: int) -> Iterable[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            data = bytearray()
            while position > 0 and data.count(b"\n") <= limit:
                step = min(8192, position)
                position -= step
                handle.seek(position)
                data[:0] = handle.read(step)
    except FileNotFoundError:
        return ()
    return tuple(data.decode("utf-8", errors="replace").splitlines()[-limit:])


def _show_status(spec: ProcessSpec) -> int:
    existing = _locate(spec)
    if existing is None:
        print(f"[{PROJECT_NAME}] dashboard: stopped (port={spec.port})")
        print(f"[{PROJECT_NAME}] URL: {spec.url}")
        return 3
    _, identity = existing
    state = "running" if _health_ok(spec) else "unhealthy"
    print(f"[{PROJECT_NAME}] dashboard: {state} (PID={identity.pid}, port={spec.port})")
    print(f"[{PROJECT_NAME}] URL: {spec.url}")
    return 0 if state == "running" else 3


def _show_logs(spec: ProcessSpec) -> None:
    print(f"===== dashboard (last {LOG_LINES} lines) =====")
    lines = _tail(spec.log_file, LOG_LINES)
    if not lines:
        print(f"[no log file: {spec.log_file}]")
        return
    for line in lines:
        print(line)


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2 or argv[1] not in ALLOWED_ACTIONS:
        print(_usage(), file=sys.stderr)
        return 64
    spec = _runtime_spec()
    try:
        _controller_config(spec)
        _prepare_runtime(spec)
        lock = _acquire_lock(spec)
        try:
            action = argv[1]
            if action == "start":
                _start(spec)
                print(f"[{PROJECT_NAME}] service started")
                print(f"[{PROJECT_NAME}] URL: {spec.url}")
                return 0
            if action == "stop":
                _stop(spec)
                print(f"[{PROJECT_NAME}] service stopped")
                return 0
            if action == "restart":
                previous = _stop(spec)
                current, _ = _start(spec)
                if previous is not None and current.pid == previous.pid and current.started_at == previous.started_at:
                    raise LauncherError("dashboard generation did not change during restart")
                print(f"[{PROJECT_NAME}] service started")
                print(f"[{PROJECT_NAME}] URL: {spec.url}")
                return 0
            if action == "status":
                return _show_status(spec)
            _show_logs(spec)
            return 0
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
    except (LauncherError, OSError, ValueError) as exc:
        print(f"[{PROJECT_NAME}] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
