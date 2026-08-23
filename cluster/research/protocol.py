"""Infrastructure-neutral contracts used by the durable campaign runner."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


RUN_NONTERMINAL_STATES = frozenset({"starting", "queued", "running", "cancelling"})
RUN_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


class CampaignRunBackend(Protocol):
    """Start, inspect, and cancel exactly one durable benchmark attempt."""

    def start(
        self,
        cell: Mapping[str, Any],
        attempt: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def inspect(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def cancel(self, attempt: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CampaignGate(Protocol):
    """Return ``eligible``, blocking issues, and non-blocking warnings."""

    def __call__(self, cell: Mapping[str, Any]) -> Mapping[str, Any]: ...


__all__ = [
    "CampaignGate",
    "CampaignRunBackend",
    "RUN_NONTERMINAL_STATES",
    "RUN_TERMINAL_STATES",
]
