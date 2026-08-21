"""Inference-worker domain types with no control-plane role semantics."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Iterator, Optional, Tuple

from .errors import DomainValidationError
from .identifiers import validate_node_id


class WorkerPlatform(str, Enum):
    AUTO = "auto"
    JETSON = "jetson"
    RASPBERRY_PI = "raspberry-pi"

    def __str__(self) -> str:
        return self.value


_USER_PATTERN = re.compile(r"^[a-z_][A-Za-z0-9_-]*$")
_PROJECT_PATTERN = re.compile(r"/(?:home|opt|srv)/[A-Za-z0-9._/-]+")
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
)


def validate_worker_host(host: str) -> str:
    if not isinstance(host, str) or not host.strip():
        raise DomainValidationError("Worker host cannot be empty")
    value = host.strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise DomainValidationError("Worker host must be a private IPv4 address") from exc
    if address.version != 4 or not any(address in network for network in _PRIVATE_NETWORKS):
        raise DomainValidationError("Worker IP address must belong to a private or loopback IPv4 network")
    return str(address)


def validate_worker_project_dir(project_dir: str, user: str = "") -> str:
    if (
        not isinstance(project_dir, str)
        or not _PROJECT_PATTERN.fullmatch(project_dir)
        or ".." in PurePosixPath(project_dir).parts
    ):
        raise DomainValidationError("project_dir must be a safe path below /home, /opt or /srv")
    normalized = str(PurePosixPath(project_dir))
    broad = {"/", "/home", "/opt", "/srv"}
    if user:
        broad.add(f"/home/{user}")
    parts = PurePosixPath(normalized).parts
    if normalized in broad or (len(parts) >= 2 and parts[1] == "home" and len(parts) < 4):
        raise DomainValidationError(f"project_dir is too broad for code synchronization: {project_dir}")
    return normalized


@dataclass(frozen=True)
class WorkerNode:
    name: str
    host: str
    user: str
    ssh_port: int
    api_port: int
    project_dir: str
    enabled: bool = True
    platform: WorkerPlatform = WorkerPlatform.AUTO
    identity_file: Optional[str] = None

    def __post_init__(self) -> None:
        name = validate_node_id(self.name)
        host = validate_worker_host(self.host)
        if not isinstance(self.user, str) or not _USER_PATTERN.fullmatch(self.user):
            raise DomainValidationError("Worker user has unsupported characters")
        if not isinstance(self.ssh_port, int) or isinstance(self.ssh_port, bool) or not 1 <= self.ssh_port <= 65535:
            raise DomainValidationError("ssh_port must be between 1 and 65535")
        if not isinstance(self.api_port, int) or isinstance(self.api_port, bool) or not 1 <= self.api_port <= 65535:
            raise DomainValidationError("api_port must be between 1 and 65535")
        if not isinstance(self.enabled, bool):
            raise DomainValidationError("enabled must be a boolean")
        project_dir = validate_worker_project_dir(self.project_dir, self.user)
        try:
            platform = WorkerPlatform(self.platform)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"Unsupported worker platform: {self.platform}") from exc
        identity_file = self.identity_file
        if identity_file == "":
            identity_file = None
        if identity_file is not None and (
            not isinstance(identity_file, str)
            or not identity_file.strip()
            or "\x00" in identity_file
            or not PurePosixPath(identity_file).is_absolute()
        ):
            raise DomainValidationError("identity_file must be an absolute path when provided")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "project_dir", project_dir)
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "identity_file", identity_file)

    @property
    def api_url(self) -> str:
        return f"http://{self.host}:{self.api_port}"

    @property
    def ssh_target(self) -> str:
        return f"{self.user}@{self.host}"


@dataclass(frozen=True)
class WorkerInventory:
    workers: Tuple[WorkerNode, ...]

    def __post_init__(self) -> None:
        workers = tuple(self.workers)
        if any(not isinstance(worker, WorkerNode) for worker in workers):
            raise DomainValidationError("Worker inventory can contain only WorkerNode values")
        names = [worker.name for worker in workers]
        if len(names) != len(set(names)):
            raise DomainValidationError("Worker names must be unique")
        endpoints = [(worker.host, worker.ssh_port) for worker in workers]
        if len(endpoints) != len(set(endpoints)):
            raise DomainValidationError("Each physical host and SSH port can be registered only once")
        object.__setattr__(self, "workers", workers)

    def __iter__(self) -> Iterator[WorkerNode]:
        return iter(self.workers)

    def __len__(self) -> int:
        return len(self.workers)

    def enabled_workers(self) -> Tuple[WorkerNode, ...]:
        return tuple(worker for worker in self.workers if worker.enabled)
