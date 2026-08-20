"""Typed, transport-neutral event envelope for Controller operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional


class EventChannel(str, Enum):
    NODE_OPS = "node_ops"
    EXPERIMENT = "experiment"
    SYSTEM = "system"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ClusterEvent:
    """Typed internal event whose wire form preserves legacy flat fields."""

    channel: EventChannel
    type: str
    at: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    node: Optional[str] = None
    run_id: Optional[str] = None
    suite_id: Optional[str] = None
    experiment_id: Optional[str] = None
    model_id: Optional[str] = None
    scenario_id: Optional[str] = None
    message: Optional[str] = None
    evidence: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls, channel: EventChannel | str, event_type: str, at: str, **payload: Any
    ) -> "ClusterEvent":
        resolved_channel = EventChannel(channel)
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event type cannot be empty")
        return cls(
            channel=resolved_channel,
            type=event_type,
            at=at,
            payload=dict(payload),
            node=payload.get("node") if isinstance(payload.get("node"), str) else None,
            run_id=payload.get("run_id") if isinstance(payload.get("run_id"), str) else None,
            suite_id=payload.get("suite_id") if isinstance(payload.get("suite_id"), str) else None,
            experiment_id=payload.get("experiment_id") if isinstance(payload.get("experiment_id"), str) else None,
            model_id=payload.get("model_id") if isinstance(payload.get("model_id"), str) else None,
            scenario_id=payload.get("scenario_id") if isinstance(payload.get("scenario_id"), str) else None,
            message=payload.get("message") if isinstance(payload.get("message"), str) else None,
            evidence=(
                dict(payload["evidence"])
                if isinstance(payload.get("evidence"), Mapping)
                else {}
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        # Flat payload maintains existing SSE consumers; channel is additive.
        return {
            **dict(self.payload),
            "type": self.type,
            "at": self.at,
            "channel": self.channel.value,
        }


__all__ = ["ClusterEvent", "EventChannel"]
