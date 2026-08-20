"""Controller-only domain configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .errors import DomainValidationError


class ControllerPlatform(str, Enum):
    MACOS = "macos"

    def __str__(self) -> str:
        return self.value


def _absolute_path(value: Path, label: str) -> Path:
    path = Path(value)
    raw = str(path)
    broad = {"/home", "/opt", "/srv", "/tmp", "/Users", "/var"}
    is_user_home = len(path.parts) < 4 and len(path.parts) >= 2 and path.parts[1] in {"home", "Users"}
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path == Path(path.anchor)
        or raw in broad
        or is_user_home
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        raise DomainValidationError(f"{label} must be a dedicated absolute path without traversal")
    return path


@dataclass(frozen=True)
class ControllerConfig:
    """Control-plane identity; deliberately not an inventory node."""

    host: str
    runtime_dir: Path
    results_dir: Path
    platform: ControllerPlatform = ControllerPlatform.MACOS

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            raise DomainValidationError("Controller host cannot be empty")
        try:
            platform = ControllerPlatform(self.platform)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError(f"Unsupported controller platform: {self.platform}") from exc
        object.__setattr__(self, "host", self.host.strip())
        object.__setattr__(self, "platform", platform)
        object.__setattr__(self, "runtime_dir", _absolute_path(self.runtime_dir, "runtime_dir"))
        object.__setattr__(self, "results_dir", _absolute_path(self.results_dir, "results_dir"))
