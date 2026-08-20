"""Cross-platform process identity comparison without /proc assumptions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str
    cwd: str
    argv: Tuple[str, ...]
    started_at: float
    user: str


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
