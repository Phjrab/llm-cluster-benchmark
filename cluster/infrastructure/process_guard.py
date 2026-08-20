#!/usr/bin/env python3
"""Identity-checked lifecycle guard for Python and native local services.

Shell launchers keep their established HOST/PORT interface, while this module
owns all process metadata and terminating signals.  A PID is never sufficient:
the executable, working directory, argv, creation time, and user must all match
both the fixed service specification and the private recorded identity. Native
services use the same contract with an exact, caller-supplied full argv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import psutil

from cluster.infrastructure.process import (
    ProcessIdentity,
    ProcessInspector,
    PsutilProcessInspector,
    can_signal,
)


RECORD_VERSION = 1
TERM_TIMEOUT_SECONDS = 10.0
KILL_TIMEOUT_SECONDS = 3.0
RECORD_TIMEOUT_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.05
EXIT_STOPPED = 3
EXIT_UNSAFE = 4
EXIT_NOT_LISTENING = 5
_MODULE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class ProcessGuardError(RuntimeError):
    """The requested lifecycle action could not be proven safe."""


@dataclass(frozen=True)
class ServiceSpec:
    pid_file: Path
    identity_file: Path
    cwd: Path
    python_bin: Path
    module: str
    host: str
    port: int
    user: str

    @classmethod
    def create(
        cls,
        *,
        pid_file: Path,
        identity_file: Path,
        cwd: Path,
        python_bin: Path,
        module: str,
        host: str,
        port: int,
        user: Optional[str] = None,
    ) -> "ServiceSpec":
        resolved_cwd = Path(cwd).resolve(strict=True)
        resolved_python = Path(python_bin).resolve(strict=True)
        if not resolved_cwd.is_dir():
            raise ProcessGuardError(f"service cwd is not a directory: {resolved_cwd}")
        if not resolved_python.is_file() or not os.access(resolved_python, os.X_OK):
            raise ProcessGuardError(f"service Python is not executable: {resolved_python}")
        if not _MODULE_PATTERN.fullmatch(module):
            raise ProcessGuardError(f"invalid service module: {module}")
        if not host or any(ord(character) < 32 for character in host):
            raise ProcessGuardError("invalid service host")
        if not 1 <= port <= 65535:
            raise ProcessGuardError("service port must be between 1 and 65535")

        runtime_path = resolved_cwd / ".run"
        if runtime_path.is_symlink():
            raise ProcessGuardError("service runtime directory must not be a symlink")
        runtime_root = runtime_path.resolve()
        normalized_pid = _validated_metadata_path(pid_file, runtime_root, "PID")
        normalized_identity = _validated_metadata_path(
            identity_file, runtime_root, "identity"
        )
        if normalized_pid == normalized_identity:
            raise ProcessGuardError("PID and identity paths must differ")
        expected_user = user or psutil.Process().username()
        return cls(
            normalized_pid,
            normalized_identity,
            resolved_cwd,
            resolved_python,
            module,
            host,
            port,
            expected_user,
        )

    @property
    def module_argv_tail(self) -> tuple[str, ...]:
        return (
            "-m",
            "uvicorn",
            f"{self.module}:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
        )

    @property
    def console_argv_tail(self) -> tuple[str, ...]:
        # Compatibility for a PID-only process created by the original root
        # launcher, whose uvicorn console script used the same venv Python.
        return (
            str(self.cwd / ".venv" / "bin" / "uvicorn"),
            f"{self.module}:app",
            "--host",
            self.host,
            "--port",
            str(self.port),
        )

    def service_document(self) -> dict[str, object]:
        return {
            "cwd": str(self.cwd),
            "python": str(self.python_bin),
            "module": self.module,
            "host": self.host,
            "port": self.port,
            "user": self.user,
        }

    def matches_service(self, identity: ProcessIdentity) -> bool:
        if identity.pid <= 1 or not identity.argv:
            return False
        try:
            executable = str(Path(identity.executable).resolve(strict=True))
            argv_executable = str(Path(identity.argv[0]).resolve(strict=True))
        except OSError:
            return False
        return (
            executable == str(self.python_bin)
            and argv_executable == executable
            and identity.cwd == str(self.cwd)
            and identity.user == self.user
            and identity.argv[1:]
            in {self.module_argv_tail, self.console_argv_tail}
        )


@dataclass(frozen=True)
class NativeServiceSpec:
    """Fixed native executable and full argv accepted by :class:`ProcessGuard`."""

    pid_file: Path
    identity_file: Path
    cwd: Path
    executable: Path
    argv: tuple[str, ...]
    user: str

    @classmethod
    def create(
        cls,
        *,
        pid_file: Path,
        identity_file: Path,
        cwd: Path,
        executable: Path,
        argv: Sequence[str],
        user: Optional[str] = None,
    ) -> "NativeServiceSpec":
        if not Path(executable).is_absolute():
            raise ProcessGuardError("native service executable must be absolute")
        resolved_cwd = Path(cwd).resolve(strict=True)
        resolved_executable = Path(executable).resolve(strict=True)
        if not resolved_cwd.is_dir():
            raise ProcessGuardError(f"service cwd is not a directory: {resolved_cwd}")
        if not resolved_executable.is_file() or not os.access(resolved_executable, os.X_OK):
            raise ProcessGuardError(
                f"native service executable is not executable: {resolved_executable}"
            )
        if (
            isinstance(argv, (str, bytes))
            or not argv
            or any(not isinstance(argument, str) for argument in argv)
        ):
            raise ProcessGuardError("native service argv must be a non-empty string list")
        normalized_argv = tuple(argv)
        if any("\x00" in argument for argument in normalized_argv):
            raise ProcessGuardError("native service argv contains a NUL byte")
        if not Path(normalized_argv[0]).is_absolute():
            raise ProcessGuardError("native argv[0] must be absolute")
        try:
            argv_executable = Path(normalized_argv[0]).resolve(strict=True)
        except OSError as exc:
            raise ProcessGuardError("native argv[0] is unavailable") from exc
        if argv_executable != resolved_executable:
            raise ProcessGuardError(
                "native argv[0] must resolve to the configured executable"
            )

        runtime_path = resolved_cwd / ".run"
        if runtime_path.is_symlink():
            raise ProcessGuardError("service runtime directory must not be a symlink")
        runtime_root = runtime_path.resolve()
        normalized_pid = _validated_metadata_path(pid_file, runtime_root, "PID")
        normalized_identity = _validated_metadata_path(
            identity_file, runtime_root, "identity"
        )
        if normalized_pid == normalized_identity:
            raise ProcessGuardError("PID and identity paths must differ")
        return cls(
            normalized_pid,
            normalized_identity,
            resolved_cwd,
            resolved_executable,
            normalized_argv,
            user or psutil.Process().username(),
        )

    @classmethod
    def from_record(
        cls,
        *,
        pid_file: Path,
        identity_file: Path,
        cwd: Path,
        executable: Path,
        user: Optional[str] = None,
    ) -> "NativeServiceSpec":
        """Rebuild an exact spec only from a private, complete identity record.

        This path deliberately cannot adopt a legacy PID-only process because
        stop/status callers without the original start arguments cannot prove
        its full argv.
        """

        resolved_cwd = Path(cwd).resolve(strict=True)
        runtime_path = resolved_cwd / ".run"
        if runtime_path.is_symlink():
            raise ProcessGuardError("service runtime directory must not be a symlink")
        normalized_identity = _validated_metadata_path(
            identity_file, runtime_path.resolve(), "identity"
        )
        record = _read_identity_record(normalized_identity)
        if record is None:
            raise ProcessGuardError(
                "native identity metadata is required to recover the exact argv"
            )
        service = record.get("service")
        if not isinstance(service, Mapping) or service.get("kind") != "native":
            raise ProcessGuardError("identity metadata is not for a native service")
        recorded_argv = service.get("argv")
        if not isinstance(recorded_argv, list) or any(
            not isinstance(argument, str) for argument in recorded_argv
        ):
            raise ProcessGuardError("native identity metadata has no exact argv")
        spec = cls.create(
            pid_file=pid_file,
            identity_file=identity_file,
            cwd=resolved_cwd,
            executable=executable,
            argv=recorded_argv,
            user=user,
        )
        if service != spec.service_document():
            raise ProcessGuardError("native service metadata does not match this host")
        return spec

    def service_document(self) -> dict[str, object]:
        return {
            "kind": "native",
            "cwd": str(self.cwd),
            "executable": str(self.executable),
            "argv": list(self.argv),
            "user": self.user,
        }

    def matches_service(self, identity: ProcessIdentity) -> bool:
        if identity.pid <= 1 or not identity.argv or identity.argv != self.argv:
            return False
        try:
            executable = Path(identity.executable).resolve(strict=True)
            argv_executable = Path(identity.argv[0]).resolve(strict=True)
        except OSError:
            return False
        return (
            executable == self.executable
            and argv_executable == self.executable
            and identity.cwd == str(self.cwd)
            and identity.user == self.user
        )


def _validated_metadata_path(path: Path, runtime_root: Path, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ProcessGuardError(f"{label} path must be absolute")
    if candidate.is_symlink():
        raise ProcessGuardError(f"{label} path must not be a symlink")
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(runtime_root)
    except ValueError as exc:
        raise ProcessGuardError(
            f"{label} path must remain below {runtime_root}"
        ) from exc
    return resolved_parent / candidate.name


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if path.is_symlink():
        raise ProcessGuardError(f"refusing symlink metadata path: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(4)}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _prepare_metadata(spec: ServiceSpec) -> None:
    for parent in {spec.pid_file.parent, spec.identity_file.parent}:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent.chmod(0o700)
    for path in (spec.pid_file, spec.identity_file):
        if path.is_symlink():
            raise ProcessGuardError(f"refusing symlink metadata path: {path}")
        if path.exists():
            if not path.is_file():
                raise ProcessGuardError(f"metadata path is not a file: {path}")
            path.chmod(0o600)


def _read_pid(path: Path) -> Optional[int]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProcessGuardError(f"cannot read PID metadata: {path}") from exc
    if not value.isascii() or not value.isdigit() or int(value) <= 1:
        raise ProcessGuardError(f"refusing malformed PID metadata: {path}")
    return int(value)


def _read_identity_record(path: Path) -> Optional[Mapping[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ProcessGuardError(f"refusing malformed identity metadata: {path}") from exc
    if not isinstance(value, dict):
        raise ProcessGuardError(f"refusing malformed identity metadata: {path}")
    return value


def _record_identity(record: Mapping[str, Any]) -> ProcessIdentity:
    identity = record.get("identity")
    if record.get("version") != RECORD_VERSION or not isinstance(identity, Mapping):
        raise ProcessGuardError("unsupported or malformed process identity record")
    try:
        parsed = ProcessIdentity.from_dict(identity)
    except (KeyError, TypeError, ValueError) as exc:
        raise ProcessGuardError("malformed process identity record") from exc
    if parsed.pid <= 1 or not parsed.argv or not parsed.user:
        raise ProcessGuardError("malformed process identity record")
    return parsed


def _clear_metadata(spec: ServiceSpec) -> None:
    for path in (spec.pid_file, spec.identity_file):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class ProcessGuard:
    def __init__(
        self,
        spec: ServiceSpec | NativeServiceSpec,
        *,
        inspector: Optional[ProcessInspector] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.spec = spec
        self.inspector = inspector or PsutilProcessInspector()
        self.clock = clock
        self.sleep = sleep

    def _write_identity(self, identity: ProcessIdentity) -> None:
        if not self.spec.matches_service(identity):
            raise ProcessGuardError(
                "candidate PID is not the configured service; no metadata was written"
            )
        document = {
            "version": RECORD_VERSION,
            "identity": identity.to_dict(),
            "service": self.spec.service_document(),
        }
        # Identity first means a crash can only leave an explicit, fail-closed
        # orphan record. Both files are individually atomic and private.
        _atomic_write(
            self.spec.identity_file,
            json.dumps(document, indent=2, sort_keys=True) + "\n",
        )
        try:
            _atomic_write(self.spec.pid_file, f"{identity.pid}\n")
        except Exception:
            try:
                self.spec.identity_file.unlink()
            except FileNotFoundError:
                pass
            raise

    def record(self, pid: int, timeout: float = RECORD_TIMEOUT_SECONDS) -> ProcessIdentity:
        if pid <= 1:
            raise ProcessGuardError("candidate PID must be greater than 1")
        _prepare_metadata(self.spec)
        if self.spec.pid_file.exists() or self.spec.identity_file.exists():
            raise ProcessGuardError("process metadata already exists; refusing to overwrite it")
        deadline = self.clock() + max(0.0, timeout)
        while True:
            identity = self.inspector.inspect(pid)
            if identity is not None and self.spec.matches_service(identity):
                self._write_identity(identity)
                return identity
            if self.clock() >= deadline:
                reason = "exited" if identity is None else "never matched the configured service"
                raise ProcessGuardError(
                    f"candidate PID {pid} {reason} before identity capture"
                )
            self.sleep(POLL_INTERVAL_SECONDS)

    def locate(self, *, adopt: bool = False) -> Optional[ProcessIdentity]:
        _prepare_metadata(self.spec)
        pid = _read_pid(self.spec.pid_file)
        record = _read_identity_record(self.spec.identity_file)
        if pid is None:
            if record is not None:
                raise ProcessGuardError(
                    "identity metadata exists without PID; no process was managed"
                )
            return None

        observed = self.inspector.inspect(pid)
        if observed is None:
            _clear_metadata(self.spec)
            return None
        if record is None:
            if not adopt:
                raise ProcessGuardError(
                    "PID metadata exists without identity; no process was managed"
                )
            if not self.spec.matches_service(observed):
                raise ProcessGuardError(
                    "PID-only process does not match this service; no signal was sent"
                )
            self._write_adopted_identity(observed)
            return observed

        expected = _record_identity(record)
        if record.get("service") != self.spec.service_document():
            raise ProcessGuardError("service metadata does not match; no signal was sent")
        if pid != expected.pid:
            raise ProcessGuardError("PID and identity metadata disagree; no signal was sent")
        if not self.spec.matches_service(observed):
            raise ProcessGuardError("recorded PID is not this service; no signal was sent")
        if not can_signal(expected, observed):
            raise ProcessGuardError("recorded process identity changed; no signal was sent")
        return observed

    def _write_adopted_identity(self, identity: ProcessIdentity) -> None:
        document = {
            "version": RECORD_VERSION,
            "identity": identity.to_dict(),
            "service": self.spec.service_document(),
        }
        _atomic_write(
            self.spec.identity_file,
            json.dumps(document, indent=2, sort_keys=True) + "\n",
        )
        self.spec.pid_file.chmod(0o600)

    def _wait_until_identity_gone(
        self, identity: ProcessIdentity, timeout: float
    ) -> bool:
        deadline = self.clock() + max(0.0, timeout)
        while True:
            try:
                observed = self.inspector.inspect(identity.pid)
            except RuntimeError as exc:
                # Some platforms transiently deny inspection while a process is
                # crossing into a zombie/exited state after SIGTERM. Uncertainty
                # is never interpreted as exit and never authorizes SIGKILL: retry
                # only within the existing bound, then fail closed.
                if self.clock() >= deadline:
                    raise ProcessGuardError(
                        f"cannot verify whether service PID {identity.pid} stopped"
                    ) from exc
                self.sleep(POLL_INTERVAL_SECONDS)
                continue
            if observed is None or not can_signal(identity, observed):
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(POLL_INTERVAL_SECONDS)

    def terminate_identity(
        self,
        identity: ProcessIdentity,
        *,
        term_timeout: float = TERM_TIMEOUT_SECONDS,
        kill_timeout: float = KILL_TIMEOUT_SECONDS,
    ) -> None:
        observed = self.inspector.inspect(identity.pid)
        if observed is None or not can_signal(identity, observed):
            return
        if not self.spec.matches_service(observed):
            raise ProcessGuardError("candidate is not this service; no signal was sent")
        if not self.inspector.signal(identity, signal.SIGTERM):
            after = self.inspector.inspect(identity.pid)
            if after is not None and can_signal(identity, after):
                raise ProcessGuardError("SIGTERM was refused for the matching service")
            return
        if self._wait_until_identity_gone(identity, term_timeout):
            return

        # Re-inspection inside inspector.signal prevents KILL if the PID was
        # reused between the bounded wait and this call.
        if not self.inspector.signal(identity, signal.SIGKILL):
            after = self.inspector.inspect(identity.pid)
            if after is not None and can_signal(identity, after):
                raise ProcessGuardError("SIGKILL was refused for the matching service")
            return
        if not self._wait_until_identity_gone(identity, kill_timeout):
            raise ProcessGuardError(f"service PID {identity.pid} did not stop")

    def stop(self) -> Optional[ProcessIdentity]:
        identity = self.locate(adopt=False)
        if identity is None:
            return None
        self.terminate_identity(identity)
        _clear_metadata(self.spec)
        return identity

    def owns_listening_tcp_port(self, identity: ProcessIdentity, port: int) -> bool:
        """Return true only while this exact process owns a TCP LISTEN socket."""
        if not 1 <= port <= 65535:
            raise ProcessGuardError("listener port must be between 1 and 65535")
        before = self.inspector.inspect(identity.pid)
        if (
            before is None
            or not can_signal(identity, before)
            or not self.spec.matches_service(before)
        ):
            return False
        try:
            connections = psutil.Process(identity.pid).net_connections(kind="inet")
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False
        except (psutil.AccessDenied, OSError) as exc:
            raise ProcessGuardError(
                f"cannot verify listener ownership for PID {identity.pid}"
            ) from exc
        listening = any(
            connection.status == psutil.CONN_LISTEN
            and getattr(connection.laddr, "port", None) == port
            for connection in connections
        )
        after = self.inspector.inspect(identity.pid)
        return bool(
            listening
            and after is not None
            and can_signal(identity, after)
            and self.spec.matches_service(after)
        )

    def terminate_candidate(self, pid: int) -> Optional[ProcessIdentity]:
        """Rollback only a child PID created by the current start attempt."""
        if pid <= 1:
            raise ProcessGuardError("candidate PID must be greater than 1")
        identity = self.inspector.inspect(pid)
        if identity is None:
            return None
        if not self.spec.matches_service(identity):
            raise ProcessGuardError(
                "rollback candidate is not this service; no signal was sent"
            )
        self.terminate_identity(identity)
        return identity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("status", "record", "stop", "terminate-candidate", "owns-port"),
    )
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--python", dest="python_bin", type=Path)
    parser.add_argument("--module")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--argv-json")
    parser.add_argument("--from-record", action="store_true")
    parser.add_argument("--listen-port", type=int)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--adopt", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        native_mode = any(
            value is not None for value in (args.executable, args.argv_json)
        ) or args.from_record
        if native_mode:
            if args.executable is None:
                raise ProcessGuardError("native service requires --executable")
            if any(
                value is not None
                for value in (args.python_bin, args.module, args.host, args.port)
            ):
                raise ProcessGuardError(
                    "native service options cannot be mixed with Python service options"
                )
            if args.from_record:
                if args.argv_json is not None or args.adopt:
                    raise ProcessGuardError(
                        "--from-record cannot be combined with --argv-json or --adopt"
                    )
                if args.action not in {"status", "stop"}:
                    raise ProcessGuardError(
                        "--from-record is valid only for status or stop"
                    )
                spec: ServiceSpec | NativeServiceSpec = NativeServiceSpec.from_record(
                    pid_file=args.pid_file,
                    identity_file=args.identity_file,
                    cwd=args.cwd,
                    executable=args.executable,
                )
            else:
                if args.argv_json is None:
                    raise ProcessGuardError(
                        "native service requires --argv-json or --from-record"
                    )
                try:
                    parsed_argv = json.loads(args.argv_json)
                except json.JSONDecodeError as exc:
                    raise ProcessGuardError("native --argv-json is malformed") from exc
                if not isinstance(parsed_argv, list) or any(
                    not isinstance(argument, str) for argument in parsed_argv
                ):
                    raise ProcessGuardError(
                        "native --argv-json must encode a string list"
                    )
                spec = NativeServiceSpec.create(
                    pid_file=args.pid_file,
                    identity_file=args.identity_file,
                    cwd=args.cwd,
                    executable=args.executable,
                    argv=parsed_argv,
                )
        else:
            if args.python_bin is None or args.module is None or args.port is None:
                raise ProcessGuardError(
                    "Python service requires --python, --module, and --port"
                )
            host = args.host
            if host is None:
                # Stop/status normally need only PORT, as before. Reuse the host
                # pinned in private metadata; PID-only legacy adoption falls back
                # to the historical 0.0.0.0 default.
                record = _read_identity_record(args.identity_file)
                service = record.get("service") if isinstance(record, Mapping) else None
                recorded_host = service.get("host") if isinstance(service, Mapping) else None
                host = recorded_host if isinstance(recorded_host, str) else "0.0.0.0"
            spec = ServiceSpec.create(
                pid_file=args.pid_file,
                identity_file=args.identity_file,
                cwd=args.cwd,
                python_bin=args.python_bin,
                module=args.module,
                host=host,
                port=args.port,
            )
        guard = ProcessGuard(spec)
        if args.action == "owns-port":
            if args.pid is not None or args.adopt:
                raise ProcessGuardError("owns-port does not accept --pid or --adopt")
            listen_port = args.listen_port
            if listen_port is None and isinstance(spec, ServiceSpec):
                listen_port = spec.port
            if listen_port is None:
                raise ProcessGuardError("native owns-port requires --listen-port")
            identity = guard.locate(adopt=False)
            if identity is None:
                return EXIT_STOPPED
            if not guard.owns_listening_tcp_port(identity, listen_port):
                return EXIT_NOT_LISTENING
            print(identity.pid)
            return 0
        if args.action == "status":
            if args.pid is not None:
                raise ProcessGuardError("--pid is not valid for status")
            identity = guard.locate(adopt=args.adopt)
            if identity is None:
                return EXIT_STOPPED
            print(identity.pid)
            return 0
        if args.action == "record":
            if args.pid is None or args.adopt:
                raise ProcessGuardError("record requires --pid")
            identity = guard.record(args.pid)
            print(identity.pid)
            return 0
        if args.action == "stop":
            if args.pid is not None or args.adopt:
                raise ProcessGuardError("stop does not accept --pid or --adopt")
            identity = guard.stop()
            if identity is None:
                return EXIT_STOPPED
            print(identity.pid)
            return 0
        if args.pid is None or args.adopt:
            raise ProcessGuardError("terminate-candidate requires --pid")
        identity = guard.terminate_candidate(args.pid)
        if identity is None:
            return EXIT_STOPPED
        print(identity.pid)
        return 0
    except (ProcessGuardError, RuntimeError, OSError, ValueError) as exc:
        print(f"[ERROR] process guard: {exc}", file=sys.stderr)
        return EXIT_UNSAFE


__all__ = [
    "EXIT_NOT_LISTENING",
    "EXIT_STOPPED",
    "EXIT_UNSAFE",
    "NativeServiceSpec",
    "ProcessGuard",
    "ProcessGuardError",
    "ServiceSpec",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
