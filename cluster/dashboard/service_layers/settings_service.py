"""Dashboard and Worker authentication settings orchestration."""

from __future__ import annotations

from typing import Any, Callable

from cluster.dashboard.schemas import ActionPayload
from cluster.dashboard.service_layers.errors import DashboardServiceError
from cluster.domain.events import EventChannel


class SettingsService:
    """Update security settings atomically without depending on HTTP types."""

    def __init__(
        self,
        *,
        lock: Any,
        read_settings: Callable[[], dict[str, Any]],
        write_settings: Callable[[dict[str, Any]], None],
        read_enabled_node_names: Callable[[], list[str]],
        start_action: Callable[[ActionPayload], dict[str, Any]],
        publish_event: Callable[..., None],
    ) -> None:
        self._lock = lock
        self._read = read_settings
        self._write = write_settings
        self._read_enabled_node_names = read_enabled_node_names
        self._start_action = start_action
        self._publish_event = publish_event

    def get(self) -> dict[str, Any]:
        return {"settings": self._read()}

    def update(
        self, payload: Any, *, supplied_token: str, token_is_valid: Callable[[str], bool]
    ) -> dict[str, Any]:
        action = None
        with self._lock:
            previous = self._read()
            if previous["dashboard_token_auth"] and not token_is_valid(supplied_token):
                raise DashboardServiceError(401, "Dashboard access token is missing or invalid")
            updated = dict(previous)
            if payload.worker_api_auth is not None:
                updated["worker_api_auth"] = payload.worker_api_auth
            if payload.dashboard_token_auth is not None:
                updated["dashboard_token_auth"] = payload.dashboard_token_auth
            if not previous["dashboard_token_auth"] and updated["dashboard_token_auth"]:
                if not token_is_valid(supplied_token):
                    raise DashboardServiceError(
                        403,
                        "Enabling dashboard token auth requires the current dashboard token",
                    )
            self._write(updated)
            if previous["worker_api_auth"] != updated["worker_api_auth"]:
                try:
                    action = self._start_action(
                        ActionPayload(
                            action="restart",
                            node_names=self._read_enabled_node_names(),
                            options={},
                        )
                    )
                except ValueError as exc:
                    self._write(previous)
                    raise DashboardServiceError(409, str(exc)) from exc
        self._publish_event(
            "settings_changed",
            channel=EventChannel.SYSTEM,
            settings=updated,
            action=action,
        )
        return {"ok": True, "settings": updated, "action": action}


__all__ = ["SettingsService"]
