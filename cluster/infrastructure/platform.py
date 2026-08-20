"""Explicit controller/worker platform capability dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple


class HostPlatform(str, Enum):
    MACOS = "macos"
    LINUX = "linux"


class WorkerPlatform(str, Enum):
    JETSON = "jetson"
    RASPBERRY_PI = "raspberry-pi"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class PlatformCapabilities:
    host: HostPlatform
    worker: WorkerPlatform | None = None

    @property
    def allows_linux_worker_setup(self) -> bool:
        return self.host is HostPlatform.LINUX and self.worker in {
            WorkerPlatform.JETSON,
            WorkerPlatform.RASPBERRY_PI,
        }

    @property
    def allowed_package_managers(self) -> Tuple[str, ...]:
        return ("apt-get",) if self.allows_linux_worker_setup else ()

    @property
    def supports_jtop(self) -> bool:
        return self.worker is WorkerPlatform.JETSON and self.host is HostPlatform.LINUX


def controller_capabilities(system_name: str) -> PlatformCapabilities:
    return PlatformCapabilities(
        host=HostPlatform.MACOS if system_name.lower() == "darwin" else HostPlatform.LINUX
    )


def worker_capabilities(system_name: str, worker_kind: str) -> PlatformCapabilities:
    normalized = worker_kind.strip().lower()
    worker = WorkerPlatform(normalized) if normalized in {"jetson", "raspberry-pi"} else WorkerPlatform.UNSUPPORTED
    return PlatformCapabilities(
        host=HostPlatform.LINUX if system_name.lower() == "linux" else HostPlatform.MACOS,
        worker=worker,
    )
