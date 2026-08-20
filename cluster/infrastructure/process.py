"""Cross-platform process identity inspection without ``/proc`` assumptions."""
from __future__ import annotations

import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Tuple

import psutil


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str
    cwd: str
    argv: Tuple[str, ...]
    started_at: float
    user: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "executable": self.executable,
            "cwd": self.cwd,
            "argv": list(self.argv),
            "started_at": self.started_at,
            "user": self.user,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProcessIdentity":
        return cls(
            pid=int(value["pid"]),
            executable=str(value["executable"]),
            cwd=str(value["cwd"]),
            argv=tuple(str(item) for item in value["argv"]),
            started_at=float(value["started_at"]),
            user=str(value["user"]),
        )


class ProcessInspector(Protocol):
    def inspect(self, pid: int) -> Optional[ProcessIdentity]: ...

    def signal(self, expected: ProcessIdentity, signum: int) -> bool: ...


def can_signal(expected: ProcessIdentity, observed: ProcessIdentity) -> bool:
    """Require every available identity attribute to match before signalling."""
    return (
        expected.pid == observed.pid
        and expected.executable == observed.executable
        and expected.cwd == observed.cwd
        and expected.argv == observed.argv
        and expected.started_at == observed.started_at
        and expected.user == observed.user
    )


class PsutilProcessInspector:
    """Inspect and signal a process only while its full identity still matches."""

    @staticmethod
    def _identity(process: psutil.Process) -> ProcessIdentity:
        return ProcessIdentity(
            pid=process.pid,
            executable=str(Path(process.exe()).resolve()),
            cwd=str(Path(process.cwd()).resolve()),
            argv=tuple(process.cmdline()),
            started_at=process.create_time(),
            user=process.username(),
        )

    def inspect(self, pid: int) -> Optional[ProcessIdentity]:
        try:
            return self._identity(psutil.Process(pid))
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except (psutil.AccessDenied, OSError) as exc:
            raise RuntimeError(f"Cannot inspect process {pid}: {exc}") from exc

    def signal(self, expected: ProcessIdentity, signum: int) -> bool:
        try:
            process = psutil.Process(expected.pid)
            observed = self._identity(process)
            if not can_signal(expected, observed):
                return False
            process.send_signal(signum)
            return True
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return False
        except (psutil.AccessDenied, OSError) as exc:
            raise RuntimeError(f"Cannot signal process {expected.pid}: {exc}") from exc


TERMINATE_SIGNAL = signal.SIGTERM
KILL_SIGNAL = signal.SIGKILL


__all__ = [
    "KILL_SIGNAL",
    "ProcessIdentity",
    "ProcessInspector",
    "PsutilProcessInspector",
    "TERMINATE_SIGNAL",
    "can_signal",
]
